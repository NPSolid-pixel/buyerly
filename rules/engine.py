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
        insights_by_window: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> RuleEvaluationResult:
        """
        Оценивает адсет по пользовательским условиям с поддержкой AND/OR логики и временных окон.

        Args:
            adset: данные адсета за период 'today' (основной).
            account: модель Account с привязанными правилами.
            insights_by_window: словарь {time_window: adset_data} для условий с другими окнами.
                                Если None, все условия оцениваются по данным из `adset` (today).
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

        # Дефолтный NOOP результат
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
                reason=reason
            )

        # Загружаем пользовательские условия
        raw_conditions = account.rule_conditions
        conditions: List[Dict[str, Any]] = []
        if raw_conditions:
            try:
                conditions = json.loads(raw_conditions) if isinstance(raw_conditions, str) else raw_conditions
            except Exception:
                conditions = []

        if not conditions or not isinstance(conditions, list) or len(conditions) == 0:
            return noop("Правила не настроены.")

        if not is_active:
            return noop("Адсет не активен.")

        # Определяем логику объединения условий (AND/OR)
        condition_logic = getattr(account, "rule_condition_logic", "and") or "and"

        matched_reasons = []
        any_match = False
        all_match = True

        for cond in conditions:
            metric = cond.get("metric", "spend")
            operator = cond.get("operator", "gte")
            target_val = float(cond.get("value", 0.0))
            time_window = cond.get("time_window", "today")

            # Выбираем данные адсета для нужного временного окна
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

        # Определяем итоговое совпадение
        if condition_logic == "or":
            triggered = any_match
        else:  # "and"
            triggered = all_match

        if not triggered:
            return noop()

        # Условия сработали — определяем действие
        action_type = account.rule_action or "turn_off"
        reason_str = "Условия правила совпали: " + ", ".join(matched_reasons)

        # Маппинг действий
        action_map = {
            "turn_off": RuleAction.STOP,
            "notify_only": RuleAction.NOTIFY_ONLY,
            "turn_on": RuleAction.AUTO_REACTIVATE,
            "increase_budget": RuleAction.INCREASE_BUDGET,
            "decrease_budget": RuleAction.DECREASE_BUDGET,
        }
        rule_action = action_map.get(action_type, RuleAction.STOP)

        budget_pct = getattr(account, "rule_budget_change_percent", 0.0) or 0.0
        budget_max = getattr(account, "rule_budget_max_daily", 0.0) or 0.0

        return RuleEvaluationResult(
            action=rule_action,
            adset_id=adset_id,
            adset_name=adset_name,
            spend=spend,
            leads=leads,
            registrations=registrations,
            total_conversions=total_conversions,
            cpa=cpa,
            reason=reason_str,
            budget_change_percent=budget_pct,
            budget_max_daily=budget_max
        )
