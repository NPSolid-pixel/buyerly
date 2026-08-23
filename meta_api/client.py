import logging
import json
import asyncio
import hashlib
import hmac
import random
import re
import time
from datetime import datetime, timezone
import httpx
from typing import Optional, List, Dict, Any

from core.currency import from_meta_budget_units, normalize_currency, to_meta_budget_units
from core.config import settings
from core.timezones import canonical_timezone_name

logger = logging.getLogger(__name__)

ACCOUNT_STATUS_MAP = {
    1: "Активен (ACTIVE)",
    2: "Заблокирован в Meta (DISABLED / Policy Ban)",
    3: "Проблема с оплатой (UNSETTLED / Hold на карте)",
    7: "На проверке безопасности (PENDING_RISK_REVIEW)",
    8: "Ожидает списания средств (PENDING_SETTLEMENT)",
    9: "Льготный период оплаты (IN_GRACE_PERIOD)",
    101: "Кабинет закрыт (CLOSED)"
}

ACCOUNT_SUMMARY_FIELDS = (
    "spend,impressions,reach,frequency,cpm,clicks,unique_clicks,"
    "inline_link_clicks,outbound_clicks,actions"
)

META_TOKEN_SUBCODE_MAP: Dict[int, tuple[str, str, str, str]] = {
    # subcode: (subcode_key, title, description, action_hint)
    458: (
        "APP_REVOKED",
        "🚫 Доступ отозван",
        "Приложение удалено из бизнес-интеграций Facebook",
        "Переподключите интеграцию в настройках Business Manager",
    ),
    459: (
        "CHECKPOINT",
        "🔒 Чекпоинт / Бан профиля",
        "Профиль Facebook отправлен на проверку безопасности (селфи / документы)",
        "Зайдите в профиль через антидетект-браузер и пройдите чекпоинт",
    ),
    460: (
        "PASSWORD_CHANGED",
        "🔑 Пароль изменён",
        "Пароль аккаунта был изменён, все сессии сброшены",
        "Авторизуйтесь заново с новым паролем",
    ),
    463: (
        "SESSION_EXPIRED",
        "⏳ Срок токена истёк",
        "Истёк 60-дневный срок действия долгоживущего токена",
        "Обновите токен через бота (кнопка '➕ Добавить кабинеты')",
    ),
    464: (
        "UNCONFIRMED_USER",
        "📧 Аккаунт не подтверждён",
        "Пользователь не подтвердил email или телефон в Facebook",
        "Подтвердите контактные данные в профиле FB",
    ),
    467: (
        "ACCESS_TOKEN_INVALIDATED",
        "🚪 Сессия завершена",
        "Выполнен выход со всех устройств или сброс токена",
        "Выпустите новый токен доступа",
    ),
    490: (
        "LOGIN_APPROVAL_NEEDED",
        "🛡 Требуется 2FA",
        "Meta запросила подтверждение двухфакторной аутентификации",
        "Подтвердите вход через приложение аутентификации",
    ),
    492: (
        "DEVICE_SESSION_EXPIRED",
        "📱 Сессия устройства устарела",
        "Сессия мобильного/веб устройства устарела",
        "Переавторизуйтесь в аккаунте",
    ),
    1348001: (
        "ACCOUNT_PERMISSION_DENIED",
        "🚫 Нет прав на кабинет",
        "Пользователь не имеет роли администратора/рекламодателя в кабинете",
        "Выдайте права пользователю в Business Manager",
    ),
}


class MetaTokenAuthError(PermissionError):
    """
    Структурированное исключение авторизации/токена Meta Marketing API.
    Наследуется от PermissionError для 100% обратной совместимости.
    """

    def __init__(
        self,
        message: str,
        *,
        code: int = 190,
        subcode: Optional[int] = None,
        subcode_key: str = "UNKNOWN",
        title: str = "",
        description: str = "",
        action_hint: str = "",
        error_user_title: str = "",
        error_user_msg: str = "",
        fbtrace_id: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.subcode_key = subcode_key
        self.title = title
        self.description = description
        self.action_hint = action_hint
        self.error_user_title = error_user_title
        self.error_user_msg = error_user_msg
        self.fbtrace_id = fbtrace_id


def classify_meta_token_error(
    error_data: Dict[str, Any],
    fallback_message: str = "",
) -> MetaTokenAuthError:
    """
    Классифицирует ошибку токена/прав Meta API на основе code, error_subcode и сообщений Meta.
    """
    raw_code = error_data.get("code")
    try:
        code = int(raw_code) if raw_code is not None else 190
    except (TypeError, ValueError):
        code = 190

    raw_subcode = error_data.get("error_subcode")
    try:
        subcode = int(raw_subcode) if raw_subcode is not None else None
    except (TypeError, ValueError):
        subcode = None

    error_user_title = str(error_data.get("error_user_title") or "").strip()
    error_user_msg = str(error_data.get("error_user_msg") or "").strip()
    fbtrace_id = str(error_data.get("fbtrace_id") or "").strip()
    message = str(error_data.get("message") or fallback_message or "Meta token expired or invalid").strip()

    if subcode is not None and subcode in META_TOKEN_SUBCODE_MAP:
        subcode_key, title, description, action_hint = META_TOKEN_SUBCODE_MAP[subcode]
    elif code in [10, 200]:
        subcode_key = "ACCOUNT_PERMISSION_DENIED"
        title = "🚫 Нет прав на кабинет"
        description = "Недостаточно прав для управления рекламным кабинетом в Meta"
        action_hint = "Проверьте права пользователя в Business Manager"
    elif code in [102]:
        subcode_key = "API_SESSION_INVALID"
        title = "🔌 Сессия API недействительна"
        description = "Сессия API Meta завершена или сброшена"
        action_hint = "Переподключите аккаунт через OAuth"
    else:
        subcode_key = "TOKEN_INVALID"
        title = "🔑 Токен недействителен"
        description = "Токен доступа Meta API стал недействительным или истёк"
        action_hint = "Обновите токен через бота (кнопка '➕ Добавить кабинеты')"

    return MetaTokenAuthError(
        f"Token expired or invalid: {message}",
        code=code,
        subcode=subcode,
        subcode_key=subcode_key,
        title=title,
        description=description,
        action_hint=action_hint,
        error_user_title=error_user_title,
        error_user_msg=error_user_msg,
        fbtrace_id=fbtrace_id,
    )


class MetaRateLimitDeferred(RuntimeError):
    """A non-critical request was postponed to protect the Meta API quota."""

class MetaClient:
    """
    Асинхронный клиент для работы с Meta Marketing API.
    с поддержкой:
      1. Экспоненциального Backoff и умных повторов при 429/5xx/Network Error.
      2. Адаптивного ограничения запросов по заголовкам квоты Meta.
      3. Канонической дедупликации метрик (Лиды, Реги, Покупки).
      4. Повторного использования HTTP-соединений и кэша инвентаря адсетов.
    """

    GRAPH_VERSION_PATTERN = re.compile(r"^v\d+\.\d+$")

    def __init__(
        self,
        timeout: float = 15.0,
        graph_version: Optional[str] = None,
        cache_provider: Optional[Any] = None,
    ):
        self.timeout = timeout
        requested_version = str(graph_version or settings.META_GRAPH_VERSION).strip()
        if not self.GRAPH_VERSION_PATTERN.fullmatch(requested_version):
            raise ValueError("META_GRAPH_VERSION must look like v26.0")
        self.graph_version = requested_version
        self.base_url = f"https://graph.facebook.com/{self.graph_version}"
        self._client: Optional[httpx.AsyncClient] = None
        self._cache_provider = cache_provider
        self._inventory_cache_seconds = 5 * 60
        self._inventory_cache: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
        self._adaptive_polling_enabled = True
        self._usage_soft_limit_percent = 60
        self._usage_hard_limit_percent = 80
        self._usage_ttl_seconds = 15 * 60
        self._usage_snapshot: Dict[str, Any] = {
            "max_percent": 0,
            "app": {},
            "accounts": {},
            "updated_at": None,
        }

    def configure_automation(
        self,
        *,
        inventory_cache_minutes: int = 5,
        adaptive_polling_enabled: bool = True,
        usage_soft_limit_percent: int = 60,
        usage_hard_limit_percent: int = 80,
        usage_ttl_minutes: int = 15,
    ) -> None:
        """Apply operator-controlled limits without recreating the client."""

        self._inventory_cache_seconds = max(60, int(inventory_cache_minutes) * 60)
        self._adaptive_polling_enabled = bool(adaptive_polling_enabled)
        self._usage_soft_limit_percent = max(1, int(usage_soft_limit_percent))
        self._usage_hard_limit_percent = max(
            self._usage_soft_limit_percent + 1,
            int(usage_hard_limit_percent),
        )
        self._usage_ttl_seconds = max(60, int(usage_ttl_minutes) * 60)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def get_usage_snapshot(self) -> Dict[str, Any]:
        return {
            "max_percent": int(self._usage_snapshot.get("max_percent", 0) or 0),
            "app": dict(self._usage_snapshot.get("app") or {}),
            "accounts": {
                key: dict(value)
                for key, value in (self._usage_snapshot.get("accounts") or {}).items()
            },
            "updated_at": self._usage_snapshot.get("updated_at"),
        }

    @staticmethod
    def _normalize_account_id(account_id: str) -> str:
        acc = str(account_id or "").strip()
        if not acc:
            return ""
        return acc if acc.startswith("act_") else f"act_{acc}"

    @staticmethod
    def _copy_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [dict(row) for row in rows]

    def _auth_protected_payload(
        self,
        payload: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if payload is None:
            return None
        protected = dict(payload)
        access_token = str(protected.get("access_token") or "")
        app_secret = str(settings.META_APP_SECRET or "")
        if access_token and app_secret:
            protected["appsecret_proof"] = hmac.new(
                app_secret.encode("utf-8"),
                access_token.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        return protected

    async def _respect_usage_limit(
        self,
        *,
        account_id: str = "",
        priority: str = "normal",
    ) -> None:
        if not self._adaptive_polling_enabled:
            return
        if priority == "critical":
            return

        now = time.time()

        # 1. Global App-Level Quota (X-App-Usage)
        app_usage = self._usage_snapshot.get("app") or {}
        app_updated = self._safe_float(self._usage_snapshot.get("updated_at") or 0)
        app_percent = 0
        if not app_updated or (now - app_updated) <= self._usage_ttl_seconds:
            app_percent = max(
                (self._safe_int(v) for v in app_usage.values()),
                default=0,
            )

        if app_percent >= self._usage_hard_limit_percent:
            raise MetaRateLimitDeferred(
                f"Meta App quota is at {app_percent}%; non-critical polling is deferred"
            )

        # 2. Local Ad Account-Level Quota (X-Business-Use-Case-Usage)
        account_percent = 0
        target_account = self._normalize_account_id(account_id)
        if target_account:
            accounts_map = self._usage_snapshot.get("accounts") or {}
            acc_data = accounts_map.get(target_account)
            if not acc_data and target_account.startswith("act_"):
                acc_data = accounts_map.get(target_account[4:])
            elif not acc_data and not target_account.startswith("act_"):
                acc_data = accounts_map.get(f"act_{target_account}")

            if acc_data:
                acc_updated = self._safe_float(acc_data.get("updated_at") or 0)
                if not acc_updated or (now - acc_updated) <= self._usage_ttl_seconds:
                    account_percent = max(
                        self._safe_int(acc_data.get("call_count")),
                        self._safe_int(acc_data.get("total_cputime")),
                        self._safe_int(acc_data.get("total_time")),
                    )

            if account_percent >= self._usage_hard_limit_percent:
                raise MetaRateLimitDeferred(
                    f"Meta quota for account {account_id} is at {account_percent}%; non-critical polling is deferred"
                )
        else:
            raw_max = self._safe_int(self._usage_snapshot.get("max_percent", 0))
            if raw_max >= self._usage_hard_limit_percent and not app_usage:
                raise MetaRateLimitDeferred(
                    f"Meta quota is at {raw_max}%; non-critical polling is deferred"
                )

        effective_pressure_percent = max(app_percent, account_percent)
        if effective_pressure_percent >= self._usage_soft_limit_percent:
            pressure = effective_pressure_percent - self._usage_soft_limit_percent + 1
            await asyncio.sleep(min(2.0, 0.05 * pressure) + random.uniform(0.0, 0.15))

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

        # 1. Standard / Omni Meta event aliases
        leads = first_value(
            "lead",
            "omni_lead",
            "omni:lead",
            "offsite_conversion.fb_pixel_lead",
            "onsite_conversion.lead_grouped",
            "onsite_web_lead",
            "onsite_lead",
            "leadgen.other",
            "leadgen",
            "leadgen_grouped",
        )

        registrations = first_value(
            "complete_registration",
            "omni_complete_registration",
            "omni:complete_registration",
            "offsite_conversion.fb_pixel_complete_registration",
            "onsite_conversion.registration_grouped",
            "onsite_web_complete_registration",
        )

        purchases = first_value(
            "purchase",
            "omni_purchase",
            "omni:purchase",
            "offsite_conversion.fb_pixel_purchase",
            "onsite_conversion.purchase_grouped",
            "onsite_web_purchase",
        )

        # 2. Semantic matching for custom conversions (e.g. custom:form_submitted, custom:order_placed)
        custom_actions = {
            k: v for k, v in actions.items()
            if v > 0 and (
                k.startswith("offsite_conversion.custom.")
                or k.startswith("custom:")
                or k.startswith("omni_custom")
            )
        }

        lead_keywords = ("lead", "form", "submit", "contact", "schedule", "appli", "request")
        reg_keywords = ("reg", "signup", "sign_up", "account")
        purchase_keywords = ("purchase", "buy", "order", "sale", "checkout", "deposit")

        if leads == 0:
            for k in sorted(custom_actions):
                k_lower = k.lower()
                if any(kw in k_lower for kw in lead_keywords):
                    leads = custom_actions[k]
                    break

        if registrations == 0:
            for k in sorted(custom_actions):
                k_lower = k.lower()
                if any(kw in k_lower for kw in reg_keywords):
                    registrations = custom_actions[k]
                    break

        if purchases == 0:
            for k in sorted(custom_actions):
                k_lower = k.lower()
                if any(kw in k_lower for kw in purchase_keywords):
                    purchases = custom_actions[k]
                    break

        # 3. Fallback for unclassified custom conversions (e.g. offsite_conversion.custom.<id>)
        if leads == 0 and custom_actions:
            for k in sorted(custom_actions):
                leads = custom_actions[k]
                break

        return {
            "leads": leads,
            "registrations": registrations,
            "purchases": purchases,
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
                "omni_outbound_click",
                "omni:outbound_click",
            ),
            "landing_page_views": cls._first_action_value(
                insight.get("actions"),
                "landing_page_view",
                "omni_landing_page_view",
                "omni:landing_page_view",
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
        priority: str = "normal",
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
                priority=priority,
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
        now = time.time()
        usage_info = {
            "call_count": 0,
            "total_cputime": 0,
            "total_time": 0,
            "estimated_time_to_regain_access": 0,
            "is_high_usage": False,
            "updated_at": now,
        }

        # 1. Проверяем заголовок X-Business-Use-Case-Usage (детальные лимиты по кабинету)
        buc_header = headers.get("x-business-use-case-usage")
        if buc_header:
            try:
                buc_data = json.loads(buc_header)
                # Структура: {"act_123456": [{"type": "ads_management", "call_count": 10, ...}]}
                for acc_key, metrics_list in buc_data.items():
                    acc_norm = self._normalize_account_id(acc_key)
                    acc_usage = {
                        "call_count": 0,
                        "total_cputime": 0,
                        "total_time": 0,
                        "estimated_time_to_regain_access": 0,
                        "is_high_usage": False,
                        "updated_at": now,
                    }
                    for metric in metrics_list:
                        call_cnt = self._safe_int(metric.get("call_count", 0))
                        cpu_time = self._safe_int(metric.get("total_cputime", 0))
                        tot_time = self._safe_int(metric.get("total_time", 0))
                        regain_mins = self._safe_int(
                            metric.get("estimated_time_to_regain_access", 0)
                        )

                        acc_usage["call_count"] = max(acc_usage["call_count"], call_cnt)
                        acc_usage["total_cputime"] = max(acc_usage["total_cputime"], cpu_time)
                        acc_usage["total_time"] = max(acc_usage["total_time"], tot_time)
                        acc_usage["estimated_time_to_regain_access"] = max(acc_usage["estimated_time_to_regain_access"], regain_mins)

                        usage_info["call_count"] = max(usage_info["call_count"], call_cnt)
                        usage_info["total_cputime"] = max(usage_info["total_cputime"], cpu_time)
                        usage_info["total_time"] = max(usage_info["total_time"], tot_time)
                        usage_info["estimated_time_to_regain_access"] = max(usage_info["estimated_time_to_regain_access"], regain_mins)

                        # Если стрелка на спидометре дошла до 80%
                        if call_cnt >= 80 or cpu_time >= 80 or tot_time >= 80:
                            acc_usage["is_high_usage"] = True
                            usage_info["is_high_usage"] = True
                            logger.warning(
                                f"⚠️ [BUC Rate Warning] Meta API Usage is HIGH for {account_id or acc_key}: "
                                f"call_count={call_cnt}%, cputime={cpu_time}%, time={tot_time}%, "
                                f"regain_in={regain_mins}m"
                            )
                    self._usage_snapshot["accounts"][acc_norm] = acc_usage

                if account_id:
                    normalized_id = self._normalize_account_id(account_id)
                    self._usage_snapshot["accounts"][normalized_id] = dict(usage_info)
            except Exception as e:
                logger.debug(f"Failed to parse x-business-use-case-usage header: {e}")

        # 2. Проверяем заголовок X-App-Usage (общие лимиты приложения)
        app_header = headers.get("x-app-usage")
        if app_header:
            try:
                app_data = json.loads(app_header)
                app_usage = {
                    "call_count": self._safe_int(app_data.get("call_count", 0)),
                    "total_cputime": self._safe_int(app_data.get("total_cputime", 0)),
                    "total_time": self._safe_int(app_data.get("total_time", 0)),
                }
                self._usage_snapshot["app"] = app_usage
                call_cnt = app_usage["call_count"]
                if max(app_usage.values(), default=0) >= 80:
                    usage_info["is_high_usage"] = True
                    logger.warning(f"⚠️ [App Rate Warning] Meta App Usage is HIGH: {call_cnt}%")
            except Exception as e:
                logger.debug(f"Failed to parse x-app-usage header: {e}")

        account_max = max(
            (
                max(
                    self._safe_int(row.get("call_count")),
                    self._safe_int(row.get("total_cputime")),
                    self._safe_int(row.get("total_time")),
                )
                for row in self._usage_snapshot["accounts"].values()
                if (now - self._safe_float(row.get("updated_at") or 0)) <= self._usage_ttl_seconds
            ),
            default=0,
        )
        app_max = max(
            (self._safe_int(value) for value in self._usage_snapshot["app"].values()),
            default=0,
        )
        self._usage_snapshot["max_percent"] = max(account_max, app_max)
        self._usage_snapshot["updated_at"] = now
        return usage_info

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        account_id: str = "",
        max_retries: int = 3,
        priority: str = "normal",
    ) -> httpx.Response:
        """
        Централизованный исполнитель HTTP-запросов с Exponential Backoff + Jitter и мониторингом лимитов.
        """
        await self._respect_usage_limit(account_id=account_id, priority=priority)
        client = await self._get_client()
        request_params = self._auth_protected_payload(params)
        request_data = self._auth_protected_payload(data)
        for attempt in range(max_retries):
            try:
                if method.upper() == "GET":
                    resp = await client.get(url, params=request_params)
                else:
                    resp = await client.post(url, data=request_data, params=request_params)

                self._parse_usage_headers(resp.headers, account_id=account_id)
                if resp.status_code == 200:
                    return resp

                if resp.status_code in [429, 500, 502, 503, 504]:
                    error_json = {}
                    try:
                        error_json = resp.json().get("error", {})
                    except Exception:
                        pass
                    error_msg = error_json.get("message", resp.text)
                    if attempt < max_retries - 1:
                        retry_after = self._safe_float(resp.headers.get("retry-after"))
                        backoff = retry_after or (
                            (2.0 * (2 ** attempt)) + random.uniform(-0.3, 0.3)
                        )
                        backoff = max(1.0, backoff)
                        logger.warning(
                            "Meta API temporary error %s on %s for %s; retrying in %.2fs "
                            "(attempt %s/%s): %s",
                            resp.status_code,
                            url,
                            account_id,
                            backoff,
                            attempt + 1,
                            max_retries,
                            error_msg,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    logger.error(
                        "Meta API temporary error exhausted after %s attempts (%s): %s",
                        max_retries,
                        resp.status_code,
                        error_msg,
                    )
                    raise RuntimeError(f"Meta API Error ({resp.status_code}): {error_msg}")

                error_data = {}
                try:
                    error_data = resp.json().get("error", {})
                except Exception:
                    pass
                error_code = error_data.get("code")
                error_subcode = error_data.get("error_subcode")
                error_msg = error_data.get("message", resp.text)
                logger.error(
                    "Meta API Error (%s, code %s, subcode %s) for %s: %s",
                    resp.status_code,
                    error_code,
                    error_subcode,
                    account_id,
                    error_msg,
                )
                if error_code in [190, 102, 10, 200]:
                    raise classify_meta_token_error(error_data, fallback_message=error_msg)
                raise RuntimeError(f"Meta API Error ({resp.status_code}): {error_msg}")

            except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                if attempt < max_retries - 1:
                    backoff = (2.0 * (2 ** attempt)) + random.uniform(-0.3, 0.3)
                    backoff = max(1.0, backoff)
                    logger.warning(
                        "Network error connecting to Meta API (%s) on %s; retrying in %.2fs "
                        "(attempt %s/%s)",
                        type(net_err).__name__,
                        url,
                        backoff,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(backoff)
                    continue
                else:
                    logger.error(
                        "Network connection to Meta API failed after %s attempts: %s",
                        max_retries,
                        net_err,
                    )
                    raise RuntimeError(f"Network error connecting to Meta API: {net_err}")

        raise RuntimeError("Unexpected end of request execution loop")

    async def get_account_info(
        self,
        account_id: str,
        access_token: str,
        *,
        priority: str = "normal",
    ) -> Dict[str, Any]:
        """
        Получает информацию о рекламном кабинете (таймзона, имя, статус, валюта).
        """
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        url = f"{self.base_url}/{acc_id}"
        params = {
            "fields": "id,name,timezone_name,currency,account_status,disable_reason",
            "access_token": access_token
        }

        resp = await self._request_with_retry(
            "GET",
            url,
            params=params,
            account_id=acc_id,
            priority=priority,
        )
        data = resp.json()
        status_code = data.get("account_status", 1)
        data["currency"] = normalize_currency(data.get("currency"))
        data["timezone_name"] = canonical_timezone_name(data.get("timezone_name"))
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
        insights_url = f"{self.base_url}/{acc_id}/insights"
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
        priority: str = "normal",
    ) -> List[Dict[str, Any]]:
        """
        Получает сводную информацию по всем адсетам кабинета за указанный период (today, yesterday, last_3d, last_7d):
        текущий статус + независимые метрики Spend, Leads, Registrations и Purchases.
        """
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"

        # 1. Получаем список всех адсетов и их текущие статусы
        adsets_url = f"{self.base_url}/{acc_id}/adsets"
        adsets_params = {
            "fields": "id,name,status,effective_status,daily_budget",
            "limit": 100,
            "access_token": access_token
        }
        request_started_at = datetime.now(timezone.utc)
        adsets_list = None
        if self._cache_provider is not None:
            adsets_list = await self._cache_provider.get_inventory(acc_id)

        if adsets_list is None:
            cached = self._inventory_cache.get(acc_id)
            if cached and cached[0] > time.monotonic():
                adsets_list = self._copy_rows(cached[1])

        if adsets_list is None:
            adsets_list = await self._fetch_paginated_data(
                adsets_url,
                adsets_params,
                account_id=acc_id,
                priority=priority,
            )
            self._inventory_cache[acc_id] = (
                time.monotonic() + self._inventory_cache_seconds,
                self._copy_rows(adsets_list),
            )
            if self._cache_provider is not None:
                await self._cache_provider.set_inventory(
                    acc_id,
                    self._copy_rows(adsets_list),
                    ttl_seconds=self._inventory_cache_seconds,
                    request_started_at=request_started_at,
                )

        # 2. Получаем Insights за указанный период
        insights_url = f"{self.base_url}/{acc_id}/insights"
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
            priority=priority,
        )
        insights_data = {
            item["adset_id"]: item 
            for item in insights_rows
            if item.get("adset_id")
        }

        # 3. Объединяем статус и метрики с канонической дедупликацией
        unified_adsets = []
        processed_ids = set()
        for adset in adsets_list:
            a_id = str(adset["id"])
            processed_ids.add(a_id)
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

            # Безопасный пропуск: мертвые архивные/удаленные адсеты без активности за отчетный период
            if (
                effective_status in ("ARCHIVED", "DELETED")
                and spend == 0
                and impressions == 0
                and clicks == 0
            ):
                continue

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

        # Дополнительная страховка: адсеты со спендом из инсайтов, которых нет в списке активных
        for a_id, insight in insights_data.items():
            if str(a_id) not in processed_ids:
                normalized = self._normalize_basic_insight(insight)
                spend = normalized["spend"]
                if spend > 0 or normalized["impressions"] > 0:
                    unified_adsets.append({
                        "adset_id": str(a_id),
                        "adset_name": insight.get("adset_name") or f"AdSet {a_id}",
                        "status": "ARCHIVED",
                        "effective_status": "ARCHIVED",
                        "spend": spend,
                        "clicks": normalized["clicks"],
                        "leads": normalized["leads"],
                        "registrations": normalized["registrations"],
                        "purchases": normalized["purchases"],
                        "impressions": normalized["impressions"],
                        "cpc": round(self._safe_float(insight.get("cpc", 0.0)), 2),
                        "ctr": round(self._safe_float(insight.get("ctr", 0.0)), 2),
                        "daily_budget": 0.0,
                        "currency": normalize_currency(currency),
                    })

        return unified_adsets

    async def set_adset_status(
        self, 
        adset_id: str, 
        access_token: str, 
        status: str,
        account_id: Optional[str] = None,
    ) -> bool:
        """
        Переключает статус адсета: 'PAUSED' или 'ACTIVE' с поддержкой повторов.
        """
        if status not in ["PAUSED", "ACTIVE"]:
            raise ValueError(f"Invalid status: {status}. Must be 'PAUSED' or 'ACTIVE'.")

        url = f"{self.base_url}/{adset_id}"
        payload = {
            "status": status,
            "access_token": access_token
        }

        resp = await self._request_with_retry(
            "POST",
            url,
            data=payload,
            account_id=adset_id,
            priority="critical",
        )
        if resp.status_code == 200 and resp.json().get("success") is True:
            for _, rows in self._inventory_cache.values():
                for row in rows:
                    if str(row.get("id")) == str(adset_id):
                        row["status"] = status
                        row["effective_status"] = status
            if self._cache_provider is not None:
                acc_id = self._normalize_account_id(account_id or "")
                await self._cache_provider.update_status(
                    account_id=acc_id,
                    adset_id=adset_id,
                    status=status,
                )
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

        url = f"{self.base_url}/{adset_id}"
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
        account_id: Optional[str] = None,
    ) -> bool:
        """
        Обновляет дневной бюджет в минимальных единицах валюты кабинета.
        """
        new_budget_units = to_meta_budget_units(new_daily_budget_dollars, currency)

        url = f"{self.base_url}/{adset_id}"
        payload = {
            "daily_budget": str(new_budget_units),
            "access_token": access_token
        }

        resp = await self._request_with_retry(
            "POST",
            url,
            data=payload,
            account_id=adset_id,
            priority="critical",
        )
        if resp.status_code == 200 and resp.json().get("success") is True:
            for _, rows in self._inventory_cache.values():
                for row in rows:
                    if str(row.get("id")) == str(adset_id):
                        row["daily_budget"] = str(new_budget_units)
            if self._cache_provider is not None:
                acc_id = self._normalize_account_id(account_id or "")
                if acc_id:
                    await self._cache_provider.invalidate(acc_id)
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

    async def invalidate_inventory_cache(self, account_id: str) -> None:
        """Invalidate inventory cache in memory and in the external provider."""
        acc_id = self._normalize_account_id(account_id)
        if acc_id:
            self._inventory_cache.pop(acc_id, None)
            if self._cache_provider is not None:
                await self._cache_provider.invalidate(acc_id)

