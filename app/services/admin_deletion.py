from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
from typing import Any

from firebase_admin import auth as firebase_auth
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import init_firebase
from app.db.session import SessionLocal
from app.models.admin import AdminAuditLog, UserDeletionJob
from app.models.burned_calories import BurnedCalories
from app.models.daily_summary import DailySummary
from app.models.food_memory import UserFoodMemory
from app.models.inference import AIInferenceLog
from app.models.insight import DetectedPattern, InsightSnapshot, UserInsightVersion
from app.models.meal import Meal, MealItem, MealRevision
from app.models.meal_analysis import MealAnalysisJob
from app.models.promo_code import PromoCodeAttempt, PromoCodeRedemption
from app.models.revenuecat_event import RevenueCatEvent, RevenueCatSubscriberSnapshot
from app.models.user import User
from app.services.privacy import pseudonymize
from app.services.revenuecat_service import RevenueCatClient
from app.services.storage import delete_storage_object, storage_key_from_url

logger = logging.getLogger("app.services.admin_deletion")

STEP_LABELS = {
    "database": "Database records",
    "storage": "Storage objects",
    "revenuecat": "RevenueCat customer",
    "firebase": "Firebase account",
    "verification": "Final verification",
}
TERMINAL_STEP_STATUSES = {"completed", "already_absent", "skipped"}


def initial_steps() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": label, "status": "waiting", "error_code": None, "message": None}
        for key, label in STEP_LABELS.items()
    ]


def _rc_ids(user: User) -> list[str]:
    return list(dict.fromkeys(filter(None, [user.revenuecat_app_user_id, user.firebase_uid])))


async def _count(db: AsyncSession, model: type, *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int((await db.execute(statement)).scalar_one())


async def user_inventory(db: AsyncSession, user: User) -> dict[str, int]:
    meal_ids = select(Meal.id).where(Meal.user_id == user.id)
    rc_ids = _rc_ids(user)
    return {
        "meals": await _count(db, Meal, Meal.user_id == user.id),
        "meal_images": await _count(db, Meal, Meal.user_id == user.id, Meal.image_url.is_not(None)),
        "ingredients": await _count(db, MealItem, MealItem.meal_id.in_(meal_ids)),
        "correction_events": await _count(db, MealRevision, MealRevision.user_id == user.id),
        "ai_analyses": await _count(db, AIInferenceLog, AIInferenceLog.user_id == user.id),
        "analysis_jobs": await _count(db, MealAnalysisJob, MealAnalysisJob.user_id == user.id),
        "insight_versions": await _count(db, UserInsightVersion, UserInsightVersion.user_id == user.id),
        "insights": await _count(db, DetectedPattern, DetectedPattern.user_id == user.id),
        "reports": await _count(db, InsightSnapshot, InsightSnapshot.user_id == user.id),
        "water_records": await _count(db, DailySummary, DailySummary.user_id == user.id),
        "activity_records": await _count(db, BurnedCalories, BurnedCalories.user_id == user.id),
        "profile_onboarding_records": 1,
        "daily_summaries": await _count(db, DailySummary, DailySummary.user_id == user.id),
        "food_memories": await _count(db, UserFoodMemory, UserFoodMemory.user_id == user.id),
        "promo_redemptions": await _count(db, PromoCodeRedemption, PromoCodeRedemption.user_id == user.id),
        "promo_attempts": await _count(db, PromoCodeAttempt, PromoCodeAttempt.user_id == user.id),
        "revenuecat_events": await _count(db, RevenueCatEvent, RevenueCatEvent.app_user_id.in_(rc_ids)),
        "revenuecat_snapshots": await _count(
            db,
            RevenueCatSubscriberSnapshot,
            or_(
                RevenueCatSubscriberSnapshot.user_id == user.id,
                RevenueCatSubscriberSnapshot.app_user_id.in_(rc_ids),
            ),
        ),
    }


async def user_storage_objects(db: AsyncSession, user_id: int) -> list[str]:
    values: list[str] = []
    meals = await db.execute(select(Meal.image_url, Meal.audio_url).where(Meal.user_id == user_id))
    for image_url, audio_url in meals.all():
        values.extend(value for value in (image_url, audio_url) if value)
    jobs = await db.scalars(select(MealAnalysisJob.image_url).where(MealAnalysisJob.user_id == user_id))
    values.extend(jobs.all())
    revisions = await db.execute(
        select(MealRevision.user_input, MealRevision.refinement_type).where(MealRevision.user_id == user_id)
    )
    values.extend(value for value, kind in revisions.all() if kind == "voice" and value)
    keys = [storage_key_from_url(value) for value in values]
    return sorted({key for key in keys if key})


async def deletion_status_for(db: AsyncSession, user_id: int) -> str | None:
    return await db.scalar(
        select(UserDeletionJob.status)
        .where(UserDeletionJob.target_user_id == user_id)
        .order_by(UserDeletionJob.created_at.desc())
        .limit(1)
    )


def preview_version(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def build_preview_snapshot(db: AsyncSession, user: User) -> dict[str, Any]:
    inventory = await user_inventory(db, user)
    storage_objects = await user_storage_objects(db, user.id)
    deletion_status = await deletion_status_for(db, user.id)
    subscription = {
        "revenuecat_customer_found": None,
        "entitlement_active": user.is_premium,
        "entitlement": user.premium_entitlement,
        "product_id": user.premium_product_id,
        "store": user.premium_store,
        "expiration_date": user.premium_expires_at.isoformat() if user.premium_expires_at else None,
        "management_url": None,
    }
    rc_ids = _rc_ids(user)
    if rc_ids:
        subscription["revenuecat_customer_found"] = bool(
            await _count(db, RevenueCatSubscriberSnapshot, RevenueCatSubscriberSnapshot.app_user_id.in_(rc_ids))
            or await _count(db, RevenueCatEvent, RevenueCatEvent.app_user_id.in_(rc_ids))
            or user.revenuecat_app_user_id
        )
    return {
        "target": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "firebase_uid": user.firebase_uid,
            "revenuecat_app_user_id": user.revenuecat_app_user_id,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
            "onboarding_status": user.onboarding_status,
            "is_premium": user.is_premium,
            "deletion_status": deletion_status,
            "access_status": user.access_status,
            "access_restriction_reason": user.access_restriction_reason,
            "access_restriction_legal_basis": user.access_restriction_legal_basis,
            "access_restriction_expires_at": (
                user.access_restriction_expires_at.isoformat() if user.access_restriction_expires_at else None
            ),
        },
        "inventory": inventory,
        "storage_objects": storage_objects,
        "subscription": subscription,
        "warnings": [
            "RevenueCat customer data and promotional grants will be deleted. Any App Store or Google Play "
            "subscription remains governed by the store and may continue billing until canceled there."
        ],
        "deletion_allowed": deletion_status not in {"pending", "running", "partially_failed"},
    }


def _update_step(
    job: UserDeletionJob,
    key: str,
    status: str,
    *,
    error_code: str | None = None,
    message: str | None = None,
) -> None:
    steps = [dict(step) for step in job.steps_json]
    for step in steps:
        if step["key"] == key:
            step.update(status=status, error_code=error_code, message=message)
            break
    job.steps_json = steps
    job.current_step = key


async def _delete_database_records(db: AsyncSession, job: UserDeletionJob) -> bool:
    user_id = job.target_user_id
    meal_ids = select(Meal.id).where(Meal.user_id == user_id)
    rc_ids = list(
        dict.fromkeys(filter(None, [job.target_revenuecat_app_user_id, job.target_firebase_uid]))
    )
    statements = [
        delete(RevenueCatEvent).where(RevenueCatEvent.app_user_id.in_(rc_ids)),
        delete(RevenueCatSubscriberSnapshot).where(
            or_(
                RevenueCatSubscriberSnapshot.user_id == user_id,
                RevenueCatSubscriberSnapshot.app_user_id.in_(rc_ids),
            )
        ),
        delete(AIInferenceLog).where(AIInferenceLog.user_id == user_id),
        delete(InsightSnapshot).where(InsightSnapshot.user_id == user_id),
        delete(DetectedPattern).where(DetectedPattern.user_id == user_id),
        delete(UserInsightVersion).where(UserInsightVersion.user_id == user_id),
        delete(MealAnalysisJob).where(MealAnalysisJob.user_id == user_id),
        delete(MealRevision).where(MealRevision.user_id == user_id),
        delete(MealItem).where(MealItem.meal_id.in_(meal_ids)),
        delete(Meal).where(Meal.user_id == user_id),
        delete(BurnedCalories).where(BurnedCalories.user_id == user_id),
        delete(DailySummary).where(DailySummary.user_id == user_id),
        delete(UserFoodMemory).where(UserFoodMemory.user_id == user_id),
        delete(PromoCodeRedemption).where(PromoCodeRedemption.user_id == user_id),
        delete(PromoCodeAttempt).where(PromoCodeAttempt.user_id == user_id),
        delete(User).where(User.id == user_id),
    ]
    existed = await db.get(User, user_id) is not None
    for statement in statements:
        await db.execute(statement)
    return existed


async def _delete_storage(job: UserDeletionJob) -> bool:
    found = False
    for storage_key in job.preview_snapshot_json.get("storage_objects", []):
        found = await delete_storage_object(storage_key) or found
    return found


async def _delete_revenuecat(job: UserDeletionJob) -> bool:
    app_user_id = job.target_revenuecat_app_user_id or job.target_firebase_uid
    return await RevenueCatClient().delete_customer(app_user_id)


async def _delete_firebase(job: UserDeletionJob) -> bool:
    if not settings.FIREBASE_CREDENTIALS:
        raise RuntimeError("Firebase Admin credentials are not configured.")
    init_firebase()
    try:
        record = await asyncio.to_thread(firebase_auth.get_user, job.target_firebase_uid)
    except firebase_auth.UserNotFoundError:
        return False
    if record.email and record.email.casefold() != job.target_email.casefold():
        raise RuntimeError("Firebase identity mismatch; deletion stopped.")
    await asyncio.to_thread(firebase_auth.delete_user, job.target_firebase_uid)
    return True


async def _verify(db: AsyncSession, job: UserDeletionJob) -> bool:
    if await db.get(User, job.target_user_id) is not None:
        raise RuntimeError("Local user still exists after database deletion.")
    return True


async def _erase_completed_job_personal_data(db: AsyncSession, job: UserDeletionJob) -> None:
    """Remove identifiers needed only while the retryable saga is active."""
    await db.execute(
        update(AdminAuditLog)
        .where(AdminAuditLog.target_user_id == job.target_user_id)
        .values(safe_target_identifier=None, admin_email=None)
    )
    job.target_email = "[erased]"
    job.target_firebase_uid = f"erased:{job.id}"
    job.target_revenuecat_app_user_id = None
    job.requested_by_admin_email = None
    job.preview_snapshot_json = {"erased": True}


async def process_deletion_job(job_id: str) -> None:
    """Run or resume failed saga steps from persisted state."""
    async with SessionLocal() as db:
        job = await db.scalar(
            select(UserDeletionJob).where(UserDeletionJob.id == job_id).with_for_update(skip_locked=True)
        )
        if not job or job.status != "pending":
            return
        job.status = "running"
        job.started_at = job.started_at or dt.datetime.now(dt.UTC)
        job.failed_at = None
        await db.commit()

        handlers = {
            "storage": lambda: _delete_storage(job),
            "database": lambda: _delete_database_records(db, job),
            "revenuecat": lambda: _delete_revenuecat(job),
            "firebase": lambda: _delete_firebase(job),
            "verification": lambda: _verify(db, job),
        }
        failures: list[tuple[str, str]] = []
        for key in ("storage", "database", "revenuecat", "firebase", "verification"):
            existing = next(step for step in job.steps_json if step["key"] == key)
            if existing["status"] in TERMINAL_STEP_STATUSES:
                continue
            _update_step(job, key, "running")
            await db.commit()
            try:
                present = await handlers[key]()
                _update_step(job, key, "completed" if present else "already_absent")
                db.add(
                    AdminAuditLog(
                        admin_uid=job.requested_by_admin_uid,
                        admin_email=None,
                        action="deletion_step_completed",
                        target_user_id=job.target_user_id,
                        safe_target_identifier=pseudonymize(job.target_email, namespace="target"),
                        deletion_job_id=job.id,
                        result="success",
                        metadata_json={"step": key, "already_absent": not present},
                    )
                )
            except Exception as exc:
                await db.rollback()
                job = await db.get(UserDeletionJob, job_id)
                assert job is not None
                error_code = f"{key.upper()}_DELETION_FAILED"
                safe_message = f"{STEP_LABELS[key]} could not be completed. Retry this job."
                _update_step(job, key, "failed", error_code=error_code, message=safe_message)
                failures.append((error_code, safe_message))
                db.add(
                    AdminAuditLog(
                        admin_uid=job.requested_by_admin_uid,
                        admin_email=None,
                        action="deletion_step_failed",
                        target_user_id=job.target_user_id,
                        safe_target_identifier=pseudonymize(job.target_email, namespace="target"),
                        deletion_job_id=job.id,
                        result="failed",
                        metadata_json={"step": key, "error_type": type(exc).__name__},
                    )
                )
                logger.exception("admin deletion step failed job_id=%s step=%s", job.id, key)
            await db.commit()

        job = await db.get(UserDeletionJob, job_id)
        assert job is not None
        now = dt.datetime.now(dt.UTC)
        if failures:
            job.status = "partially_failed"
            job.failed_at = now
            job.last_error_code, job.last_error_message = failures[-1]
            result = "partial_failure"
        else:
            job.status = "completed"
            job.completed_at = now
            job.current_step = "verification"
            job.last_error_code = None
            job.last_error_message = None
            result = "success"
            await _erase_completed_job_personal_data(db, job)
        db.add(
            AdminAuditLog(
                admin_uid=job.requested_by_admin_uid,
                admin_email=None,
                action="deletion_job_completed" if not failures else "deletion_job_partially_failed",
                target_user_id=job.target_user_id,
                safe_target_identifier=None if not failures else pseudonymize(job.target_email, namespace="target"),
                deletion_job_id=job.id,
                result=result,
                metadata_json={"retry_count": job.retry_count},
            )
        )
        await db.commit()
