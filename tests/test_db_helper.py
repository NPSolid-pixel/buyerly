import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from database.db import Base


def get_test_db_url() -> str:
    return os.getenv(
        "TEST_DATABASE_URL",
        os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://buyerly:buyerly_secret@localhost:5432/buyerly_test",
        ),
    )


def create_test_engine():
    return create_async_engine(get_test_db_url(), echo=False)


async def init_test_db(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
