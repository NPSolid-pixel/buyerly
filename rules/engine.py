import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List
from database.models import Account

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
    total_conversions: int
    cpa: float
    reason: str
    budget_change_percent: float = 0.0
    budget_max_daily: float = 0.0
    cooldown_minutes: int = 0
    notify_tg: bool = True

class RuleEngine:
    """
    Движок правил с поддержкой динамических условий (Спенд, CPL, CPR, CPA, Лиды, Реги, CTR, CPC),
    логики AND/OR, действий управления бюджетом и множественных временных окон.
    """

    @staticmethod
    def _eval_condition(metric_val: float, operator: str, target_val: float) -> bool:
        if operator in ("gte", "gt"):
            return metric_val >= (target_val - 0.001)
        elif operator in ("lte", "lt"):
            return metric_val <= (target_val + 0.001)
        elif operator == "eq":
            return abs(metric_val - target_val) < 0.01
        return False

    @staticmethod
    def _get_metric_value(metric: str, adset_data: Dict[str, Any]) -> tuple:
        """Возвращает (значение метрики, читаемое название, единица измерения)."""
        spend = float(adset_data.get("spend", 0.0))
        leads = int(adset_data.get("leads", 0))
        registrations = int(adset_data.get("registrations", 0))
        purchases = int(adset_data.get("purchases", 0))
        total_conversions = leads + registrations
        clicks = int(adset_data.get("clicks", 0))

        cpl = (spend / leads) if leads > 0 else spend
        cpr = (spend / registrations) if registrations > 0 else spend
        cpa = (spend / total_conversions) if total_conversions > 0 else 0.0
        cpc = float(adset_data.get("cpc", 0.0))
        ctr = float(adset_data.get("ctr", 0.0))

        metric_map = {
            "spend":         (spend, "Спенд", "$"),
            "cpl":           (cpl, "Цена за лид (CPL)", "$"),
            "cpr":           (cpr, "Цена за регу (CPR)", "$"),
            "cpa":           (cpa, "Цена за конверсию (CPA)", "$"),
            "leads":         (float(leads), "Лиды", ""),
            "registrations": (float(registrations), "Регистрации", ""),
            "purchases":     (float(purchases), "Покупки", ""),
            "ctr":           (ctr, "CTR", "%"),
            "cpc":           (cpc, "CPC", "$"),
        }
        val, name, unit = metric_map.get(metric, (0.0, metric, ""))
        return val, name, unit

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
        total_conversions = leads + registrations
        cpa = (spend / total_conversions) if total_conversions > 0 else 0.0
        is_active = status == "ACTIVE" or effective_status == "ACTIVE"

        def noop(reason="Метрики в пределах нормы."):
            return RuleEvaluationResult(
                action=RuleAction.NOOP,
                adset_id=adset_id,
                adset_name=adset_name,
                spend=spend,
                leads=leads,
                registrations=registrations,
                total_conversions=total_conversions,
                cpa=cpa,
                reason=reason,
                cooldown_minutes=0,
                notify_tg=False
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

        if not is_active:
            return noop("Адсет не активен.")

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

        for rule in active_rules:
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

                metric_val, metric_name, unit = RuleEngine._get_metric_value(metric, source_data)

                op_symbol = "≥" if operator in ("gte", "gt") else ("≤" if operator in ("lte", "lt") else "=")
                
                if unit == "$":
                    val_fmt = f"${metric_val:.2f}"
                    tgt_fmt = f"${target_val:.2f}"
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

                matches = RuleEngine._eval_condition(metric_val, operator, target_val)

                if matches:
                    any_match = True
                    matched_reasons.append(f"{metric_name}{window_label} ({val_fmt}) {op_symbol} {tgt_fmt}")
                else:
                    all_match = False

            triggered = any_match if condition_logic == "or" else all_match

            if triggered:
                action_type = rule.get("action", "turn_off")
                rule_action = action_map.get(action_type, RuleAction.STOP)
                rule_name = rule.get("name", "Unknown Rule")
                reason_str = f"[{rule_name}] " + ", ".join(matched_reasons)
                
                triggered_actions.append({
                    "action": rule_action,
                    "reason": reason_str,
                    "budget_change": float(rule.get("budget_change_percent", 0.0)),
                    "budget_max": float(rule.get("budget_max_daily", 0.0)),
                    "cooldown_minutes": int(rule.get("cooldown_minutes", 0)),
                    "notify_tg": rule.get("notify_tg", True),
                    "priority": get_action_priority(rule_action)
                })

        if not triggered_actions:
            return noop()

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
            total_conversions=total_conversions,
            cpa=cpa,
            reason=combined_reason,
            budget_change_percent=highest_priority_action["budget_change"],
            budget_max_daily=highest_priority_action["budget_max"],
            cooldown_minutes=highest_priority_action["cooldown_minutes"],
            notify_tg=highest_priority_action["notify_tg"]
        )
