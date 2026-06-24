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

    # CORS — comma-separated allowed origins, or "*" for all.
    # In production set an explicit list (e.g. "https://app.calry.ai") to allow
    # credentialed requests; "*" disables credentials per the CORS spec.
    ALLOWED_ORIGINS: str = "*"

    # Database URLs
    # Must use postgresql+asyncpg:// for SQLAlchemy async connections
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/calry"

    # Firebase configuration
    FIREBASE_PROJECT_ID: str = "calry-62362"
    FIREBASE_CREDENTIALS: str | None = None

    # AI API keys
    OPENROUTER_API_KEY: str | None = None
    DEFAULT_AI_PROVIDER: str = "openrouter"
    AI_PROVIDER: str = "openrouter"
    # Model split (C14): text-only estimation is dominated by the in-prompt
    # reference anchors + deterministic validation, so flash-lite (-67% in /
    # -84% out) is adequate. Vision genuinely benefits from flash, so photos
    # stay on the stronger model. Voice transcription stays on flash for ASR
    # quality, then its transcript is estimated with the text model.
    OPENROUTER_TEXT_MODEL: str = "google/gemini-2.5-flash-lite"
    OPENROUTER_IMAGE_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_AUDIO_MODEL: str = "google/gemini-2.5-flash"
    AI_REQUEST_TIMEOUT_SECONDS: float = 30.0
    AI_MAX_RETRIES: int = 1

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
