import logging
import sys

from app.core.config import settings


def setup_logging() -> None:
    """Configures the application's logging structure.

    Ensures that logging levels are aligned with settings and format is clean,
    well-structured, and highly visible in stdout for Railway deployment.
    """
    log_level_str = settings.LOG_LEVEL.lower()
    if log_level_str == "debug":
        level = logging.DEBUG
    elif log_level_str == "warning":
        level = logging.WARNING
    elif log_level_str == "error":
        level = logging.ERROR
    else:
        level = logging.INFO

    # Concise standard format: dynamic, clear, and perfectly parsed by modern log ingestion
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s:%(funcName)s:%(lineno)d] - %(message)s"

    logging.basicConfig(
        level=level,
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,  # Force override of root handlers
    )

    # Silence high-frequency background loggers
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)

    logger = logging.getLogger("app")
    logger.info(f"Logging system initialized with level: {logging.getLevelName(level)}")
