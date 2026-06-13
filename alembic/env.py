import asyncio
from logging.config import fileConfig
import os
import sys

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Dynamically prepend the project root to sys.path to prevent ModuleNotFoundError
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import settings and model Base
from app.core.config import settings
from app.db.session import get_async_database_url
from app.models import Base

# Alembic configuration context
config = context.config

# Setup logging automatically from config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Dynamically override the static ini database URL with environment settings.DATABASE_URL
# This is absolutely vital for Railway deployments to function.
db_url = get_async_database_url(settings.DATABASE_URL)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Runs migrations synchronously inside the active connection context."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """
    # Build engine configuration from properties section
    sec = config.get_section(config.config_ini_section, {})

    # NullPool is used for migrations to prevent long-running connections from lingering
    connectable = async_engine_from_config(
        sec,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
