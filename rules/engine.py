import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List
from database.models import Account

class RuleAction(str, Enum):
    NOOP = "NOOP"                             # Всё в норме
    STOP = "STOP"                             # Остановить адсет (PAUSE)
    NOTIFY_ONLY = "NOTIFY_ONLY"               # Только уведомить в TG (без выключения в Meta)
    PROPOSE_REACTIVATE = "PROPOSE_REACTIVATE" # Предложить включить обратно (кнопка в TG)
    AUTO_REACTIVATE = "AUTO_REACTIVATE"       # Автоматически включить обратно (ACTIVE)

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

class RuleEngine:
    """
    Движок правил с поддержкой динамических условий FB Ads Manager (Спенд, CPL, CPR)
    и 3 действий: Turn off ad sets, Turn on ad sets, Send notification only.
    """

    @staticmethod
    def _eval_condition(metric_val: float, operator: str, target_val: float) -> bool:
        if operator == "gt":
            return metric_val > target_val
        elif operator == "lt":
            return metric_val < target_val
        elif operator == "eq":
            return abs(metric_val - target_val) < 0.01
        return False

    @staticmethod
    def evaluate(
        adset: Dict[str, Any],
        account: Account,
        is_stopped_today: bool = False
    ) -> RuleEvaluationResult:
        adset_id = str(adset["adset_id"])
        adset_name = str(adset["adset_name"])
        status = adset.get("status", "UNKNOWN")
        effective_status = adset.get("effective_status", status)
        spend = float(adset.get("spend", 0.0))
        leads = int(adset.get("leads", 0))
        registrations = int(adset.get("registrations", 0))
        
        # Определяем целевые конверсии по настройке аккаунта
        if account.conversion_event == "leads":
            conversions = leads
        elif account.conversion_event == "registrations":
            conversions = registrations
        else: # "all"
            conversions = leads + registrations

        cpa = (spend / conversions) if conversions > 0 else 0.0
        cpl = (spend / leads) if leads > 0 else spend
        cpr = (spend / registrations) if registrations > 0 else spend
        is_active = status == "ACTIVE" or effective_status == "ACTIVE"

        # ----------------------------------------------------
        # 1. ПРОВЕРКА ДИНАМИЧЕСКИХ ПОЛЬЗОВАТЕЛЬСКИХ УСЛОВИЙ (FB Builder)
        # ----------------------------------------------------
        raw_conditions = account.rule_conditions
        conditions: List[Dict[str, Any]] = []
        if raw_conditions:
            try:
                conditions = json.loads(raw_conditions) if isinstance(raw_conditions, str) else raw_conditions
            except Exception:
                conditions = []

        if conditions and isinstance(conditions, list) and len(conditions) > 0:
            all_match = True
            matched_reasons = []

            for cond in conditions:
                metric = cond.get("metric", "spend")
                operator = cond.get("operator", "gt")
                target_val = float(cond.get("value", 0.0))

                metric_val = 0.0
                metric_name = "Спенд"

                if metric == "spend":
                    metric_val = spend
                    metric_name = "Спенд"
                elif metric == "cpl":
                    metric_val = cpl
                    metric_name = "Цена за лид (CPL)"
                elif metric == "cpr":
                    metric_val = cpr
                    metric_name = "Цена за регу (CPR)"

                op_symbol = ">" if operator == "gt" else ("<" if operator == "lt" else "=")
                matches = RuleEngine._eval_condition(metric_val, operator, target_val)

                if not matches:
                    all_match = False
                    break
                else:
                    matched_reasons.append(f"{metric_name} (${metric_val:.2f}) {op_symbol} ${target_val:.2f}")

            if all_match and is_active:
                action_type = account.rule_action or "turn_off"
                reason_str = "Условия правила совпали: " + ", ".join(matched_reasons)

                if action_type == "turn_off":
                    return RuleEvaluationResult(
                        action=RuleAction.STOP,
                        adset_id=adset_id,
                        adset_name=adset_name,
                        spend=spend,
                        leads=leads,
                        registrations=registrations,
                        total_conversions=conversions,
                        cpa=cpa,
                        reason=reason_str
                    )
                elif action_type == "notify_only":
                    return RuleEvaluationResult(
                        action=RuleAction.NOTIFY_ONLY,
                        adset_id=adset_id,
                        adset_name=adset_name,
                        spend=spend,
                        leads=leads,
                        registrations=registrations,
                        total_conversions=conversions,
                        cpa=cpa,
                        reason=reason_str
                    )
                elif action_type == "turn_on":
                    return RuleEvaluationResult(
                        action=RuleAction.AUTO_REACTIVATE,
                        adset_id=adset_id,
                        adset_name=adset_name,
                        spend=spend,
                        leads=leads,
                        registrations=registrations,
                        total_conversions=conversions,
                        cpa=cpa,
                        reason=reason_str
                    )

            if not all_match:
                return RuleEvaluationResult(
                    action=RuleAction.NOOP,
                    adset_id=adset_id,
                    adset_name=adset_name,
                    spend=spend,
                    leads=leads,
                    registrations=registrations,
                    total_conversions=conversions,
                    cpa=cpa,
                    reason="Метрики в пределах нормы."
                )

        # Лимиты аккаунта
        lim_0 = account.max_spend_0_leads
        lim_1 = account.max_spend_1_lead
        lim_multi = account.max_cpa_multiple_leads

        conv_label = "конверсий" if account.conversion_event == "all" else ("лидов" if account.conversion_event == "leads" else "рег")

        # ----------------------------------------------------
        # СЦЕНАРИЙ 1: Адсет сейчас АКТИВЕН (проверяем на стоп)
        # ----------------------------------------------------
        if is_active:
            # Ступень 1: 0 конверсий
            if conversions == 0 and spend >= lim_0:
                return RuleEvaluationResult(
                    action=RuleAction.STOP,
                    adset_id=adset_id,
                    adset_name=adset_name,
                    spend=spend,
                    leads=leads,
                    registrations=registrations,
                    total_conversions=conversions,
                    cpa=cpa,
                    reason=f"Спенд ${spend:.2f} превысил лимит ${lim_0:.2f} при 0 {conv_label}."
                )

            # Ступень 2: 1 конверсия
            if conversions == 1 and spend >= lim_1:
                return RuleEvaluationResult(
                    action=RuleAction.STOP,
                    adset_id=adset_id,
                    adset_name=adset_name,
                    spend=spend,
                    leads=leads,
                    registrations=registrations,
                    total_conversions=conversions,
                    cpa=cpa,
                    reason=f"Спенд ${spend:.2f} превысил лимит ${lim_1:.2f} при 1 {conv_label} (CPA: ${cpa:.2f})."
                )

            # Ступень 3: 2+ конверсий (контроль CPA)
            if conversions >= 2 and cpa > lim_multi:
                return RuleEvaluationResult(
                    action=RuleAction.STOP,
                    adset_id=adset_id,
                    adset_name=adset_name,
                    spend=spend,
                    leads=leads,
                    registrations=registrations,
                    total_conversions=conversions,
                    cpa=cpa,
                    reason=f"Итоговый CPA ${cpa:.2f} превысил допустимый порог ${lim_multi:.2f} (Спенд: ${spend:.2f}, {conv_label}: {conversions})."
                )

            return RuleEvaluationResult(
                action=RuleAction.NOOP,
                adset_id=adset_id,
                adset_name=adset_name,
                spend=spend,
                leads=leads,
                registrations=registrations,
                total_conversions=conversions,
                cpa=cpa,
                reason="Метрики в пределах нормы."
            )

        # ----------------------------------------------------
        # СЦЕНАРИЙ 2: Адсет ОСТАНОВЛЕН (проверяем на долет лида/реги)
        # ----------------------------------------------------
        if is_stopped_today and conversions > 0:
            is_good_single = (conversions == 1 and spend <= lim_1)
            is_good_multi = (conversions >= 2 and cpa <= lim_multi)

            if is_good_single or is_good_multi:
                action = RuleAction.AUTO_REACTIVATE if account.auto_reactivate else RuleAction.PROPOSE_REACTIVATE
                return RuleEvaluationResult(
                    action=action,
                    adset_id=adset_id,
                    adset_name=adset_name,
                    spend=spend,
                    leads=leads,
                    registrations=registrations,
                    total_conversions=conversions,
                    cpa=cpa,
                    reason=f"Долетел(а) {conv_label}! Спенд: ${spend:.2f}, Всего: {conversions} (Лиды: {leads}, Реги: {registrations}), CPA: ${cpa:.2f}."
                )

        return RuleEvaluationResult(
            action=RuleAction.NOOP,
            adset_id=adset_id,
            adset_name=adset_name,
            spend=spend,
            leads=leads,
            registrations=registrations,
            total_conversions=conversions,
            cpa=cpa,
            reason="Адсет остановлен, долета конверсий не зафиксировано."
        )
