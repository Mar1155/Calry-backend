import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import CalryException, NotFoundException, ValidationException
from app.dependencies.admin import AdminIdentity, enforce_admin_rate_limit, get_current_admin, new_audit
from app.dependencies.db import get_db
from app.models.admin import AdminAuditLog, UserDeletionJob
from app.models.user import User
from app.schemas.admin import (
    AdminMeResponse,
    AuditLogListResponse,
    CreateDeletionJobRequest,
    DeletionJobCreatedResponse,
    DeletionJobResponse,
    DeletionPreviewResponse,
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

router = APIRouter()


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
        requested_by_admin_email=admin.email,
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
