import logging
import json
import asyncio
import random
import httpx
from typing import Optional, List, Dict, Any

from core.currency import from_meta_budget_units, normalize_currency, to_meta_budget_units

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

ACCOUNT_SUMMARY_FIELDS = (
    "spend,impressions,reach,frequency,cpm,clicks,unique_clicks,"
    "inline_link_clicks,outbound_clicks,actions"
)

class MetaClient:
    """
    Асинхронный клиент для работы с Meta Marketing API (Graph API v20.0)
    с поддержкой:
      1. Экспоненциального Backoff и умных повторов (2s -> 4s -> 8s) при 429/5xx/Network Error.
      2. Парсинга заголовка X-Business-Use-Case-Usage и предупреждения о расходе квоты >80%.
      3. Канонической дедупликации метрик (Лиды, Реги, Покупки).
    """

    BASE_URL = "https://graph.facebook.com/v20.0"

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _action_values(cls, rows: Any) -> Dict[str, int]:
        values: Dict[str, int] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            action_type = str(row.get("action_type", ""))
            if action_type:
                values[action_type] = cls._safe_int(row.get("value", 0))
        return values

    @classmethod
    def _first_action_value(cls, rows: Any, *aliases: str) -> int:
        values = cls._action_values(rows)
        for alias in aliases:
            if alias in values:
                return values[alias]
        if len(values) == 1:
            return next(iter(values.values()))
        return 0

    @classmethod
    def _conversion_counts(cls, insight: Dict[str, Any]) -> Dict[str, int]:
        """Extract independent funnel actions without summing synonymous rows."""

        actions = cls._action_values(insight.get("actions"))

        def first_value(*aliases: str) -> int:
            for alias in aliases:
                if alias in actions:
                    return actions[alias]
            return 0

        return {
            "leads": first_value(
                "lead",
                "offsite_conversion.fb_pixel_lead",
                "onsite_web_lead",
            ),
            "registrations": first_value(
                "complete_registration",
                "offsite_conversion.fb_pixel_complete_registration",
                "omni_complete_registration",
            ),
            "purchases": first_value(
                "purchase",
                "offsite_conversion.fb_pixel_purchase",
                "omni_purchase",
            ),
        }

    @classmethod
    def _normalize_basic_insight(cls, insight: Dict[str, Any]) -> Dict[str, Any]:
        counts = cls._conversion_counts(insight)
        spend = cls._safe_float(insight.get("spend", 0.0))
        impressions = cls._safe_int(insight.get("impressions", 0))
        reach = cls._safe_int(insight.get("reach", 0))
        frequency = cls._safe_float(insight.get("frequency", 0.0))
        cpm = cls._safe_float(insight.get("cpm", 0.0))
        if frequency <= 0 and reach > 0:
            frequency = impressions / reach
        if cpm <= 0 and impressions > 0:
            cpm = spend / impressions * 1000

        return {
            "spend": spend,
            "impressions": impressions,
            "reach": reach,
            "frequency": frequency,
            "cpm": cpm,
            "clicks": cls._safe_int(insight.get("clicks", 0)),
            "unique_clicks": cls._safe_int(insight.get("unique_clicks", 0)),
            "link_clicks": cls._safe_int(insight.get("inline_link_clicks", 0)),
            "outbound_clicks": cls._first_action_value(
                insight.get("outbound_clicks"),
                "outbound_click",
            ),
            "landing_page_views": cls._first_action_value(
                insight.get("actions"),
                "landing_page_view",
                "offsite_conversion.fb_pixel_landing_page_view",
            ),
            **counts,
        }

    async def _fetch_paginated_data(
        self,
        url: str,
        params: Dict[str, Any],
        *,
        account_id: str,
        max_pages: int = 500,
    ) -> List[Dict[str, Any]]:
        """Fetch every cursor page without following token-bearing `paging.next` URLs."""

        page_params = dict(params)
        rows: List[Dict[str, Any]] = []
        seen_cursors = set()

        for _ in range(max_pages):
            response = await self._request_with_retry(
                "GET",
                url,
                params=page_params,
                account_id=account_id,
            )
            payload = response.json()
            page_rows = payload.get("data", [])
            if isinstance(page_rows, list):
                rows.extend(row for row in page_rows if isinstance(row, dict))

            paging = payload.get("paging") or {}
            next_page = paging.get("next")
            cursor = (paging.get("cursors") or {}).get("after")
            if not next_page:
                return rows
            if not cursor or cursor in seen_cursors:
                raise RuntimeError("Meta pagination stopped on an invalid cursor")
            seen_cursors.add(cursor)
            page_params["after"] = cursor

        raise RuntimeError(f"Meta pagination exceeded {max_pages} pages")

    def _parse_usage_headers(self, headers: httpx.Headers, account_id: str = "") -> Dict[str, Any]:
        """
        Парсит диагностический заголовок X-Business-Use-Case-Usage и X-App-Usage.
        Если использование квоты достигает 80%, логирует предупреждение и флаг замедления.
        """
        usage_info = {
            "call_count": 0,
            "total_cputime": 0,
            "total_time": 0,
            "estimated_time_to_regain_access": 0,
            "is_high_usage": False
        }

        # 1. Проверяем заголовок X-Business-Use-Case-Usage (детальные лимиты по кабинету)
        buc_header = headers.get("x-business-use-case-usage")
        if buc_header:
            try:
                buc_data = json.loads(buc_header)
                # Структура: {"act_123456": [{"type": "ads_management", "call_count": 10, ...}]}
                for acc_key, metrics_list in buc_data.items():
                    for metric in metrics_list:
                        call_cnt = metric.get("call_count", 0)
                        cpu_time = metric.get("total_cputime", 0)
                        tot_time = metric.get("total_time", 0)
                        regain_mins = metric.get("estimated_time_to_regain_access", 0)

                        usage_info["call_count"] = max(usage_info["call_count"], call_cnt)
                        usage_info["total_cputime"] = max(usage_info["total_cputime"], cpu_time)
                        usage_info["total_time"] = max(usage_info["total_time"], tot_time)
                        usage_info["estimated_time_to_regain_access"] = max(usage_info["estimated_time_to_regain_access"], regain_mins)

                        # Если стрелка на спидометре дошла до 80%
                        if call_cnt >= 80 or cpu_time >= 80 or tot_time >= 80:
                            usage_info["is_high_usage"] = True
                            logger.warning(
                                f"⚠️ [BUC Rate Warning] Meta API Usage is HIGH for {account_id or acc_key}: "
                                f"call_count={call_cnt}%, cputime={cpu_time}%, time={tot_time}%, "
                                f"regain_in={regain_mins}m"
                            )
            except Exception as e:
                logger.debug(f"Failed to parse x-business-use-case-usage header: {e}")

        # 2. Проверяем заголовок X-App-Usage (общие лимиты приложения)
        app_header = headers.get("x-app-usage")
        if app_header:
            try:
                app_data = json.loads(app_header)
                call_cnt = app_data.get("call_count", 0)
                if call_cnt >= 80:
                    usage_info["is_high_usage"] = True
                    logger.warning(f"⚠️ [App Rate Warning] Meta App Usage is HIGH: {call_cnt}%")
            except Exception as e:
                logger.debug(f"Failed to parse x-app-usage header: {e}")

        return usage_info

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        account_id: str = "",
        max_retries: int = 3
    ) -> httpx.Response:
        """
        Централизованный исполнитель HTTP-запросов с Exponential Backoff + Jitter и мониторингом лимитов.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(max_retries):
                try:
                    if method.upper() == "GET":
                        resp = await client.get(url, params=params)
                    else:
                        resp = await client.post(url, data=data, params=params)

                    # Анализируем заголовки расхода квоты
                    self._parse_usage_headers(resp.headers, account_id=account_id)

                    # Успешный ответ
                    if resp.status_code == 200:
                        return resp

                    # Обработка временных ошибок перегрузки и сбоев Meta (429 Too Many Requests, 5xx Server Error)
                    if resp.status_code in [429, 500, 502, 503, 504]:
                        error_json = {}
                        try:
                            error_json = resp.json().get("error", {})
                        except Exception:
                            pass
                        error_msg = error_json.get("message", resp.text)
                        
                        if attempt < max_retries - 1:
                            # Экспоненциальная задержка: 2с -> 4с -> 8с + случайный джиттер
                            backoff = (2.0 * (2 ** attempt)) + random.uniform(-0.3, 0.3)
                            backoff = max(1.0, backoff)
                            logger.warning(
                                f"⏳ Meta API Rate Limit/Server Error ({resp.status_code}) on {url} for {account_id}. "
                                f"Backing off for {backoff:.2f}s (Attempt {attempt + 1}/{max_retries}). Details: {error_msg}"
                            )
                            await asyncio.sleep(backoff)
                            continue
                        else:
                            logger.error(f"❌ Meta API rate limit / server error exhausted after {max_retries} retries ({resp.status_code}): {error_msg}")
                            raise RuntimeError(f"Meta API Error ({resp.status_code}): {error_msg}")

                    # Фатальные ошибки авторизации и параметров (400, 401, 403)
                    error_data = {}
                    try:
                        error_data = resp.json().get("error", {})
                    except Exception:
                        pass
                    error_code = error_data.get("code")
                    error_msg = error_data.get("message", resp.text)

                    logger.error(f"Meta API Error ({resp.status_code}, code {error_code}) for {account_id}: {error_msg}")
                    if error_code in [190, 102, 10]:
                        raise PermissionError(f"Token expired or invalid: {error_msg}")
                    raise RuntimeError(f"Meta API Error ({resp.status_code}): {error_msg}")

                except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                    if attempt < max_retries - 1:
                        backoff = (2.0 * (2 ** attempt)) + random.uniform(-0.3, 0.3)
                        backoff = max(1.0, backoff)
                        logger.warning(
                            f"🌐 Network error connecting to Meta API ({type(net_err).__name__}) on {url}. "
                            f"Retrying in {backoff:.2f}s (Attempt {attempt + 1}/{max_retries})..."
                        )
                        await asyncio.sleep(backoff)
                        continue
                    else:
                        logger.error(f"❌ Network connection to Meta API failed after {max_retries} attempts: {net_err}")
                        raise RuntimeError(f"Network error connecting to Meta API: {net_err}")

        raise RuntimeError("Unexpected end of request execution loop")

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

        resp = await self._request_with_retry("GET", url, params=params, account_id=acc_id)
        data = resp.json()
        status_code = data.get("account_status", 1)
        data["currency"] = normalize_currency(data.get("currency"))
        data["status_label"] = ACCOUNT_STATUS_MAP.get(status_code, f"Неизвестный статус ({status_code})")
        return data

    async def get_account_insights_summary(
        self,
        account_id: str,
        access_token: str,
        date_preset: str = "today",
    ) -> Dict[str, Any]:
        """Return exact account-level totals for a Meta reporting period.

        These totals intentionally do not depend on the current ad set list or
        delivery status. Meta therefore includes spend from ad sets that ran in
        the period and were paused, archived or otherwise absent from the
        current operational list later in the day.
        """

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        insights_url = f"{self.BASE_URL}/{acc_id}/insights"
        rows = await self._fetch_paginated_data(
            insights_url,
            {
                "level": "account",
                "fields": ACCOUNT_SUMMARY_FIELDS,
                "date_preset": date_preset,
                "limit": 100,
                "access_token": access_token,
            },
            account_id=acc_id,
        )
        if not rows:
            return self._normalize_basic_insight({})
        if len(rows) != 1:
            raise RuntimeError(
                f"Meta returned {len(rows)} account-level insight rows without a time breakdown"
            )
        return self._normalize_basic_insight(rows[0])

    async def get_adsets_insights(
        self, 
        account_id: str, 
        access_token: str, 
        date_preset: str = "today",
        currency: str = "UNKNOWN",
    ) -> List[Dict[str, Any]]:
        """
        Получает сводную информацию по всем адсетам кабинета за указанный период (today, yesterday, last_3d, last_7d):
        текущий статус + независимые метрики Spend, Leads, Registrations и Purchases.
        """
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"

        # 1. Получаем список всех адсетов и их текущие статусы
        adsets_url = f"{self.BASE_URL}/{acc_id}/adsets"
        adsets_params = {
            "fields": "id,name,status,effective_status,daily_budget",
            "limit": 100,
            "access_token": access_token
        }
        adsets_list = await self._fetch_paginated_data(
            adsets_url,
            adsets_params,
            account_id=acc_id,
        )

        # 2. Получаем Insights за указанный период
        insights_url = f"{self.BASE_URL}/{acc_id}/insights"
        insights_params = {
            "level": "adset",
            "fields": "adset_id,adset_name,spend,impressions,clicks,cpc,ctr,actions,cost_per_action_type",
            "date_preset": date_preset,
            "limit": 100,
            "access_token": access_token
        }
        insights_rows = await self._fetch_paginated_data(
            insights_url,
            insights_params,
            account_id=acc_id,
        )
        insights_data = {
            item["adset_id"]: item 
            for item in insights_rows
            if item.get("adset_id")
        }

        # 3. Объединяем статус и метрики с канонической дедупликацией
        unified_adsets = []
        for adset in adsets_list:
            a_id = adset["id"]
            a_name = adset["name"]
            status = adset.get("status", "UNKNOWN")
            effective_status = adset.get("effective_status", status)

            insight = insights_data.get(a_id, {})
            normalized = self._normalize_basic_insight(insight)
            spend = normalized["spend"]
            impressions = normalized["impressions"]
            clicks = normalized["clicks"]
            cpc = self._safe_float(insight.get("cpc", 0.0))
            ctr = self._safe_float(insight.get("ctr", 0.0))

            unified_adsets.append({
                "adset_id": a_id,
                "adset_name": a_name,
                "status": status,
                "effective_status": effective_status,
                "spend": spend,
                "clicks": clicks,
                "leads": normalized["leads"],
                "registrations": normalized["registrations"],
                "purchases": normalized["purchases"],
                "impressions": impressions,
                "cpc": round(cpc, 2),
                "ctr": round(ctr, 2),
                "daily_budget": from_meta_budget_units(adset.get("daily_budget", 0), currency),
                "currency": normalize_currency(currency),
            })

        return unified_adsets

    async def set_adset_status(
        self, 
        adset_id: str, 
        access_token: str, 
        status: str
    ) -> bool:
        """
        Переключает статус адсета: 'PAUSED' или 'ACTIVE' с поддержкой повторов.
        """
        if status not in ["PAUSED", "ACTIVE"]:
            raise ValueError(f"Invalid status: {status}. Must be 'PAUSED' or 'ACTIVE'.")

        url = f"{self.BASE_URL}/{adset_id}"
        payload = {
            "status": status,
            "access_token": access_token
        }

        resp = await self._request_with_retry("POST", url, data=payload, account_id=adset_id)
        if resp.status_code == 200 and resp.json().get("success") is True:
            logger.info(f"Successfully set adset {adset_id} status to {status}")
            return True
        else:
            error_data = resp.json().get("error", {})
            error_msg = error_data.get("message", resp.text)
            logger.error(f"Failed to set adset {adset_id} status: {error_msg}")
            raise RuntimeError(f"Meta API Error ({resp.status_code}): {error_msg}")

    async def get_adset_state(
        self,
        adset_id: str,
        access_token: str,
        currency: str = "UNKNOWN",
    ) -> Dict[str, Any]:
        """Read the live state used by guarded action reversal checks."""

        url = f"{self.BASE_URL}/{adset_id}"
        response = await self._request_with_retry(
            "GET",
            url,
            params={
                "fields": "id,name,status,effective_status,daily_budget",
                "access_token": access_token,
            },
            account_id=adset_id,
        )
        payload = response.json()
        return {
            "adset_id": str(payload.get("id") or adset_id),
            "adset_name": str(payload.get("name") or ""),
            "status": str(payload.get("status") or "UNKNOWN").upper(),
            "effective_status": str(
                payload.get("effective_status") or payload.get("status") or "UNKNOWN"
            ).upper(),
            "daily_budget": from_meta_budget_units(payload.get("daily_budget"), currency),
            "currency": normalize_currency(currency),
        }

    async def update_adset_budget(
        self, 
        adset_id: str, 
        access_token: str, 
        new_daily_budget_dollars: float,
        currency: str = "UNKNOWN",
    ) -> bool:
        """
        Обновляет дневной бюджет в минимальных единицах валюты кабинета.
        """
        new_budget_units = to_meta_budget_units(new_daily_budget_dollars, currency)

        url = f"{self.BASE_URL}/{adset_id}"
        payload = {
            "daily_budget": str(new_budget_units),
            "access_token": access_token
        }

        resp = await self._request_with_retry("POST", url, data=payload, account_id=adset_id)
        if resp.status_code == 200 and resp.json().get("success") is True:
            logger.info(
                "Successfully updated adset %s daily budget to %.2f %s",
                adset_id,
                new_daily_budget_dollars,
                normalize_currency(currency),
            )
            return True
        else:
            error_data = resp.json().get("error", {})
            error_msg = error_data.get("message", resp.text)
            logger.error(f"Failed to update adset {adset_id} budget: {error_msg}")
            raise RuntimeError(f"Meta API Error ({resp.status_code}): {error_msg}")
