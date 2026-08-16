import asyncio
from sqlalchemy import select
from database.db import init_db, async_session_maker
from database.models import RulePreset

DEFAULT_PRESETS = [
    {
        "name": "Стоп при высоком CPL ($10+)",
        "action": "turn_off",
        "conditions": '[{"metric": "cpl", "operator": "gte", "value": 10.0, "time_window": "today"}]',
        "condition_logic": "and",
        "notify_tg": True
    },
    {
        "name": "Увеличить бюджет при CPL < $5",
        "action": "increase_budget",
        "conditions": '[{"metric": "cpl", "operator": "lt", "value": 5.0, "time_window": "today"}, {"metric": "leads", "operator": "gte", "value": 3.0, "time_window": "today"}]',
        "condition_logic": "and",
        "budget_change_percent": 20.0,
        "budget_max_daily": 500.0,
        "notify_tg": True
    },
    {
        "name": "Алерт без лидов при спенде $15+",
        "action": "notify_only",
        "conditions": '[{"metric": "spend", "operator": "gte", "value": 15.0, "time_window": "today"}, {"metric": "leads", "operator": "eq", "value": 0.0, "time_window": "today"}]',
        "condition_logic": "and",
        "notify_tg": True
    }
]

async def seed_defaults():
    await init_db()
    async with async_session_maker() as session:
        for p_data in DEFAULT_PRESETS:
            stmt = select(RulePreset).where(RulePreset.name == p_data["name"])
            res = await session.execute(stmt)
            existing = res.scalar_one_or_none()
            if not existing:
                preset = RulePreset(
                    owner_id="system",
                    **p_data
                )
                session.add(preset)
                print(f"Added preset: {p_data['name']}")
        await session.commit()
    print("Database initialized and seeded successfully.")

if __name__ == "__main__":
    asyncio.run(seed_defaults())
