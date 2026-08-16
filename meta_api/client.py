import logging
import httpx
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

ACCOUNT_STATUS_MAP = {
    1: "🟢 Активен (ACTIVE)",
    2: "🔴 Заблокирован в Meta (DISABLED / Policy Ban)",
    3: "💳 Проблема с оплатой (UNSETTLED / Hold на карте)",
    7: "⚠️ На проверке безопасности (PENDING_RISK_REVIEW)",
    8: "⏳ Ожидает списания средств (PENDING_SETTLEMENT)",
    9: "⏳ Льготный период оплаты (IN_GRACE_PERIOD)",
    101: "⚪ Кабинет закрыт (CLOSED)"
}

class MetaClient:
    """
    Асинхронный клиент для работы с Meta Marketing API (Graph API v20.0).
    """

    BASE_URL = "https://graph.facebook.com/v20.0"

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    async def get_account_info(self, account_id: str, access_token: str) -> Dict[str, Any]:
        """
        Получает информацию о рекламном кабинете (таймзона, имя, статус, валюта).
        """
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        url = f"{self.BASE_URL}/{acc_id}"
        params = {
            "fields": "id,name,timezone_name,currency,account_status,disable_reason",
            "access_token": access_token
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                error_data = resp.json().get("error", {})
                error_code = error_data.get("code")
                error_msg = error_data.get("message", resp.text)
                logger.error(f"Meta API Error (account info) for {acc_id}: {error_msg}")
                if error_code in [190, 102, 10]:
                    raise PermissionError(f"Token expired or invalid: {error_msg}")
                raise RuntimeError(f"Meta API Error ({resp.status_code}): {error_msg}")
            
            data = resp.json()
            status_code = data.get("account_status", 1)
            data["status_label"] = ACCOUNT_STATUS_MAP.get(status_code, f"Неизвестный статус ({status_code})")
            return data

    async def get_adsets_insights(
        self, 
        account_id: str, 
        access_token: str, 
        date_preset: str = "today"
    ) -> List[Dict[str, Any]]:
        """
        Получает сводную информацию по всем адсетам кабинета за указанный период (today, yesterday, last_3d, last_7d):
        текущий статус + спенд + лиды + регистрации + CPA.
        """
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # 1. Получаем список всех адсетов и их текущие статусы
            adsets_url = f"{self.BASE_URL}/{acc_id}/adsets"
            adsets_params = {
                "fields": "id,name,status,effective_status",
                "limit": 100,
                "access_token": access_token
            }
            adsets_resp = await client.get(adsets_url, params=adsets_params)
            
            if adsets_resp.status_code != 200:
                error_data = adsets_resp.json().get("error", {})
                error_msg = error_data.get("message", adsets_resp.text)
                logger.error(f"Meta API Error (adsets) for {acc_id}: {error_msg}")
                raise RuntimeError(f"Meta API Error ({adsets_resp.status_code}): {error_msg}")

            adsets_list = adsets_resp.json().get("data", [])

            # 2. Получаем Insights за указанный период
            insights_url = f"{self.BASE_URL}/{acc_id}/insights"
            insights_params = {
                "level": "adset",
                "fields": "adset_id,adset_name,spend,impressions,clicks,cpc,ctr,actions,cost_per_action_type",
                "date_preset": date_preset,
                "limit": 100,
                "access_token": access_token
            }
            insights_resp = await client.get(insights_url, params=insights_params)
            
            if insights_resp.status_code != 200:
                error_data = insights_resp.json().get("error", {})
                error_msg = error_data.get("message", insights_resp.text)
                logger.error(f"Meta API Error (insights) for {acc_id}: {error_msg}")
                raise RuntimeError(f"Meta API Error ({insights_resp.status_code}): {error_msg}")

            insights_data = {
                item["adset_id"]: item 
                for item in insights_resp.json().get("data", [])
            }

            # 3. Объединяем статус и метрики
            unified_adsets = []
            for adset in adsets_list:
                a_id = adset["id"]
                a_name = adset["name"]
                status = adset.get("status", "UNKNOWN")
                effective_status = adset.get("effective_status", status)

                insight = insights_data.get(a_id, {})
                spend = float(insight.get("spend", 0.0))
                impressions = int(insight.get("impressions", 0))
                clicks = int(insight.get("clicks", 0))
                cpc = float(insight.get("cpc", 0.0)) if "cpc" in insight else 0.0
                ctr = float(insight.get("ctr", 0.0)) if "ctr" in insight else 0.0

                # Раздельный подсчет: Клики, Лиды, Реги, Покупки (Пурчейс)
                leads = 0
                registrations = 0
                purchases = 0
                actions = insight.get("actions", [])
                for act in actions:
                    act_type = act.get("action_type", "")
                    try:
                        val = int(act.get("value", 0))
                    except (ValueError, TypeError):
                        val = 0
                        
                    if act_type in ["lead", "offsite_conversion.fb_pixel_lead", "contact", "onsite_conversion.lead_grouped"]:
                        leads += val
                    elif act_type in ["complete_registration", "offsite_conversion.fb_pixel_complete_registration", "registration"]:
                        registrations += val
                    elif act_type in ["purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase", "onsite_conversion.purchase"]:
                        purchases += val

                total_conversions = leads + registrations
                cpa = (spend / total_conversions) if total_conversions > 0 else 0.0

                unified_adsets.append({
                    "adset_id": a_id,
                    "adset_name": a_name,
                    "status": status,
                    "effective_status": effective_status,
                    "spend": spend,
                    "clicks": clicks,
                    "leads": leads,
                    "registrations": registrations,
                    "purchases": purchases,
                    "total_conversions": total_conversions,
                    "cpa": round(cpa, 2),
                    "impressions": impressions,
                    "cpc": round(cpc, 2),
                    "ctr": round(ctr, 2)
                })

            return unified_adsets

    async def set_adset_status(
        self, 
        adset_id: str, 
        access_token: str, 
        status: str
    ) -> bool:
        """
        Переключает статус адсета: 'PAUSED' или 'ACTIVE'.
        """
        if status not in ["PAUSED", "ACTIVE"]:
            raise ValueError(f"Invalid status: {status}. Must be 'PAUSED' or 'ACTIVE'.")

        url = f"{self.BASE_URL}/{adset_id}"
        payload = {
            "status": status,
            "access_token": access_token
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, data=payload)
            if resp.status_code == 200 and resp.json().get("success") is True:
                logger.info(f"Successfully set adset {adset_id} status to {status}")
                return True
            else:
                error_data = resp.json().get("error", {})
                error_msg = error_data.get("message", resp.text)
                logger.error(f"Failed to set adset {adset_id} status: {error_msg}")
                raise RuntimeError(f"Meta API Error ({resp.status_code}): {error_msg}")
