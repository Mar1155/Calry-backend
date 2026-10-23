import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.public.legal import router as legal_router
from app.api.v1.routes.admin import router as admin_router
from app.api.v1.routes.admin_scans import router as admin_scans_router
from app.api.v1.routes.awareness import router as awareness_router
from app.api.v1.routes.burned_calories import router as burned_router
from app.api.v1.routes.food_memory import router as food_memory_router
from app.api.v1.routes.habits import router as habits_router
from app.api.v1.routes.insights import router as insights_router
from app.api.v1.routes.meal_completion import router as meal_completion_router
from app.api.v1.routes.meals import router as meals_router
from app.api.v1.routes.memory import router as memory_router
from app.api.v1.routes.onboarding import router as onboarding_router
from app.api.v1.routes.premium import router as premium_router
from app.api.v1.routes.revenuecat_webhook import router as webhook_router
from app.api.v1.routes.summaries import router as summaries_router
from app.api.v1.routes.system import router as system_router
from app.api.v1.routes.users import router as users_router
from app.core.config import settings
from app.core.exceptions import CalryException
from app.dependencies.app_version import enforce_min_app_version
from app.core.logging import setup_logging

logger = logging.getLogger("app.main")

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        send_default_pii=False,
        enable_logs=True,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profile_session_sample_rate=settings.SENTRY_PROFILE_SESSION_SAMPLE_RATE,
        profile_lifecycle="trace",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages application startup and shutdown hooks.

    Initializes essential logging layers and prints environment configurations.
    """
    setup_logging()
    logger.info("Initializing Calry Calorie Tracker API...")
    logger.info(f"Active Environment Target: {settings.ENVIRONMENT}")
    logger.info(f"Active Default AI Provider: {settings.DEFAULT_AI_PROVIDER.upper()}")
    yield
    logger.info("Shutting down Calry Calorie Tracker API...")
    # Release the pooled OpenRouter HTTP client (C7).
    from app.ai.providers.openrouter import close_shared_client

    await close_shared_client()


app = FastAPI(
    title="Calry Calorie Tracker API",
    description="Scalable, AI-first mobile backend foundation built for Calry.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configure CORS Middleware.
# Origins come from settings.ALLOWED_ORIGINS (default "*" for local/mobile dev).
# The CORS spec forbids credentials with a "*" origin, so credentials are only
# enabled when an explicit origin list is configured (e.g. in production).
_cors_origins = settings.cors_origins
_allow_all_origins = _cors_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SelectiveGZipMiddleware:
    """GZip JSON responses, but never NDJSON streams or binary media.

    Starlette's GZipMiddleware only exempts ``text/event-stream``. Compressing the
    ``/stream`` meal analysis routes would buffer events and break incremental
    delivery; photos and audio are already compressed.
    """

    _SKIP_SUFFIXES = ("/stream", "/media")
    _SKIP_PREFIXES = ("/static",)

    def __init__(self, app: ASGIApp, minimum_size: int = 1024, compresslevel: int = 6) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path or path.endswith(self._SKIP_SUFFIXES) or path.startswith(self._SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        await self.gzip(scope, receive, send)


app.add_middleware(SelectiveGZipMiddleware)


# Mount static files directory
# Required for serving local uploads (images/voice recordings) in offline development
static_dir = Path("app/static")
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "request method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
        )


# Global custom exception handler for CalryException
@app.exception_handler(CalryException)
async def calry_exception_handler(request: Request, exc: CalryException) -> JSONResponse:
    logger.warning(f"Application Business Exception: [{exc.error_code}] - {exc.message}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
        },
    )


# Global validation exception handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning(f"Request validation failure on endpoint: {request.url.path}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error_code": "VALIDATION_FAILED",
            "message": "Input validation failed. Please check payload parameters.",
            "details": jsonable_encoder(exc.errors(), custom_encoder={Exception: str}),
        },
    )


# Fallback global exception handler
@app.exception_handler(Exception)
async def fallback_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled critical system error: {exc}")
    detail = {"error": str(exc)} if not settings.is_production else {}
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error_code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected server-side error occurred.",
            "details": detail,
        },
    )


# Register Versioned API Routes under namespace /api/v1
app.include_router(system_router, prefix="/api/v1", tags=["System"])
app.include_router(users_router, prefix="/api/v1/users", tags=["Users"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(onboarding_router, prefix="/api/v1/onboarding", tags=["Onboarding"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(meals_router, prefix="/api/v1/meals", tags=["Meals"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(meal_completion_router, prefix="/api/v1/meals", tags=["Meal Completion"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(burned_router, prefix="/api/v1/burned-calories", tags=["Burned Calories"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(summaries_router, prefix="/api/v1/summary", tags=["Summaries"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(insights_router, prefix="/api/v1", tags=["Insights"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(memory_router, prefix="/api/v1/memory", tags=["Memory"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(premium_router, prefix="/api/v1/premium", tags=["Premium"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(webhook_router, prefix="/api/v1/webhooks", tags=["Webhooks"])
app.include_router(food_memory_router, prefix="/api/v1/food-memory", tags=["Food Memory"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(habits_router, prefix="/api/v1/habits", tags=["Habits"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(awareness_router, prefix="/api/v1/awareness", tags=["Awareness"], dependencies=[Depends(enforce_min_app_version)])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(admin_scans_router, prefix="/api/v1/admin", tags=["Admin Scans"])

# Public HTTPS pages used by store metadata and RevenueCat paywalls.
app.include_router(legal_router, tags=["Legal"])


@app.get("/", tags=["Root"])
async def read_root() -> dict:
    """Standard welcome landing greeting directing developers to docs."""
    return {
        "project": "Calry AI-First Calorie Tracker Backend",
        "status": "online",
        "documentation": "/docs",
        "healthcheck": "/api/v1/health",
    }
