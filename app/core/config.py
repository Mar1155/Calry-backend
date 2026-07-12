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

    # Firebase configuration
    FIREBASE_PROJECT_ID: str = "calry-62362"
    FIREBASE_CREDENTIALS: str | None = None

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

    # Tester deployments: treat every authenticated user as premium, so testers
    # never pay. Pair with the app-side PREMIUM_BYPASS dart-define. Must stay
    # False on the real production deployment.
    PREMIUM_BYPASS: bool = False

    # AI API keys
    OPENROUTER_API_KEY: str | None = None
    DEFAULT_AI_PROVIDER: str = "openrouter"
    AI_PROVIDER: str = "openrouter"
    # Model split (C14): text-only estimation is dominated by the in-prompt
    # reference anchors + deterministic validation, so flash-lite (-67% in /
    # -84% out) is adequate. Vision genuinely benefits from flash, so photos
    # stay on the stronger model. Voice transcription stays on flash for ASR
    # quality, then its transcript is estimated with the text model.
    OPENROUTER_TEXT_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_IMAGE_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_AUDIO_MODEL: str = "google/gemini-2.5-flash"
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
    FOOD_MEMORY_MIN_USE_COUNT: int = 2     # only serve a memory confirmed >= N times

    @property
    def cors_origins(self) -> list[str]:
        """Parsed list of allowed CORS origins."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "testing"


settings = Settings()
