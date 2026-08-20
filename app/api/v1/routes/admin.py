import asyncio
import datetime as dt
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from firebase_admin import auth as firebase_auth
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import CalryException, NotFoundException, ValidationException
from app.core.security import init_firebase
from app.dependencies.admin import AdminIdentity, enforce_admin_rate_limit, get_current_admin, new_audit
from app.dependencies.db import get_db
from app.models.admin import AdminAuditLog, UserDeletionJob
from app.models.promo_code import PromoCode
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.admin import (
    AccessRestrictionRequest,
    AccessRestrictionResponse,
    AdminMeResponse,
    AuditLogListResponse,
    CreateDeletionJobRequest,
    CreatePromoCodeRequest,
    DeletionJobCreatedResponse,
    DeletionJobResponse,
    DeletionPreviewResponse,
    LiftAccessRestrictionRequest,
    PromoCodeCreatedResponse,
    RevokePromotionalEntitlementRequest,
    RevokePromotionalEntitlementResponse,
    SubscriptionResponse,
    UserDetailResponse,
    UserSearchItemResponse,
    UserSearchResponse,
    UserSummaryResponse,
)
from app.services.admin_deletion import (
    build_preview_snapshot,
    deletion_status_for,
    initial_steps,
    preview_version,
    process_deletion_job,
)
from app.services.promo_code_service import generate_promo_code, promo_code_digest, promo_code_hint
from app.services.revenuecat_service import RevenueCatAPIError, RevenueCatClient, derive_entitlement_state

router = APIRouter()
logger = logging.getLogger("app.api.admin")


def _abbreviate(value: str) -> str:
    return value if len(value) <= 12 else f"{value[:6]}…{value[-4:]}"


async def _summary(db: AsyncSession, user: User, *, abbreviate_uid: bool = False) -> UserSummaryResponse:
    return UserSummaryResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        firebase_uid=_abbreviate(user.firebase_uid) if abbreviate_uid else user.firebase_uid,
        revenuecat_app_user_id=user.revenuecat_app_user_id,
        created_at=user.created_at,
        updated_at=user.updated_at,
        onboarding_status=user.onboarding_status,
        is_premium=user.is_premium,
        deletion_status=await deletion_status_for(db, user.id),
        access_status=user.access_status,
        access_restriction_reason=user.access_restriction_reason,
        access_restriction_legal_basis=user.access_restriction_legal_basis,
        access_restriction_expires_at=user.access_restriction_expires_at,
    )


async def _get_user(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundException("User not found.", "ADMIN_USER_NOT_FOUND")
    return user


def _job_response(job: UserDeletionJob) -> DeletionJobResponse:
    now = dt.datetime.now(dt.UTC)
    created_at = job.created_at.replace(tzinfo=job.created_at.tzinfo or dt.UTC)
    started_at = job.started_at.replace(tzinfo=job.started_at.tzinfo or dt.UTC) if job.started_at else None
    stale_pending = job.status == "pending" and (now - created_at).total_seconds() >= settings.ADMIN_DELETION_STALE_SECONDS
    stale_running = bool(
        job.status == "running"
        and started_at
        and (now - started_at).total_seconds() >= settings.ADMIN_DELETION_STALE_SECONDS
    )
    return DeletionJobResponse(
        id=job.id,
        target_user_id=job.target_user_id,
        target_email=job.target_email,
        status=job.status,
        current_step=job.current_step,
        steps=job.steps_json,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failed_at=job.failed_at,
        retry_count=job.retry_count,
        can_retry=job.status == "partially_failed" or stale_pending or stale_running,
        last_error_code=job.last_error_code,
        last_error_message=job.last_error_message,
    )


@router.get("/me", response_model=AdminMeResponse)
async def admin_me(
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminMeResponse:
    db.add(new_audit(request, admin, "admin_login", "success"))
    return AdminMeResponse(uid=admin.uid, email=admin.email)


@router.get("/users/search", response_model=UserSearchResponse)
async def search_users(
    request: Request,
    q: str = Query(max_length=255),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> UserSearchResponse:
    enforce_admin_rate_limit(request, "search", settings.ADMIN_SEARCH_RATE_LIMIT_PER_MINUTE)
    term = q.strip()
    if (not term.isdigit() and len(term) < 3) or not term:
        raise ValidationException("Enter at least 3 characters, or an exact numeric user ID.", "SEARCH_QUERY_TOO_SHORT")
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    conditions = [
        User.email.ilike(pattern, escape="\\"),
        User.firebase_uid.ilike(pattern, escape="\\"),
        User.revenuecat_app_user_id.ilike(pattern, escape="\\"),
    ]
    if term.isdigit():
        conditions.append(User.id == int(term))
    where = or_(*conditions)
    total = int((await db.execute(select(func.count()).select_from(User).where(where))).scalar_one())
    users = (
        await db.scalars(select(User).where(where).order_by(User.created_at.desc()).offset(offset).limit(limit))
    ).all()
    db.add(new_audit(request, admin, "user_search", "success", metadata={"result_count": len(users)}))
    return UserSearchResponse(
        results=[
            UserSearchItemResponse(
                id=user.id,
                email=user.email,
                name=user.name,
                firebase_uid=_abbreviate(user.firebase_uid),
                created_at=user.created_at,
                onboarding_status=user.onboarding_status,
                is_premium=user.is_premium,
                deletion_status=await deletion_status_for(db, user.id),
                access_status=user.access_status,
                access_restriction_expires_at=user.access_restriction_expires_at,
            )
            for user in users
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_detail(
    user_id: int,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    user = await _get_user(db, user_id)
    snapshot = await build_preview_snapshot(db, user)
    db.add(new_audit(request, admin, "user_viewed", "success", target_user_id=user.id, target_identifier=user.email))
    return UserDetailResponse(
        **(await _summary(db, user)).model_dump(),
        inventory=snapshot["inventory"],
        storage_objects=snapshot["storage_objects"],
        subscription=SubscriptionResponse(**snapshot["subscription"]),
    )


async def _revoke_firebase_sessions(firebase_uid: str) -> bool:
    if settings.is_testing:
        return True
    try:
        init_firebase()
        await asyncio.to_thread(firebase_auth.revoke_refresh_tokens, firebase_uid)
        return True
    except Exception as exc:
        logger.exception("admin firebase session revocation failed error_type=%s", type(exc).__name__)
        return False


@router.post("/users/{user_id}/access-restriction", response_model=AccessRestrictionResponse)
async def restrict_user_access(
    user_id: int,
    payload: AccessRestrictionRequest,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AccessRestrictionResponse:
    enforce_admin_rate_limit(request, "access_restriction", settings.ADMIN_DELETION_RATE_LIMIT_PER_MINUTE)
    user = await _get_user(db, user_id)
    if user.firebase_uid == admin.uid:
        raise ValidationException("Administrators cannot restrict their own account.", "ADMIN_SELF_RESTRICTION")
    if payload.confirmation_value not in {user.email, str(user.id)}:
        raise ValidationException("Confirmation value does not match target user.", "RESTRICTION_CONFIRMATION_MISMATCH")

    expires_at = payload.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.UTC)
    if payload.status == "suspended" and (expires_at is None or expires_at <= dt.datetime.now(dt.UTC)):
        raise ValidationException("A suspension requires a future expiration.", "INVALID_RESTRICTION_EXPIRATION")
    if payload.status == "banned" and expires_at is not None:
        raise ValidationException("A ban cannot have an expiration.", "INVALID_RESTRICTION_EXPIRATION")

    now = dt.datetime.now(dt.UTC)
    user.access_status = payload.status
    user.access_restriction_reason = payload.reason
    user.access_restriction_legal_basis = payload.legal_basis
    user.access_restricted_at = now
    user.access_restriction_expires_at = expires_at
    user.access_restricted_by_admin_uid = admin.uid
    db.add(
        new_audit(
            request,
            admin,
            "user_access_restricted",
            "success",
            target_user_id=user.id,
            target_identifier=user.email,
            metadata={
                "status": payload.status,
                "reason": payload.reason,
                "legal_basis": payload.legal_basis,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )
    )
    await db.commit()
    tokens_revoked = await _revoke_firebase_sessions(user.firebase_uid)
    return AccessRestrictionResponse(
        status=user.access_status,
        reason=user.access_restriction_reason,
        legal_basis=user.access_restriction_legal_basis,
        restricted_at=user.access_restricted_at,
        expires_at=user.access_restriction_expires_at,
        firebase_tokens_revoked=tokens_revoked,
    )


@router.post("/users/{user_id}/access-restriction/lift", response_model=AccessRestrictionResponse)
async def lift_user_access_restriction(
    user_id: int,
    payload: LiftAccessRestrictionRequest,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AccessRestrictionResponse:
    user = await _get_user(db, user_id)
    previous_status = user.access_status
    user.access_status = "active"
    user.access_restriction_reason = None
    user.access_restriction_legal_basis = None
    user.access_restricted_at = None
    user.access_restriction_expires_at = None
    user.access_restricted_by_admin_uid = None
    db.add(
        new_audit(
            request,
            admin,
            "user_access_restored",
            "success",
            target_user_id=user.id,
            target_identifier=user.email,
            metadata={"previous_status": previous_status, "reason": payload.reason},
        )
    )
    await db.commit()
    return AccessRestrictionResponse(
        status="active",
        reason=None,
        legal_basis=None,
        restricted_at=None,
        expires_at=None,
        firebase_tokens_revoked=False,
    )


@router.post(
    "/users/{user_id}/premium/revoke-promotional",
    response_model=RevokePromotionalEntitlementResponse,
)
async def revoke_user_promotional_entitlement(
    user_id: int,
    payload: RevokePromotionalEntitlementRequest,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> RevokePromotionalEntitlementResponse:
    enforce_admin_rate_limit(request, "premium_revoke", settings.ADMIN_DELETION_RATE_LIMIT_PER_MINUTE)
    user = await _get_user(db, user_id)
    if payload.confirmation_value not in {user.email, str(user.id)}:
        raise ValidationException("Confirmation value does not match target user.", "PREMIUM_CONFIRMATION_MISMATCH")
    app_user_id = user.revenuecat_app_user_id or user.firebase_uid
    try:
        subscriber = await RevenueCatClient().revoke_promotional_entitlements(
            app_user_id,
            settings.REVENUECAT_ENTITLEMENT_ID,
        )
    except RevenueCatAPIError as exc:
        raise CalryException(
            "RevenueCat promotional grants could not be revoked.",
            502,
            "REVENUECAT_PROMOTIONAL_REVOKE_FAILED",
        ) from exc

    state = derive_entitlement_state(subscriber, settings.REVENUECAT_ENTITLEMENT_ID)
    await UserRepository(db).update_user_premium_status(
        user=user,
        is_premium=state.is_active,
        premium_entitlement=settings.REVENUECAT_ENTITLEMENT_ID if state.is_active else None,
        premium_expires_at=state.expires_at,
        revenuecat_app_user_id=app_user_id,
        premium_store=state.store,
        premium_product_id=state.product_id,
    )
    db.add(
        new_audit(
            request,
            admin,
            "promotional_entitlement_revoked",
            "success",
            target_user_id=user.id,
            target_identifier=user.email,
            metadata={
                "reason": payload.reason,
                "store_entitlement_remains_active": state.is_active,
                "resulting_store": state.store,
            },
        )
    )
    await db.commit()
    return RevokePromotionalEntitlementResponse(
        promotional_grants_revoked=True,
        entitlement_active=state.is_active,
        store=state.store,
        expiration_date=state.expires_at,
    )


@router.post("/promo-codes", response_model=PromoCodeCreatedResponse, status_code=201)
async def create_promo_code(
    payload: CreatePromoCodeRequest,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoCodeCreatedResponse:
    """Create a free-access code whose plaintext is returned exactly once."""
    enforce_admin_rate_limit(request, "promo_code_create", settings.ADMIN_PROMO_CODE_RATE_LIMIT_PER_MINUTE)
    pepper = settings.PROMO_CODE_PEPPER
    if not pepper:
        raise CalryException(
            "Access-code generation is not configured.",
            503,
            "PROMO_CODE_NOT_CONFIGURED",
        )

    plaintext = generate_promo_code()
    valid_until = (
        dt.datetime.now(dt.UTC) + dt.timedelta(days=payload.valid_days)
        if payload.valid_days is not None
        else None
    )
    promo = PromoCode(
        code_digest=promo_code_digest(plaintext, pepper),
        code_hint=promo_code_hint(plaintext),
        kind="free_access",
        grant_duration=payload.grant_duration,
        max_redemptions=payload.max_redemptions,
        valid_until=valid_until,
    )
    db.add(promo)
    await db.flush()
    db.add(
        new_audit(
            request,
            admin,
            "promo_code_created",
            "success",
            metadata={
                "promo_code_id": promo.id,
                "code_hint": promo.code_hint,
                "grant_duration": promo.grant_duration,
                "max_redemptions": promo.max_redemptions,
                "valid_until": valid_until.isoformat() if valid_until else None,
            },
        )
    )
    await db.commit()
    logger.info("admin_promo_code_created promo_code_id=%s admin_uid=%s", promo.id, admin.uid)
    return PromoCodeCreatedResponse(
        id=promo.id,
        code=plaintext,
        code_hint=promo.code_hint,
        grant_duration=promo.grant_duration,
        max_redemptions=promo.max_redemptions,
        valid_until=promo.valid_until,
        created_at=promo.created_at,
    )


@router.get("/users/{user_id}/deletion-preview", response_model=DeletionPreviewResponse)
async def get_deletion_preview(
    user_id: int,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> DeletionPreviewResponse:
    user = await _get_user(db, user_id)
    snapshot = await build_preview_snapshot(db, user)
    version = preview_version(snapshot)
    db.add(
        new_audit(
            request,
            admin,
            "deletion_preview",
            "success",
            target_user_id=user.id,
            target_identifier=user.email,
            metadata={"preview_version": version},
        )
    )
    return DeletionPreviewResponse(**snapshot, preview_version=version)


@router.post("/users/{user_id}/deletion-jobs", response_model=DeletionJobCreatedResponse, status_code=202)
async def create_deletion_job(
    user_id: int,
    payload: CreateDeletionJobRequest,
    request: Request,
    background: BackgroundTasks,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> DeletionJobCreatedResponse:
    enforce_admin_rate_limit(request, "deletion", settings.ADMIN_DELETION_RATE_LIMIT_PER_MINUTE)
    existing = await db.scalar(select(UserDeletionJob).where(UserDeletionJob.idempotency_key == payload.idempotency_key))
    if existing:
        if existing.target_user_id != user_id or existing.requested_by_admin_uid != admin.uid:
            raise CalryException("Idempotency key is already in use.", 409, "IDEMPOTENCY_KEY_CONFLICT")
        return DeletionJobCreatedResponse(job_id=existing.id, status=existing.status)

    user = await _get_user(db, user_id)
    active = await db.scalar(
        select(UserDeletionJob).where(
            UserDeletionJob.target_user_id == user_id,
            UserDeletionJob.status.in_(["pending", "running", "partially_failed"]),
        )
    )
    if active:
        raise CalryException(
            "A deletion job already exists for this user.",
            409,
            "DELETION_JOB_ALREADY_ACTIVE",
            {"job_id": active.id},
        )
    if payload.confirmation_value not in {user.email, str(user.id)}:
        raise ValidationException("Confirmation value does not match target user.", "DELETION_CONFIRMATION_MISMATCH")
    snapshot = await build_preview_snapshot(db, user)
    current_version = preview_version(snapshot)
    if payload.preview_version != current_version:
        raise CalryException("Deletion preview is stale. Review it again.", 409, "DELETION_PREVIEW_STALE")

    user.deletion_in_progress = True
    job = UserDeletionJob(
        target_user_id=user.id,
        target_email=user.email,
        target_firebase_uid=user.firebase_uid,
        target_revenuecat_app_user_id=user.revenuecat_app_user_id,
        requested_by_admin_uid=admin.uid,
        requested_by_admin_email=None,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        preview_snapshot_json=snapshot,
        steps_json=initial_steps(),
    )
    db.add(job)
    await db.flush()
    db.add(
        new_audit(
            request,
            admin,
            "deletion_job_created",
            "success",
            target_user_id=user.id,
            target_identifier=user.email,
            deletion_job_id=job.id,
            metadata={"reason": payload.reason, "preview_version": current_version},
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise CalryException("Deletion request conflicts with an existing job.", 409, "DELETION_JOB_CONFLICT") from exc
    background.add_task(process_deletion_job, job.id)
    return DeletionJobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/deletion-jobs/{job_id}", response_model=DeletionJobResponse)
async def get_deletion_job(
    job_id: str,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> DeletionJobResponse:
    job = await db.get(UserDeletionJob, job_id)
    if not job:
        raise NotFoundException("Deletion job not found.", "DELETION_JOB_NOT_FOUND")
    return _job_response(job)


@router.post("/deletion-jobs/{job_id}/retry", response_model=DeletionJobCreatedResponse, status_code=202)
async def retry_deletion_job(
    job_id: str,
    request: Request,
    background: BackgroundTasks,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> DeletionJobCreatedResponse:
    enforce_admin_rate_limit(request, "deletion", settings.ADMIN_DELETION_RATE_LIMIT_PER_MINUTE)
    job = await db.get(UserDeletionJob, job_id)
    if not job:
        raise NotFoundException("Deletion job not found.", "DELETION_JOB_NOT_FOUND")
    if not _job_response(job).can_retry:
        raise CalryException("Job is active or already completed.", 409, "DELETION_JOB_NOT_RETRYABLE")
    job.status = "pending"
    job.retry_count += 1
    db.add(
        new_audit(
            request,
            admin,
            "deletion_job_retried",
            "success",
            target_user_id=job.target_user_id,
            target_identifier=job.target_email,
            deletion_job_id=job.id,
            metadata={"retry_count": job.retry_count},
        )
    )
    await db.commit()
    background.add_task(process_deletion_job, job.id)
    return DeletionJobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/audit-log", response_model=AuditLogListResponse)
async def get_audit_log(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    total = int((await db.execute(select(func.count()).select_from(AdminAuditLog))).scalar_one())
    entries = (
        await db.scalars(select(AdminAuditLog).order_by(AdminAuditLog.timestamp.desc()).offset(offset).limit(limit))
    ).all()
    return AuditLogListResponse(results=list(entries), total=total)
