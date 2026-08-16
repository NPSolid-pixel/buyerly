from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any
from database.models import Account

class RuleAction(str, Enum):
    NOOP = "NOOP"                             # Всё в норме
    STOP = "STOP"                             # Остановить адсет (PAUSE)
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
    Движок ступенчатых правил с поддержкой Лидов и Регистраций.
    """

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
        is_active = status == "ACTIVE" or effective_status == "ACTIVE"

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
