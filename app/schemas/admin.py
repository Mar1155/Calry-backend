import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["pending", "running", "partially_failed", "completed"]


class AdminMeResponse(BaseModel):
    uid: str
    email: str | None
    authorized: bool = True


class UserSummaryResponse(BaseModel):
    id: int
    email: str
    name: str | None
    firebase_uid: str
    revenuecat_app_user_id: str | None
    created_at: dt.datetime
    updated_at: dt.datetime | None
    onboarding_status: str
    is_premium: bool
    deletion_status: JobStatus | None


class UserSearchItemResponse(BaseModel):
    id: int
    email: str
    name: str | None
    firebase_uid: str
    created_at: dt.datetime
    onboarding_status: str
    is_premium: bool
    deletion_status: JobStatus | None


class UserSearchResponse(BaseModel):
    results: list[UserSearchItemResponse]
    total: int
    limit: int
    offset: int


class SubscriptionResponse(BaseModel):
    revenuecat_customer_found: bool | None
    entitlement_active: bool
    entitlement: str | None
    product_id: str | None
    store: str | None
    expiration_date: dt.datetime | None
    management_url: str | None = None


class UserDetailResponse(UserSummaryResponse):
    inventory: dict[str, int]
    storage_objects: list[str]
    subscription: SubscriptionResponse


class DeletionPreviewResponse(BaseModel):
    target: UserSummaryResponse
    inventory: dict[str, int]
    storage_objects: list[str]
    subscription: SubscriptionResponse
    warnings: list[str]
    deletion_allowed: bool
    preview_version: str


class CreateDeletionJobRequest(BaseModel):
    confirmation_value: str = Field(min_length=1, max_length=255)
    reason: Literal["admin_requested_deletion"]
    preview_version: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")


class DeletionJobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus


class DeletionStepResponse(BaseModel):
    key: str
    label: str
    status: Literal["waiting", "running", "completed", "failed", "already_absent", "skipped"]
    error_code: str | None = None
    message: str | None = None


class DeletionJobResponse(BaseModel):
    id: str
    target_user_id: int
    target_email: str
    status: JobStatus
    current_step: str | None
    steps: list[DeletionStepResponse]
    created_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    failed_at: dt.datetime | None
    retry_count: int
    can_retry: bool
    last_error_code: str | None
    last_error_message: str | None


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp: dt.datetime
    admin_uid: str
    admin_email: str | None
    action: str
    target_user_id: int | None
    safe_target_identifier: str | None
    deletion_job_id: str | None
    result: str
    metadata_json: dict
    request_id: str | None
    source_ip: str | None


class AuditLogListResponse(BaseModel):
    results: list[AuditLogResponse]
    total: int
