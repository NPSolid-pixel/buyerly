import asyncio

from core.config import settings
from core.runtime import configure_logging
from database.db import ensure_bootstrap_admin, ensure_default_settings, init_schema
from database.migrate_sqlite import migrate_sqlite_to_postgres


async def main() -> None:
    logger = configure_logging("database-migration")
    logger.info("Preparing Buyerly database schema")
    await init_schema()
    await migrate_sqlite_to_postgres(settings.LEGACY_SQLITE_PATH)
    await ensure_bootstrap_admin()
    await ensure_default_settings()
    logger.info("Buyerly database is ready")


if __name__ == "__main__":
    asyncio.run(main())
