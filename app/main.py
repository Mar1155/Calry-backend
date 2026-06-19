import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.routes.awareness import router as awareness_router
from app.api.v1.routes.burned_calories import router as burned_router
from app.api.v1.routes.food_memory import router as food_memory_router
from app.api.v1.routes.habits import router as habits_router
from app.api.v1.routes.insights import router as insights_router
from app.api.v1.routes.meals import router as meals_router
from app.api.v1.routes.premium import router as premium_router
from app.api.v1.routes.revenuecat_webhook import router as webhook_router
from app.api.v1.routes.summaries import router as summaries_router
from app.api.v1.routes.system import router as system_router
from app.api.v1.routes.users import router as users_router
from app.api.v1.routes.meal_completion import router as meal_completion_router
from app.core.config import settings
from app.core.exceptions import CalryException
from app.core.logging import setup_logging

logger = logging.getLogger("app.main")


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


app = FastAPI(
    title="Calry Calorie Tracker API",
    description="Scalable, AI-first mobile backend foundation built for Calry.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configure CORS Middleware
# Essential for testing APIs via Flutter web debuggers or local networks
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permits all origins for easy mobile local host routing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Mount static files directory
# Required for serving local uploads (images/voice recordings) in offline development
static_dir = Path("app/static")
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


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
            "details": exc.errors(),
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
app.include_router(users_router, prefix="/api/v1/users", tags=["Users"])
app.include_router(meals_router, prefix="/api/v1/meals", tags=["Meals"])
app.include_router(meal_completion_router, prefix="/api/v1/meals", tags=["Meal Completion"])
app.include_router(burned_router, prefix="/api/v1/burned-calories", tags=["Burned Calories"])
app.include_router(summaries_router, prefix="/api/v1/summary", tags=["Summaries"])
app.include_router(insights_router, prefix="/api/v1", tags=["Insights"])
app.include_router(premium_router, prefix="/api/v1/premium", tags=["Premium"])
app.include_router(webhook_router, prefix="/api/v1/webhooks", tags=["Webhooks"])
app.include_router(food_memory_router, prefix="/api/v1/food-memory", tags=["Food Memory"])
app.include_router(habits_router, prefix="/api/v1/habits", tags=["Habits"])
app.include_router(awareness_router, prefix="/api/v1/awareness", tags=["Awareness"])


@app.get("/", tags=["Root"])
async def read_root() -> dict:
    """Standard welcome landing greeting directing developers to docs."""
    return {
        "project": "Calry AI-First Calorie Tracker Backend",
        "status": "online",
        "documentation": "/docs",
        "healthcheck": "/api/v1/health",
    }
