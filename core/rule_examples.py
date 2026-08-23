import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.metrics import validate_runtime_rule
from database.models import (
    RuleExamplesBootstrap,
    RuleGroup,
    RuleGroupItem,
    RulePreset,
    User,
)


RULE_EXAMPLES_VERSION = 1

EXAMPLE_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "key": "stop_no_leads",
        "name": "Пример · Стоп без лидов после 20",
        "action": "turn_off",
        "conditions": [
            {"metric": "spend", "operator": "gte", "value": 20, "time_window": "today"},
            {"metric": "leads", "operator": "eq", "value": 0, "time_window": "today"},
        ],
        "condition_logic": "and",
        "cooldown_minutes": 60,
        "check_interval_minutes": 5,
        "notify_tg": True,
    },
    {
        "key": "stop_expensive_lead",
        "name": "Пример · Стоп дорогого лида выше 12",
        "action": "turn_off",
        "conditions": [
            {"metric": "cpl", "operator": "gt", "value": 12, "time_window": "today"},
            {"metric": "leads", "operator": "gte", "value": 2, "time_window": "today"},
        ],
        "condition_logic": "and",
        "cooldown_minutes": 120,
        "check_interval_minutes": 5,
        "notify_tg": True,
    },
    {
        "key": "notify_expensive_registration",
        "name": "Пример · Алерт CPReg выше 25",
        "action": "notify_only",
        "conditions": [
            {"metric": "cpreg", "operator": "gt", "value": 25, "time_window": "today"},
            {"metric": "registrations", "operator": "gte", "value": 1, "time_window": "today"},
        ],
        "condition_logic": "and",
        "cooldown_minutes": 120,
        "check_interval_minutes": 10,
        "notify_tg": True,
    },
    {
        "key": "scale_purchases",
        "name": "Пример · +20% при выгодных покупках",
        "action": "increase_budget",
        "conditions": [
            {"metric": "purchases", "operator": "gte", "value": 2, "time_window": "today"},
            {"metric": "cpp", "operator": "lte", "value": 20, "time_window": "today"},
        ],
        "condition_logic": "and",
        "cooldown_minutes": 360,
        "check_interval_minutes": 15,
        "notify_tg": True,
        "budget_change_percent": 20,
        "budget_max_daily": 300,
    },
    {
        "key": "reduce_weak_traffic",
        "name": "Пример · −20% при слабом CTR",
        "action": "decrease_budget",
        "conditions": [
            {"metric": "spend", "operator": "gte", "value": 100, "time_window": "today"},
            {"metric": "ctr", "operator": "lt", "value": 1, "time_window": "today"},
        ],
        "condition_logic": "and",
        "cooldown_minutes": 180,
        "check_interval_minutes": 15,
        "notify_tg": True,
        "budget_change_percent": 20,
    },
    {
        "key": "reactivate_late_purchase",
        "name": "Пример · Включить при долетевшей покупке",
        "action": "turn_on",
        "conditions": [
            {"metric": "purchases", "operator": "gte", "value": 1, "time_window": "today"},
        ],
        "condition_logic": "and",
        "cooldown_minutes": 1440,
        "check_interval_minutes": 15,
        "notify_tg": True,
    },
)

EXAMPLE_GROUPS = (
    {
        "name": "Пример · Контроль запуска",
        "description": (
            "Стоп без лидов, уведомление о дорогой регистрации и снижение бюджета "
            "при слабом CTR. Пороги считаются в валюте каждого кабинета."
        ),
        "preset_keys": (
            "stop_no_leads",
            "notify_expensive_registration",
            "reduce_weak_traffic",
        ),
    },
    {
        "name": "Пример · Контроль и масштабирование",
        "description": (
            "Остановка дорогого лида, безопасное увеличение бюджета на покупках "
            "и включение после долетевшей покупки."
        ),
        "preset_keys": (
            "stop_expensive_lead",
            "scale_purchases",
            "reactivate_late_purchase",
        ),
    },
)


def _runtime_payload(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": definition["action"],
        "conditions": definition["conditions"],
        "logic": definition.get("condition_logic", "and"),
        "cooldown_minutes": definition.get("cooldown_minutes", 0),
        "check_interval": definition.get("check_interval_minutes", 5),
        "notify_tg": definition.get("notify_tg", True),
        "budget_change_percent": definition.get("budget_change_percent", 0),
        "budget_max_daily": definition.get("budget_max_daily", 0),
    }


async def ensure_rule_examples(session, user: User) -> bool:
    """Create safe, unassigned examples once; deletion remains permanent."""

    existing = (
        await session.execute(
            select(RuleExamplesBootstrap).where(
                RuleExamplesBootstrap.owner_user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    marker = RuleExamplesBootstrap(
        owner_user_id=user.id,
        version=RULE_EXAMPLES_VERSION,
    )
    session.add(marker)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return False

    presets_by_key: dict[str, RulePreset] = {}
    for definition in EXAMPLE_PRESETS:
        validate_runtime_rule(_runtime_payload(definition))
        preset = RulePreset(
            workspace_id=user.active_workspace_id,
            owner_user_id=user.id,
            name=definition["name"],
            action=definition["action"],
            conditions=definition["conditions"],
            condition_logic=definition.get("condition_logic", "and"),
            cooldown_minutes=definition.get("cooldown_minutes", 0),
            check_interval_minutes=definition.get("check_interval_minutes", 5),
            notify_tg=definition.get("notify_tg", True),
            budget_change_percent=definition.get("budget_change_percent", 0),
            budget_max_daily=definition.get("budget_max_daily", 0),
        )
        session.add(preset)
        presets_by_key[definition["key"]] = preset
    await session.flush()

    for group_definition in EXAMPLE_GROUPS:
        group = RuleGroup(
            workspace_id=user.active_workspace_id,
            owner_user_id=user.id,
            name=group_definition["name"],
            description=group_definition["description"],
        )
        session.add(group)
        await session.flush()
        for position, preset_key in enumerate(group_definition["preset_keys"]):
            session.add(
                RuleGroupItem(
                    group_id=group.id,
                    preset_id=presets_by_key[preset_key].id,
                    position=position,
                )
            )

    await session.commit()
    return True
