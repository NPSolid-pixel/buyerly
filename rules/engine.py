import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List
from database.models import Account
from core.currency import format_money, normalize_currency
from core.metrics import (
    compare_metric,
    cost_per_event,
    rule_metric_reading,
    validate_runtime_rule,
)

class RuleAction(str, Enum):
    NOOP = "NOOP"                             # Всё в норме
    STOP = "STOP"                             # Остановить адсет (PAUSE)
    NOTIFY_ONLY = "NOTIFY_ONLY"               # Только уведомить в TG (без выключения в Meta)
    PROPOSE_REACTIVATE = "PROPOSE_REACTIVATE" # Предложить включить обратно (кнопка в TG)
    AUTO_REACTIVATE = "AUTO_REACTIVATE"       # Автоматически включить обратно (ACTIVE)
    INCREASE_BUDGET = "INCREASE_BUDGET"       # Увеличить дневной бюджет адсета на N%
    DECREASE_BUDGET = "DECREASE_BUDGET"       # Уменьшить дневной бюджет адсета на N%

@dataclass
class RuleEvaluationResult:
    action: RuleAction
    adset_id: str
    adset_name: str
    spend: float
    leads: int
    registrations: int
    purchases: int
    cpl: Optional[float]
    cpreg: Optional[float]
    cpp: Optional[float]
    reason: str
    budget_change_percent: float = 0.0
    budget_max_daily: float = 0.0
    cooldown_minutes: int = 0
    notify_tg: bool = True
    rule_id: Optional[int] = None
    rule_name: str = ""
    conditions_snapshot: List[Dict[str, Any]] = field(default_factory=list)
    currency: str = "UNKNOWN"

class RuleEngine:
    """
    Движок правил с поддержкой динамических условий (Spend, CPL, CPReg, CPP,
    Leads, Registrations, Purchases, CTR, CPC),
    логики AND/OR, действий управления бюджетом и множественных временных окон.
    """

    @staticmethod
    def _eval_condition(metric_val: float, operator: str, target_val: float) -> bool:
        """Backward-compatible helper for callers outside the main evaluator."""
        from core.metrics import MetricReading

        return compare_metric(
            MetricReading(key="value", label="Значение", unit="", value=metric_val),
            operator,
            target_val,
        )

    @staticmethod
    def _get_metric_value(metric: str, adset_data: Dict[str, Any]) -> tuple:
        """Возвращает (значение метрики, читаемое название, единица измерения)."""
        reading = rule_metric_reading(metric, adset_data)
        return reading.value, reading.label, reading.unit

    @staticmethod
    def evaluate(
        adset: Dict[str, Any],
        account: Account,
        insights_by_window: Optional[Dict[str, Dict[str, Any]]] = None,
        active_rules_override: Optional[List[Dict[str, Any]]] = None,
    ) -> RuleEvaluationResult:
        """
        Оценивает адсет по пользовательским правилам с поддержкой AND/OR логики и временных окон.
        Поддерживает множественные правила на один кабинет с разрешением конфликтов.
        """
        adset_id = str(adset["adset_id"])
        adset_name = str(adset["adset_name"])
        status = adset.get("status", "UNKNOWN")
        effective_status = adset.get("effective_status", status)
        spend = float(adset.get("spend", 0.0))
        leads = int(adset.get("leads", 0))
        registrations = int(adset.get("registrations", 0))
        purchases = int(adset.get("purchases", 0))
        cpl = cost_per_event(spend, leads)
        cpreg = cost_per_event(spend, registrations)
        cpp = cost_per_event(spend, purchases)
        is_active = status == "ACTIVE" and effective_status == "ACTIVE"
        currency = normalize_currency(getattr(account, "currency", "UNKNOWN"))

        def noop(reason="Метрики в пределах нормы."):
            return RuleEvaluationResult(
                action=RuleAction.NOOP,
                adset_id=adset_id,
                adset_name=adset_name,
                spend=spend,
                leads=leads,
                registrations=registrations,
                purchases=purchases,
                cpl=cpl,
                cpreg=cpreg,
                cpp=cpp,
                reason=reason,
                cooldown_minutes=0,
                notify_tg=False,
                currency=currency,
            )

        if not getattr(account, "rules_enabled", False):
            return noop("Правила выключены для этого кабинета.")

        if active_rules_override is not None:
            active_rules = active_rules_override
        else:
            raw_rules = getattr(account, "active_rules", "[]")
            try:
                active_rules = json.loads(raw_rules) if isinstance(raw_rules, str) else raw_rules
            except Exception:
                active_rules = []

        if not active_rules or not isinstance(active_rules, list) or len(active_rules) == 0:
            return noop("Правила не настроены.")

        def get_action_priority(action: RuleAction) -> int:
            priorities = {
                RuleAction.STOP: 100,
                RuleAction.DECREASE_BUDGET: 90,
                RuleAction.INCREASE_BUDGET: 80,
                RuleAction.AUTO_REACTIVATE: 70,
                RuleAction.PROPOSE_REACTIVATE: 60,
                RuleAction.NOTIFY_ONLY: 50,
                RuleAction.NOOP: 0
            }
            return priorities.get(action, 0)
            
        action_map = {
            "turn_off": RuleAction.STOP,
            "notify_only": RuleAction.NOTIFY_ONLY,
            "turn_on": RuleAction.AUTO_REACTIVATE,
            "increase_budget": RuleAction.INCREASE_BUDGET,
            "decrease_budget": RuleAction.DECREASE_BUDGET,
        }

        triggered_actions = []
        invalid_rule_seen = False
        funnel_guarded = False

        for rule in active_rules:
            if rule.get("enabled", True) is False or rule.get("needs_review", False) is True:
                continue
            try:
                validate_runtime_rule(rule)
            except (TypeError, ValueError):
                invalid_rule_seen = True
                continue
            action_type = rule.get("action")
            if action_type == "turn_on":
                if status != "PAUSED":
                    continue
            elif not is_active:
                continue

            conditions = rule.get("conditions", [])
            if not conditions:
                continue
                
            condition_logic = rule.get("logic", "and")
            
            matched_reasons = []
            any_match = False
            all_match = True

            for cond in conditions:
                metric = cond.get("metric", "spend")
                operator = cond.get("operator", "gte")
                target_val = float(cond.get("value", 0.0))
                time_window = cond.get("time_window", "today")

                if time_window != "today" and insights_by_window and time_window in insights_by_window:
                    source_data = insights_by_window[time_window]
                else:
                    source_data = adset

                reading = rule_metric_reading(metric, source_data)
                metric_val, metric_name, unit = reading.value, reading.label, reading.unit

                op_symbol = {
                    "gt": ">",
                    "gte": "≥",
                    "lt": "<",
                    "lte": "≤",
                    "eq": "=",
                }.get(operator, operator)
                
                if metric_val is None:
                    all_match = False
                    continue
                if unit == "currency":
                    val_fmt = format_money(metric_val, currency)
                    tgt_fmt = format_money(target_val, currency)
                elif unit == "%":
                    val_fmt = f"{metric_val:.2f}%"
                    tgt_fmt = f"{target_val:.2f}%"
                else:
                    val_fmt = f"{int(metric_val)}"
                    tgt_fmt = f"{int(target_val)}"

                window_label = ""
                if time_window != "today":
                    window_labels = {"yesterday": "Вчера", "last_3d": "3 дня", "last_7d": "7 дней"}
                    window_label = f" [{window_labels.get(time_window, time_window)}]"

                matches = compare_metric(reading, operator, target_val)

                if matches:
                    any_match = True
                    matched_reasons.append(f"{metric_name}{window_label} ({val_fmt}) {op_symbol} {tgt_fmt}")
                else:
                    all_match = False

            triggered = any_match if condition_logic == "or" else all_match

            if triggered:
                rule_action = action_map.get(action_type)
                if rule_action is None:
                    invalid_rule_seen = True
                    continue

                # Funnel protection is deliberately enforced below the rule
                # builder so it also covers legacy and already attached rules.
                # A deeper conversion always wins over every STOP condition.
                if rule_action == RuleAction.STOP and (registrations > 0 or purchases > 0):
                    funnel_guarded = True
                    continue
                rule_name = rule.get("name", "Unknown Rule")
                reason_str = f"[{rule_name}] " + ", ".join(matched_reasons)
                
                triggered_actions.append({
                    "action": rule_action,
                    "reason": reason_str,
                    "budget_change": float(rule.get("budget_change_percent", 0.0)),
                    "budget_max": float(rule.get("budget_max_daily", 0.0)),
                    "cooldown_minutes": int(rule.get("cooldown_minutes", 0)),
                    "notify_tg": rule.get("notify_tg", True),
                    "rule_id": rule.get("preset_id"),
                    "rule_name": rule_name,
                    "conditions": conditions,
                    "priority": get_action_priority(rule_action)
                })

        if not triggered_actions:
            if funnel_guarded:
                return noop(
                    "Защита воронки: STOP пропущен, потому что Meta показывает "
                    f"регистрации ({registrations}) или покупки ({purchases})."
                )
            return noop(
                "Правила не настроены или некорректны; действия в Meta пропущены."
                if invalid_rule_seen
                else "Метрики в пределах нормы."
            )

        # Sort by priority descending
        triggered_actions.sort(key=lambda x: x["priority"], reverse=True)
        
        highest_priority_action = triggered_actions[0]
        combined_reason = " | ".join(t["reason"] for t in triggered_actions)

        return RuleEvaluationResult(
            action=highest_priority_action["action"],
            adset_id=adset_id,
            adset_name=adset_name,
            spend=spend,
            leads=leads,
            registrations=registrations,
            purchases=purchases,
            cpl=cpl,
            cpreg=cpreg,
            cpp=cpp,
            reason=combined_reason,
            budget_change_percent=highest_priority_action["budget_change"],
            budget_max_daily=highest_priority_action["budget_max"],
            cooldown_minutes=highest_priority_action["cooldown_minutes"],
            notify_tg=highest_priority_action["notify_tg"],
            rule_id=highest_priority_action["rule_id"],
            rule_name=highest_priority_action["rule_name"],
            conditions_snapshot=highest_priority_action["conditions"],
            currency=currency,
        )
