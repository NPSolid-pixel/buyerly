import asyncio

from core.runtime import configure_logging
from database.db import (
    ensure_bootstrap_admin,
    ensure_default_settings,
)
from database.migrations import run_production_migrations


async def main() -> None:
    logger = configure_logging("database-migration")
    logger.info("Applying Buyerly Alembic migrations")
    await run_production_migrations()
    await ensure_bootstrap_admin()
    await ensure_default_settings()
    logger.info("Buyerly database is ready")


if __name__ == "__main__":
    asyncio.run(main())
