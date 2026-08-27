"""Server-side Facebook Login for Business OAuth exchange and validation."""

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx


class MetaOAuthRemoteError(RuntimeError):
    """Sanitized Meta OAuth failure safe to persist or show in the product."""


class MetaOAuthClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        graph_version: str,
        login_config_id: str,
        timeout: float = 20.0,
    ):
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.redirect_uri = redirect_uri.strip()
        self.graph_version = graph_version.strip()
        self.login_config_id = login_config_id.strip()
        self.timeout = timeout
        self.graph_url = f"https://graph.facebook.com/{self.graph_version}"

    def build_authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "response_type": "code",
            "config_id": self.login_config_id,
            "override_default_response_type": "true",
        }
        return (
            f"https://www.facebook.com/{self.graph_version}/dialog/oauth?"
            f"{urlencode(params)}"
        )

    async def _get_json(
        self,
        path: str,
        *,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.graph_url}/{path.lstrip('/')}", params=params)
        except httpx.HTTPError as exc:
            raise MetaOAuthRemoteError("Meta OAuth is temporarily unavailable") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise MetaOAuthRemoteError("Meta OAuth returned an invalid response") from exc
        if response.status_code >= 400 or payload.get("error"):
            error = payload.get("error") if isinstance(payload, dict) else {}
            error_code = error.get("code") if isinstance(error, dict) else None
            suffix = f" (code {error_code})" if error_code else ""
            raise MetaOAuthRemoteError(f"Meta OAuth rejected the request{suffix}")
        if not isinstance(payload, dict):
            raise MetaOAuthRemoteError("Meta OAuth returned an invalid response")
        return payload

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        short_lived = await self._get_json(
            "oauth/access_token",
            params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
        )
        access_token = str(short_lived.get("access_token") or "").strip()
        if not access_token:
            raise MetaOAuthRemoteError("Meta did not return an access token")

        # Business Login can return a token that is already long-lived. If the
        # documented exchange is not applicable, validation below still gives
        # us the real expiry and preserves the usable token.
        try:
            long_lived = await self._get_json(
                "oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "fb_exchange_token": access_token,
                },
            )
            access_token = str(long_lived.get("access_token") or access_token).strip()
        except MetaOAuthRemoteError:
            pass

        debug = await self.debug_token(access_token)
        identity = await self.get_identity(access_token)
        return {
            "access_token": access_token,
            "debug": debug,
            "identity": identity,
        }

    async def debug_token(self, access_token: str) -> Dict[str, Any]:
        payload = await self._get_json(
            "debug_token",
            params={
                "input_token": access_token,
                "access_token": f"{self.app_id}|{self.app_secret}",
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("is_valid") is not True:
            raise MetaOAuthRemoteError("Meta access token is invalid")
        token_app_id = str(data.get("app_id") or "")
        if token_app_id and token_app_id != self.app_id:
            raise MetaOAuthRemoteError("Meta access token belongs to another application")
        return data

    async def get_identity(self, access_token: str) -> Dict[str, Any]:
        proof = self.appsecret_proof(access_token)
        identity = await self._get_json(
            "me",
            params={
                "fields": "id,name",
                "access_token": access_token,
                "appsecret_proof": proof,
            },
        )
        if not identity.get("id"):
            raise MetaOAuthRemoteError("Meta did not return the authorized user")
        return identity

    def appsecret_proof(self, access_token: str) -> str:
        return hmac.new(
            self.app_secret.encode("utf-8"),
            access_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def discover_ad_accounts(self, access_token: str) -> list[Dict[str, Any]]:
        """Return every ad account visible to the authorized Meta profile."""

        rows: list[Dict[str, Any]] = []
        params: Dict[str, Any] = {
            "fields": (
                "id,account_id,name,account_status,currency,timezone_name,business"
            ),
            "limit": 100,
            "access_token": access_token,
            "appsecret_proof": self.appsecret_proof(access_token),
        }
        seen_cursors: set[str] = set()
        for _ in range(200):
            payload = await self._get_json("me/adaccounts", params=params)
            page = payload.get("data")
            if isinstance(page, list):
                rows.extend(item for item in page if isinstance(item, dict))
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            if not paging.get("next"):
                return rows
            cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            after = str(cursors.get("after") or "")
            if not after or after in seen_cursors:
                raise MetaOAuthRemoteError("Meta returned an invalid accounts cursor")
            seen_cursors.add(after)
            params["after"] = after
        raise MetaOAuthRemoteError("Meta returned too many account pages")


    async def revoke_permissions(self, access_token: str) -> bool:
        """Best-effort revocation of granted permissions in Meta Graph API."""
        try:
            proof = self.appsecret_proof(access_token)
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.delete(
                    f"{self.graph_url}/me/permissions",
                    params={
                        "access_token": access_token,
                        "appsecret_proof": proof,
                    },
                )
            payload = response.json() if response.content else {}
            return bool(payload.get("success", False))
        except Exception:
            return False


REQUIRED_META_SCOPES = ("ads_read", "ads_management", "business_management")
EXPIRING_THRESHOLD_DAYS = 7


def meta_token_expiry(debug_data: Dict[str, Any]) -> Optional[datetime]:
    raw_expiry = debug_data.get("expires_at")
    try:
        timestamp = int(raw_expiry or 0)
    except (TypeError, ValueError):
        timestamp = 0
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def evaluate_meta_connection_health(
    debug_data: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Classify token health into active, expiring, expired, missing_scopes, or needs_reconnect."""
    now = now or datetime.now(timezone.utc)
    is_valid = bool(debug_data.get("is_valid", False))
    if not is_valid:
        return {
            "status": "needs_reconnect",
            "days_until_expiration": None,
            "missing_scopes": list(REQUIRED_META_SCOPES),
            "granted_scopes": [],
            "token_expires_at": None,
            "error": "Токен недействителен или отозван в Meta",
        }

    raw_scopes = debug_data.get("scopes") or []
    granted_scopes = [str(s) for s in raw_scopes if s]
    missing_scopes = [s for s in REQUIRED_META_SCOPES if s not in granted_scopes]

    expires_at = meta_token_expiry(debug_data)
    days_until_expiration: Optional[int] = None
    if expires_at is not None:
        diff_seconds = (expires_at - now).total_seconds()
        days_until_expiration = max(0, int(diff_seconds // 86400))
        if diff_seconds <= 0:
            return {
                "status": "expired",
                "days_until_expiration": 0,
                "missing_scopes": missing_scopes,
                "granted_scopes": granted_scopes,
                "token_expires_at": expires_at,
                "error": "Срок действия токена истёк",
            }

    if missing_scopes:
        return {
            "status": "missing_scopes",
            "days_until_expiration": days_until_expiration,
            "missing_scopes": missing_scopes,
            "granted_scopes": granted_scopes,
            "token_expires_at": expires_at,
            "error": f"Отсутствуют обязательные права: {', '.join(missing_scopes)}",
        }

    if days_until_expiration is not None and days_until_expiration <= EXPIRING_THRESHOLD_DAYS:
        return {
            "status": "expiring",
            "days_until_expiration": days_until_expiration,
            "missing_scopes": [],
            "granted_scopes": granted_scopes,
            "token_expires_at": expires_at,
            "error": "",
        }

    return {
        "status": "active",
        "days_until_expiration": days_until_expiration,
        "missing_scopes": [],
        "granted_scopes": granted_scopes,
        "token_expires_at": expires_at,
        "error": "",
    }

