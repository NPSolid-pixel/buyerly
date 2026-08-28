import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from core.config import settings
from database.db import Base


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
LEGACY_BASELINE_REVISION = "0009_web_sessions"
POST_BASELINE_COLUMNS = {
    "accounts": {
        "access_token_encrypted",
    },
    "email_verification_codes": {
        "code_hash",
        "purpose",
        "scope",
        "delivered_at",
    },
    "meta_oauth_states": {
        "reconnect_connection_id",
        "workspace_id",
    },
}


def alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


def _schema_snapshot(sync_connection) -> dict[str, set[str]]:
    inspector = inspect(sync_connection)
    return {
        table_name: {
            column["name"]
            for column in inspector.get_columns(table_name)
        }
        for table_name in inspector.get_table_names()
        if table_name != "alembic_version"
    }


def _contract_errors(
    snapshot: dict[str, set[str]],
    *,
    baseline: bool = False,
) -> list[str]:
    errors = []
    expected_tables = Base.metadata.tables
    for table_name, table in sorted(expected_tables.items()):
        actual_columns = snapshot.get(table_name)
        if actual_columns is None:
            errors.append(f"missing table {table_name}")
            continue
        expected_columns = set(table.columns.keys())
        if baseline:
            expected_columns -= POST_BASELINE_COLUMNS.get(table_name, set())
        missing_columns = sorted(expected_columns - actual_columns)
        if missing_columns:
            errors.append(
                f"missing columns {table_name}: {', '.join(missing_columns)}"
            )
    return errors


async def _read_schema_snapshot() -> dict[str, set[str]]:
    import database.models  # noqa: F401

    migration_engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
    )
    try:
        async with migration_engine.connect() as connection:
            return await connection.run_sync(_schema_snapshot)
    finally:
        await migration_engine.dispose()


async def _read_alembic_heads() -> set[str] | None:
    """Return current revisions, or ``None`` when the version table is absent."""
    migration_engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
    )
    try:
        async with migration_engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: (
                    set(
                        MigrationContext.configure(
                            sync_connection
                        ).get_current_heads()
                    )
                    if "alembic_version"
                    in inspect(sync_connection).get_table_names()
                    else None
                )
            )
    finally:
        await migration_engine.dispose()


async def _adopt_legacy_schema_if_needed(config: Config) -> None:
    current_heads = await _read_alembic_heads()
    if current_heads:
        return

    snapshot = await _read_schema_snapshot()
    if not snapshot:
        return

    errors = _contract_errors(snapshot, baseline=True)
    if errors:
        joined = "; ".join(errors[:20])
        raise RuntimeError(
            "Refusing to stamp an unversioned database with schema drift: "
            f"{joined}"
        )

    unexpected_future_columns = []
    for table_name, future_columns in POST_BASELINE_COLUMNS.items():
        present = sorted(snapshot.get(table_name, set()) & future_columns)
        if present:
            unexpected_future_columns.append(
                f"{table_name}: {', '.join(present)}"
            )
    if unexpected_future_columns:
        raise RuntimeError(
            "Refusing to stamp an unversioned database that already contains "
            "post-baseline columns: "
            + "; ".join(unexpected_future_columns)
        )

    # Buyerly production predates Alembic execution but already contains the
    # schema/data transformations through 0009. Stamp that explicit baseline;
    # every later revision is then applied normally by `upgrade head`. Treat an
    # existing but empty version table like a missing one: a failed/partial
    # adoption can create the table before its version row is committed.
    await asyncio.to_thread(command.stamp, config, LEGACY_BASELINE_REVISION)


async def _assert_database_at_head(config: Config) -> None:
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())

    migration_engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
    )
    try:
        async with migration_engine.connect() as connection:
            current_heads = set(
                await connection.run_sync(
                    lambda sync_connection: MigrationContext.configure(
                        sync_connection
                    ).get_current_heads()
                )
            )
    finally:
        await migration_engine.dispose()
    if current_heads != expected_heads:
        raise RuntimeError(
            "Alembic revision mismatch after upgrade: "
            f"database={sorted(current_heads)}, expected={sorted(expected_heads)}"
        )


async def _assert_schema_contract() -> None:
    errors = _contract_errors(await _read_schema_snapshot())
    if errors:
        raise RuntimeError(
            "Database schema does not match application models after Alembic: "
            + "; ".join(errors[:20])
        )


async def run_production_migrations() -> None:
    """Run the sole production schema path and fail closed on schema drift."""
    config = alembic_config()
    await _adopt_legacy_schema_if_needed(config)
    await asyncio.to_thread(command.upgrade, config, "head")
    await _assert_database_at_head(config)
    await _assert_schema_contract()
