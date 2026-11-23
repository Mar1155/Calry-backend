import asyncio
import datetime as dt
import os
from collections.abc import AsyncGenerator

# Set environment to testing before importing app modules
os.environ["ENVIRONMENT"] = "testing"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.sqlite.aiosqlite import SQLiteDialect_aiosqlite
from sqlalchemy.dialects.sqlite.base import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.dependencies.db import get_db
from app.main import app
from app.models.base import Base

# In-memory SQLite for high-speed, zero-config local testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


class _UTCAwareSQLiteDateTime(SQLITE_DATETIME):
    """Make SQLite behave like Postgres for ``DateTime(timezone=True)``.

    Postgres returns timezone-aware datetimes for those columns; SQLite has no
    timezone type and hands back naive values. Code that compares a stored
    timestamp with ``datetime.now(UTC)`` then fails only under tests. Values
    are stored in UTC, so naive results are tagged UTC on the way out.
    """

    def result_processor(self, dialect, coltype):
        process = super().result_processor(dialect, coltype)
        if not self.timezone:
            return process

        def to_aware(value):
            if process is not None:
                value = process(value)
            if isinstance(value, dt.datetime) and value.tzinfo is None:
                value = value.replace(tzinfo=dt.UTC)
            return value

        return to_aware


SQLiteDialect_aiosqlite.colspecs = {
    **SQLiteDialect_aiosqlite.colspecs,
    sqltypes.DateTime: _UTCAwareSQLiteDateTime,
}


@pytest.fixture(scope="session")
def event_loop():
    """Generates a dedicated event loop for running asynchronous test cases."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_engine():
    """Initializes the SQLite test engine and creates all database tables."""
    engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Creates a distinct async DB session, auto-rolling back updates after each test."""
    TestingSessionLocal = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with TestingSessionLocal() as session:
        yield session
        # Rollback guarantees complete isolation between tests
        await session.rollback()


@pytest.fixture(autouse=True)
def _reset_app_version_cache():
    from app.services.app_version_service import invalidate_policy_cache

    invalidate_policy_cache()
    yield
    invalidate_policy_cache()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Creates an async HTTPX client with database dependency overrides."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    # Cleanup overrides
    app.dependency_overrides.clear()
