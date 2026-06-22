from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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
    FIREBASE_CREDENTIALS_PATH: str | None = None
    FIREBASE_PROJECT_ID: str = "calry-62362"

    # AI API keys
    OPENROUTER_API_KEY: str | None = None
    DEFAULT_AI_PROVIDER: str = "openrouter"
    AI_PROVIDER: str = "openrouter"
    OPENROUTER_TEXT_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_IMAGE_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_AUDIO_MODEL: str = "google/gemini-2.5-flash"
    AI_REQUEST_TIMEOUT_SECONDS: float = 30.0
    AI_MAX_RETRIES: int = 1

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
