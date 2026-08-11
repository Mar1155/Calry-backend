from datetime import date
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "testing", "production"] = "development"
    LOG_LEVEL: str = "info"
    PORT: int = 8000

    # Sentry error/performance monitoring. Unset in dev to keep local runs quiet.
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 1.0
    SENTRY_PROFILE_SESSION_SAMPLE_RATE: float = 1.0

    # CORS — comma-separated allowed origins, or "*" for all.
    # In production set an explicit list (e.g. "https://app.calry.ai") to allow
    # credentialed requests; "*" disables credentials per the CORS spec.
    ALLOWED_ORIGINS: str = "*"

    # Public legal documents used by the App Store, Google Play and the
    # RevenueCat-hosted paywall. Configure the real legal entity and a monitored
    # inbox before release; the store-listing fallback keeps local builds usable.
    LEGAL_OPERATOR_NAME: str = "Calry"
    LEGAL_CONTACT_EMAIL: str | None = None
    LEGAL_EFFECTIVE_DATE: date = date(2026, 7, 21)

    # Database URLs
    # Must use postgresql+asyncpg:// for SQLAlchemy async connections
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/calry"

    # Redis/Celery background jobs. Railway should run a separate worker service
    # with start_worker.sh and the same env vars as the API.
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str | None = None
    MEAL_ANALYSIS_MAX_RETRIES: int = 2
    # Keep Railway worker memory predictable. Celery otherwise defaults to one
    # prefork child per visible CPU, duplicating the full application in each.
    CELERY_WORKER_CONCURRENCY: int = 1
    CELERY_WORKER_MAX_TASKS_PER_CHILD: int = 20
    CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB: int = 384_000

    # Media storage. "local" keeps current app/static/uploads behavior for dev.
    # "s3" uploads to S3-compatible storage and returns an HTTP URL.
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    S3_BUCKET: str | None = None
    S3_REGION: str = "us-east-1"
    S3_ENDPOINT_URL: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_PUBLIC_URL_BASE: str | None = None
    S3_PUBLIC_READ: bool = True
    MEAL_UPLOAD_MAX_BYTES: int = 20 * 1024 * 1024

    # Firebase configuration
    FIREBASE_PROJECT_ID: str = "calry-62362"
    FIREBASE_CREDENTIALS: str | None = None

    # Internal admin dashboard. Authorization always happens after Firebase ID
    # token verification. A custom ``admin: true`` claim is preferred; this
    # allowlist is the safe MVP fallback.
    ADMIN_FIREBASE_UIDS: str = ""
    ADMIN_FRONTEND_ORIGIN: str | None = None
    ADMIN_SEARCH_RATE_LIMIT_PER_MINUTE: int = 30
    ADMIN_DELETION_RATE_LIMIT_PER_MINUTE: int = 5
    ADMIN_DELETION_STALE_SECONDS: int = 300
    # Stable server-only key used to pseudonymize identifiers in security audit
    # records. Raw user emails and client IP addresses are never stored there.
    ADMIN_AUDIT_HASH_KEY: str | None = None
    ADMIN_AUDIT_RETENTION_DAYS: int = 365

    # RevenueCat webhook shared secret. Required in production so billing state
    # only changes via server-to-server events.
    REVENUECAT_WEBHOOK_SECRET: str | None = None

    # RevenueCat secret REST API key (sk_...). When set, every webhook triggers a
    # GET /subscribers/{app_user_id} verification so is_premium reflects the real
    # entitlement state instead of trusting the webhook payload. When unset, the
    # webhook falls back to payload-derived state (logged as such).
    REVENUECAT_API_KEY: str | None = None
    # Entitlement identifier configured in the RevenueCat dashboard.
    REVENUECAT_ENTITLEMENT_ID: str = "Calry Pro"
    REVENUECAT_API_TIMEOUT_SECONDS: float = 10.0
    REVENUECAT_API_MAX_RETRIES: int = 2

    # Custom free-access codes. Codes are never stored in plaintext: this
    # server-only pepper HMACs them before lookup. It must be a long random
    # secret in production and must stay stable across deployments.
    PROMO_CODE_PEPPER: str | None = None
    PROMO_CODE_REDEMPTION_ENABLED: bool = True
    PROMO_CODE_MAX_ATTEMPTS: int = 8
    PROMO_CODE_ATTEMPT_WINDOW_MINUTES: int = 15

    # Tester deployments: treat every authenticated user as premium, so testers
    # never pay. Pair with the app-side PREMIUM_BYPASS dart-define. Must stay
    # False on the real production deployment.
    PREMIUM_BYPASS: bool = False

    # Rollout guard for persisted, event-driven insight snapshots. Disable to
    # fall back to legacy endpoints while preserving stored snapshot data.
    ENABLE_VERSIONED_INSIGHT_SNAPSHOTS: bool = True
    INSIGHT_RECOMPUTE_DEBOUNCE_SECONDS: int = 600

    # Proactive Insight Engine. Events are stored transactionally, then consumed
    # asynchronously; periodic sweeps recover any missed broker notification.
    ENABLE_PROACTIVE_INSIGHTS: bool = True
    PROACTIVE_INSIGHTS_ASYNC_ENABLED: bool = True
    PROACTIVE_INSIGHT_MODEL: str = "google/gemini-2.5-flash-lite"
    PROACTIVE_INSIGHT_MIN_CONFIDENCE: float = 0.72
    PROACTIVE_INSIGHT_MIN_SIGNIFICANCE: float = 0.30
    PROACTIVE_INSIGHT_MIN_NOVELTY: float = 0.55
    PROACTIVE_INSIGHT_MIN_USEFULNESS: float = 0.55
    PROACTIVE_INSIGHT_COOLDOWN_DAYS: int = 7
    PROACTIVE_INSIGHT_TYPE_COOLDOWNS: str = "daily_calorie_milestone:1,repeated_meal:14"
    PROACTIVE_INSIGHT_MAX_CANDIDATES_PER_EVENT: int = 4
    PROACTIVE_NOTIFICATION_MIN_SCORE: float = 0.62
    PROACTIVE_NOTIFICATION_DAILY_LIMIT: int = 1
    PROACTIVE_NOTIFICATION_MAX_AGE_HOURS: int = 72
    PROACTIVE_NOTIFICATION_RETRY_MAX: int = 3
    PROACTIVE_NOTIFICATION_RETRY_BASE_SECONDS: int = 300
    PROACTIVE_PUSH_ENABLED: bool = True

    @property
    def proactive_insight_type_cooldowns(self) -> dict[str, int]:
        values: dict[str, int] = {}
        for item in self.PROACTIVE_INSIGHT_TYPE_COOLDOWNS.split(","):
            name, separator, days = item.strip().partition(":")
            if separator and name and days.isdigit():
                values[name] = int(days)
        return values

    # AI API keys
    OPENROUTER_API_KEY: str | None = None
    DEFAULT_AI_PROVIDER: str = "openrouter"
    AI_PROVIDER: str = "openrouter"
    # Text and image models analyze meals. Audio is first converted to text by
    # the dedicated speech-to-text endpoint, then follows the text pipeline.
    OPENROUTER_TEXT_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_IMAGE_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_TRANSCRIPTION_MODEL: str = "openai/whisper-large-v3"
    AI_REQUEST_TIMEOUT_SECONDS: float = 30.0
    AI_MAX_RETRIES: int = 1
    # Keep structured food responses short. Reasoning-capable budget models can
    # otherwise spend thousands of completion tokens thinking before emitting JSON.
    AI_MAX_COMPLETION_TOKENS: int = 900
    AI_TEMPERATURE: float = 0.1
    AI_REASONING_EFFORT: str = "minimal"
    AI_EXCLUDE_REASONING: bool = True

    # Structured outputs (C16): use OpenRouter json_schema response_format. Falls
    # back to json_object automatically if a routed model rejects it.
    AI_STRUCTURED_OUTPUT: bool = True

    # Image preprocessing (C15): conservative downscale before the vision call to
    # cut image input tokens (~50%, tile-based) with negligible recognition loss.
    AI_IMAGE_DOWNSCALE: bool = True
    AI_IMAGE_MAX_EDGE: int = 1536
    AI_IMAGE_JPEG_QUALITY: int = 85

    # Pre-inference food-memory cache (C3 / C19): serve confirmed repeat foods
    # deterministically without an LLM call.
    FOOD_MEMORY_CACHE_ENABLED: bool = True
    FOOD_MEMORY_FUZZY_ENABLED: bool = True
    FOOD_MEMORY_FUZZY_THRESHOLD: int = 92  # rapidfuzz token_set_ratio [0-100]
    FOOD_MEMORY_MIN_USE_COUNT: int = 2  # only serve a memory confirmed >= N times

    # AI Memory System (Phase 1, deterministic MVP). Master switch for derivation
    # and read endpoints. Distillation runs in a Celery worker; when the async flag
    # is off (or no broker is reachable) the trigger degrades to a no-op so the meal
    # path never fails. Confidence thresholds are calibration knobs (see RFC §6/§8).
    MEMORY_ENABLED: bool = True
    MEMORY_DISTILLATION_ASYNC_ENABLED: bool = True
    MEMORY_ACTIVE_AT: float = 0.70  # provisional -> active confidence threshold
    MEMORY_ARCHIVE_FLOOR: float = 0.50  # below this a belief archives on consolidation
    MEMORY_PORTION_DIVERGENCE_PCT: float = 0.15  # value-change tolerance for portions
    MEMORY_PREFERENCE_REGULAR_OCCURRENCES: int = 5  # confirmed logs across days to be "regular"
    MEMORY_PREFERENCE_REGULAR_DAYS: int = 3
    MEMORY_CALIBRATION_NO_EDIT_RATE: float = 0.70  # band that earns a "learning" moment
    MEMORY_CALIBRATION_WITHIN5_RATE: float = 0.60  # band that earns a "within 5%" moment
    MEMORY_MAX_TIMELINE_LIMIT: int = 100

    @property
    def cors_origins(self) -> list[str]:
        """Parsed list of allowed CORS origins."""
        origins = [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]
        if self.ADMIN_FRONTEND_ORIGIN and origins != ["*"] and self.ADMIN_FRONTEND_ORIGIN not in origins:
            origins.append(self.ADMIN_FRONTEND_ORIGIN)
        return origins

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "testing"

    @property
    def admin_firebase_uids(self) -> set[str]:
        return {uid.strip() for uid in self.ADMIN_FIREBASE_UIDS.split(",") if uid.strip()}


settings = Settings()
