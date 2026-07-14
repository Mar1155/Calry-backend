"""Permanently delete a Calry user by email.

By default this removes the RevenueCat customer, Firebase Auth identity and all
backend-owned data. External deletion happens first so a Firebase identity
cannot immediately recreate the local account after a successful DB deletion.

Usage:
    venv/bin/python scripts/delete_user.py user@example.com
    venv/bin/python scripts/delete_user.py user@example.com --yes
    venv/bin/python scripts/delete_user.py user@example.com --backend-only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.security import init_firebase  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.burned_calories import BurnedCalories  # noqa: E402
from app.models.daily_summary import DailySummary  # noqa: E402
from app.models.food_memory import UserFoodMemory  # noqa: E402
from app.models.inference import AIInferenceLog  # noqa: E402
from app.models.meal import Meal, MealItem, MealRevision  # noqa: E402
from app.models.meal_analysis import MealAnalysisJob  # noqa: E402
from app.models.promo_code import PromoCodeAttempt, PromoCodeRedemption  # noqa: E402
from app.models.revenuecat_event import (  # noqa: E402
    RevenueCatEvent,
    RevenueCatSubscriberSnapshot,
)
from app.models.user import User  # noqa: E402
from app.services.revenuecat_service import RevenueCatClient  # noqa: E402


@dataclass(frozen=True)
class UserDeletionTarget:
    id: int
    email: str
    firebase_uid: str
    revenuecat_app_user_id: str | None
    created_at: object


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if not normalized or "@" not in normalized:
        raise ValueError("Provide a valid email address.")
    return normalized


async def find_user_by_email(db: AsyncSession, email: str) -> User | None:
    normalized = normalize_email(email)
    result = await db.execute(select(User).where(func.lower(User.email) == normalized))
    return result.scalar_one_or_none()


def deletion_target(user: User) -> UserDeletionTarget:
    return UserDeletionTarget(
        id=user.id,
        email=user.email,
        firebase_uid=user.firebase_uid,
        revenuecat_app_user_id=user.revenuecat_app_user_id,
        created_at=user.created_at,
    )


def _revenuecat_ids(target: UserDeletionTarget) -> list[str]:
    return list(dict.fromkeys(filter(None, [target.revenuecat_app_user_id, target.firebase_uid])))


async def _count(db: AsyncSession, model, *conditions) -> int:
    stmt = select(func.count()).select_from(model)
    if conditions:
        stmt = stmt.where(*conditions)
    return int((await db.execute(stmt)).scalar_one())


async def related_data_counts(db: AsyncSession, target: UserDeletionTarget) -> dict[str, int]:
    meal_ids = select(Meal.id).where(Meal.user_id == target.id)
    rc_ids = _revenuecat_ids(target)
    return {
        "meals": await _count(db, Meal, Meal.user_id == target.id),
        "meal_items": await _count(db, MealItem, MealItem.meal_id.in_(meal_ids)),
        "meal_revisions": await _count(db, MealRevision, MealRevision.user_id == target.id),
        "analysis_jobs": await _count(db, MealAnalysisJob, MealAnalysisJob.user_id == target.id),
        "activities": await _count(db, BurnedCalories, BurnedCalories.user_id == target.id),
        "daily_summaries": await _count(db, DailySummary, DailySummary.user_id == target.id),
        "food_memories": await _count(db, UserFoodMemory, UserFoodMemory.user_id == target.id),
        "ai_inference_logs": await _count(db, AIInferenceLog, AIInferenceLog.user_id == target.id),
        "promo_redemptions": await _count(db, PromoCodeRedemption, PromoCodeRedemption.user_id == target.id),
        "promo_attempts": await _count(db, PromoCodeAttempt, PromoCodeAttempt.user_id == target.id),
        "revenuecat_events": await _count(db, RevenueCatEvent, RevenueCatEvent.app_user_id.in_(rc_ids)),
        "revenuecat_snapshots": await _count(
            db,
            RevenueCatSubscriberSnapshot,
            or_(
                RevenueCatSubscriberSnapshot.user_id == target.id,
                RevenueCatSubscriberSnapshot.app_user_id.in_(rc_ids),
            ),
        ),
    }


async def delete_database_user(db: AsyncSession, target: UserDeletionTarget) -> None:
    """Delete all known personal data and then the local user atomically."""
    meal_ids = select(Meal.id).where(Meal.user_id == target.id)
    rc_ids = _revenuecat_ids(target)

    # Tables using SET NULL or no FK need explicit deletion for privacy. The
    # remaining explicit deletes also make this reliable when SQLite FK
    # enforcement is disabled in local/test environments.
    statements = [
        delete(RevenueCatEvent).where(RevenueCatEvent.app_user_id.in_(rc_ids)),
        delete(RevenueCatSubscriberSnapshot).where(
            or_(
                RevenueCatSubscriberSnapshot.user_id == target.id,
                RevenueCatSubscriberSnapshot.app_user_id.in_(rc_ids),
            )
        ),
        delete(AIInferenceLog).where(AIInferenceLog.user_id == target.id),
        delete(MealAnalysisJob).where(MealAnalysisJob.user_id == target.id),
        delete(MealRevision).where(MealRevision.user_id == target.id),
        delete(MealItem).where(MealItem.meal_id.in_(meal_ids)),
        delete(Meal).where(Meal.user_id == target.id),
        delete(BurnedCalories).where(BurnedCalories.user_id == target.id),
        delete(DailySummary).where(DailySummary.user_id == target.id),
        delete(UserFoodMemory).where(UserFoodMemory.user_id == target.id),
        delete(PromoCodeRedemption).where(PromoCodeRedemption.user_id == target.id),
        delete(PromoCodeAttempt).where(PromoCodeAttempt.user_id == target.id),
        delete(User).where(User.id == target.id),
    ]
    try:
        for statement in statements:
            await db.execute(statement)
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def delete_revenuecat_customer(target: UserDeletionTarget) -> bool:
    client = RevenueCatClient()
    if not client.is_configured:
        raise RuntimeError(
            "REVENUECAT_API_KEY is required for complete deletion. "
            "Configure it or pass --keep-revenuecat explicitly."
        )
    app_user_id = target.revenuecat_app_user_id or target.firebase_uid
    return await client.delete_customer(app_user_id)


async def delete_firebase_identity(target: UserDeletionTarget) -> bool:
    if not settings.FIREBASE_CREDENTIALS:
        raise RuntimeError(
            "FIREBASE_CREDENTIALS is required for complete deletion. "
            "Configure it or pass --keep-firebase explicitly."
        )

    from firebase_admin import auth

    init_firebase()
    try:
        record = await asyncio.to_thread(auth.get_user, target.firebase_uid)
    except auth.UserNotFoundError:
        return False

    if record.email and record.email.casefold() != target.email.casefold():
        raise RuntimeError(
            "Firebase UID email does not match the backend email; deletion aborted "
            "to avoid removing the wrong identity."
        )
    await asyncio.to_thread(auth.delete_user, target.firebase_uid)
    return True


def _confirm(target: UserDeletionTarget, counts: dict[str, int], assume_yes: bool) -> None:
    print(f"Environment: {settings.ENVIRONMENT}")
    print(f"User: {target.email} (id={target.id}, firebase_uid={target.firebase_uid})")
    print("Data that will be permanently deleted:")
    for label, count in counts.items():
        print(f"  {label}: {count}")
    print("Note: deleting the RevenueCat customer does not cancel an App Store or Play Store subscription.")
    if assume_yes:
        return

    answer = input(f"Type DELETE {target.email} to continue: ").strip()
    if answer != f"DELETE {target.email}":
        raise SystemExit("Deletion cancelled.")


async def run(args: argparse.Namespace) -> None:
    try:
        email = normalize_email(args.email)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    async with SessionLocal() as db:
        user = await find_user_by_email(db, email)
        if user is None:
            raise SystemExit(f"No backend user found for {email!r}.")
        target = deletion_target(user)
        counts = await related_data_counts(db, target)

    _confirm(target, counts, args.yes)

    if not args.keep_revenuecat:
        deleted = await delete_revenuecat_customer(target)
        print("RevenueCat customer deleted." if deleted else "RevenueCat customer was already absent.")

    if not args.keep_firebase:
        deleted = await delete_firebase_identity(target)
        print("Firebase identity deleted." if deleted else "Firebase identity was already absent.")

    async with SessionLocal() as db:
        # Re-resolve the target so a concurrent change cannot redirect deletion.
        current = await db.get(User, target.id)
        if current is None:
            print("Backend user was already absent.")
            return
        if current.email.casefold() != target.email.casefold() or current.firebase_uid != target.firebase_uid:
            raise RuntimeError("User identity changed during deletion; backend deletion aborted.")
        await delete_database_user(db, target)

    print(f"Deleted {target.email} and all selected account data.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Permanently delete a Calry user by email.")
    parser.add_argument("email", help="Exact account email (comparison is case-insensitive).")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt.")
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="Delete only backend data; equivalent to --keep-firebase --keep-revenuecat.",
    )
    parser.add_argument("--keep-firebase", action="store_true", help="Do not delete the Firebase Auth identity.")
    parser.add_argument("--keep-revenuecat", action="store_true", help="Do not delete the RevenueCat customer.")
    args = parser.parse_args()
    if args.backend_only:
        args.keep_firebase = True
        args.keep_revenuecat = True
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
