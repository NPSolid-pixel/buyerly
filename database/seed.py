import asyncio
from sqlalchemy import select
from database.db import init_db, async_session_maker
from database.models import Preset

DEFAULT_PRESETS = [
    {
        "name": "Германия 5-5-1 (Лимиты $2 / $6)",
        "currency": "USD",
        "max_spend_0_leads": 2.0,
        "max_spend_1_lead": 6.0,
        "max_cpa_multiple_leads": 6.0,
        "auto_reactivate": False
    },
    {
        "name": "Швеция 5-5-1 (Лимиты $2 / $6)",
        "currency": "USD",
        "max_spend_0_leads": 2.0,
        "max_spend_1_lead": 6.0,
        "max_cpa_multiple_leads": 6.0,
        "auto_reactivate": False
    },
    {
        "name": "Тестовый 5-5-1 (Лимиты $1 / $3)",
        "currency": "USD",
        "max_spend_0_leads": 1.0,
        "max_spend_1_lead": 3.0,
        "max_cpa_multiple_leads": 3.0,
        "auto_reactivate": False
    }
]

async def seed_defaults():
    await init_db()
    async with async_session_maker() as session:
        for p_data in DEFAULT_PRESETS:
            stmt = select(Preset).where(Preset.name == p_data["name"])
            res = await session.execute(stmt)
            existing = res.scalar_one_or_none()
            if not existing:
                preset = Preset(**p_data)
                session.add(preset)
                print(f"Added preset: {p_data['name']}")
        await session.commit()
    print("Database initialized and seeded successfully.")

if __name__ == "__main__":
    asyncio.run(seed_defaults())
