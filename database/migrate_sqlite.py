"""One-time, transactional migration from Buyerly SQLite to PostgreSQL."""

import logging
import os
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, func, select, text

from database.db import Base, engine
import database.models  # noqa: F401  Registers all model tables.

logger = logging.getLogger(__name__)


def _source_rows(source: sqlite3.Connection, table) -> list[dict]:
    table_exists = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table.name,),
    ).fetchone()
    if not table_exists:
        return []

    source_columns = {
        row[1] for row in source.execute(f'PRAGMA table_info("{table.name}")')
    }
    columns = [column for column in table.columns if column.name in source_columns]
    if not columns:
        return []

    names = ", ".join(f'"{column.name}"' for column in columns)
    rows = source.execute(f'SELECT {names} FROM "{table.name}"').fetchall()
    converted = []
    for row in rows:
        values = {}
        for column, value in zip(columns, row):
            if value is not None and isinstance(column.type, Boolean):
                value = bool(value)
            elif value is not None and isinstance(column.type, DateTime):
                if isinstance(value, str):
                    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if value.tzinfo is not None:
                    value = value.astimezone(timezone.utc).replace(tzinfo=None)
            values[column.name] = value
        converted.append(values)
    return converted


async def migrate_sqlite_to_postgres(source_path: str) -> dict[str, int]:
    """Copy an existing SQLite database only when PostgreSQL is still empty."""

    if engine.dialect.name != "postgresql":
        logger.info("Legacy migration skipped: target is not PostgreSQL")
        return {}
    if not source_path or not os.path.isfile(source_path):
        logger.info("Legacy migration skipped: SQLite source is absent")
        return {}

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    copied: dict[str, int] = {}
    try:
        async with engine.begin() as conn:
            target_rows = 0
            for table in Base.metadata.sorted_tables:
                target_rows += int(
                    (await conn.execute(select(func.count()).select_from(table))).scalar_one()
                )
            if target_rows:
                logger.info(
                    "Legacy migration skipped: PostgreSQL already contains %s rows",
                    target_rows,
                )
                return {}

            for table in Base.metadata.sorted_tables:
                rows = _source_rows(source, table)
                if rows:
                    await conn.execute(table.insert(), rows)
                copied[table.name] = len(rows)

            for table in Base.metadata.sorted_tables:
                if "id" not in table.c:
                    continue
                table_name = table.name.replace('"', '""')
                await conn.execute(
                    text(
                        "SELECT setval("
                        f"pg_get_serial_sequence('{table_name}', 'id'), "
                        f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM \"{table_name}\""
                    )
                )

            for table in Base.metadata.sorted_tables:
                actual = int(
                    (await conn.execute(select(func.count()).select_from(table))).scalar_one()
                )
                if actual != copied[table.name]:
                    raise RuntimeError(
                        f"Migration verification failed for {table.name}: "
                        f"expected {copied[table.name]}, got {actual}"
                    )
    finally:
        source.close()

    logger.info(
        "SQLite migration completed: %s rows across %s tables",
        sum(copied.values()),
        len(copied),
    )
    return copied
