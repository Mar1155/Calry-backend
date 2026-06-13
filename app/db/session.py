import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger("app.db")


def get_async_database_url(url: str) -> str:
    """Transforms standard database URLs to secure async-compliant versions.

    Railway and other cloud providers often provide 'postgres://' or 'postgresql://' URLs.
    This function converts them to SQLAlchemy 'postgresql+asyncpg://' or supports sqlite
    testing URLs.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


db_url = get_async_database_url(settings.DATABASE_URL)
logger.info(f"Initializing DB connection with: {db_url.split('@')[-1] if '@' in db_url else db_url}")

# Build async engine options optimal for production usage
engine_options = {
    "echo": settings.LOG_LEVEL.lower() == "debug",
}

# Apply connection pool parameters for production PostgreSQL setups
if db_url.startswith("postgresql"):
    engine_options.update(
        {
            "pool_size": 20,
            "max_overflow": 10,
            "pool_pre_ping": True,  # Auto-checks connections before reusing
            "pool_recycle": 1800,  # Recycles connections every 30 minutes
        }
    )

engine = create_async_engine(db_url, **engine_options)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)
