from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import pool

# Import every module's models (for side effects) so Alembic autogenerate sees the
# full schema -- every per-module Base shares one registry/MetaData
# (trading-platform#35/#103), so this registers all model classes against the same
# shared_registry.metadata used below as target_metadata.
import trading.broker.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.candles.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.execution.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.monitoring.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.risk.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.storage.cache.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.strategy.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.tick_ingest.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from alembic import context
from trading.core.db_registry import shared_registry

# Load .env from the project root (one level above the alembic/ directory).
load_dotenv(Path(__file__).parent.parent / ".env")

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = shared_registry.metadata


def _get_url() -> str:
    """
    Read database URL from the POSTGRES_URL environment variable.

    This keeps credentials out of alembic.ini and works the same way
    both locally (via .env) and in Docker (via container env vars).
    The URL must use the ``postgresql+asyncpg://`` scheme for asyncpg,
    but Alembic's sync runner needs ``postgresql://`` — we swap the
    driver prefix here so the same env var works for both.
    """
    url = os.environ["POSTGRES_URL"]
    # Alembic uses a sync connection for migrations; strip the asyncpg driver.
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without connecting)."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode via asyncpg."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url().replace("postgresql://", "postgresql+psycopg2://", 1)

    # Use a simple sync engine for migrations — asyncpg is not needed here.
    from sqlalchemy import create_engine

    sync_url = _get_url()
    engine = create_engine(sync_url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        do_run_migrations(conn)
    engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Use synchronous psycopg2 path — Alembic doesn't need async here.
    from sqlalchemy import create_engine

    engine = create_engine(_get_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        do_run_migrations(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
