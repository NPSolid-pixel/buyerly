"""Authenticated Meta connection lifecycle and unauthenticated OAuth callback."""

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete, or_, select, update

from api.auth import get_current_user
from api.deps import (
    ensure_workspace_write_access,
    get_user_workspace,
    get_user_workspace_member,
)
from core.config import settings
from core.currency import normalize_currency
from core.meta_tokens import (
    MetaTokenError,
    decrypt_meta_token,
    encrypt_meta_token,
)
from core.logging_config import redact_secrets
from core.ownership import entity_is_owned_by, owned_by
from core.rate_limit import rate_limit_dep
from core.timezones import canonical_timezone_name, resolve_account_clock
from database.db import async_session_maker
from database.models import (
    Account,
    MetaConnection,
    MetaConnectionAsset,
    MetaOAuthState,
    User,
)
from meta_api.client import MetaClient
from meta_api.oauth import MetaOAuthClient, MetaOAuthRemoteError, meta_token_expiry


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/meta", tags=["Meta OAuth"])
OAUTH_STATE_TTL_MINUTES = 10
meta_client = MetaClient()


class MetaAccountImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[str] = Field(min_length=1, max_length=500)


def _oauth_client() -> MetaOAuthClient:
    missing = [
        key
        for key, value in (
            ("META_APP_ID", settings.META_APP_ID),
            ("META_APP_SECRET", settings.META_APP_SECRET),
            ("META_LOGIN_CONFIG_ID", settings.META_LOGIN_CONFIG_ID),
            ("META_OAUTH_REDIRECT_URI", settings.META_OAUTH_REDIRECT_URI),
            ("META_TOKEN_ENCRYPTION_KEY", settings.META_TOKEN_ENCRYPTION_KEY),
        )
        if not str(value or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "meta_oauth_not_configured",
                "missing": missing,
            },
        )
    return MetaOAuthClient(
        app_id=settings.META_APP_ID,
        app_secret=settings.META_APP_SECRET,
        redirect_uri=settings.META_OAUTH_REDIRECT_URI,
        graph_version=settings.META_GRAPH_VERSION,
        login_config_id=settings.META_LOGIN_CONFIG_ID,
    )


def _safe_return_path(value: str) -> str:
    return value if value in {"/add-accounts", "/settings"} else "/add-accounts"


def _app_redirect(path: str, **params: str) -> str:
    base = settings.WEBAPP_URL.rstrip("/")
    suffix = f"?{urlencode(params)}" if params else ""
    return f"{base}{path}{suffix}" if base else f"{path}{suffix}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _owned_connection(session, connection_id: int, user: User) -> MetaConnection:
    connection = (
        await session.execute(
            select(MetaConnection).where(
                MetaConnection.id == connection_id,
                owned_by(MetaConnection, user),
            )
        )
    ).scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Подключение Meta не найдено")
    return connection


def _serialize_asset(asset: MetaConnectionAsset, imported_ids: set[str]) -> dict:
    return {
        "account_id": asset.meta_account_id,
        "name": asset.name,
        "business_id": asset.business_id,
        "business_name": asset.business_name,
        "account_status": asset.account_status,
        "currency": asset.currency,
        "timezone_name": asset.timezone_name,
        "imported": asset.meta_account_id in imported_ids,
        "discovered_at": asset.discovered_at.isoformat(),
    }


@router.get("/oauth/config")
async def oauth_config(user: User = Depends(get_current_user)):
    required = {
        "app_id": bool(settings.META_APP_ID.strip()),
        "app_secret": bool(settings.META_APP_SECRET.strip()),
        "login_config_id": bool(settings.META_LOGIN_CONFIG_ID.strip()),
        "redirect_uri": bool(settings.META_OAUTH_REDIRECT_URI.strip()),
        "encryption_key": bool(settings.META_TOKEN_ENCRYPTION_KEY.strip()),
    }
    return {
        "configured": all(required.values()),
        "checks": required,
        "graph_version": settings.META_GRAPH_VERSION,
        "redirect_uri": settings.META_OAUTH_REDIRECT_URI,
    }


@router.post(
    "/oauth/start",
    dependencies=[Depends(rate_limit_dep(limit=10, window_seconds=60, scope="oauth_start"))],
)
async def start_oauth(
    return_path: str = Query(default="/add-accounts"),
    user: User = Depends(get_current_user),
):
    client = _oauth_client()
    raw_state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(raw_state.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        session.add(
            MetaOAuthState(
                state_hash=state_hash,
                owner_user_id=user.id,
                return_path=_safe_return_path(return_path),
                expires_at=now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
            )
        )
        await session.commit()
    return {
        "authorization_url": client.build_authorization_url(raw_state),
        "expires_in_seconds": OAUTH_STATE_TTL_MINUTES * 60,
    }


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(
    state: str = Query(default="", max_length=512),
    code: str = Query(default="", max_length=4096),
    error: str = Query(default="", max_length=128),
):
    if error:
        return RedirectResponse(
            _app_redirect("/add-accounts", meta_status="cancelled"),
            status_code=303,
        )
    if not state or not code:
        return RedirectResponse(
            _app_redirect("/add-accounts", meta_status="invalid_callback"),
            status_code=303,
        )

    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        result = await session.execute(
            select(MetaOAuthState).where(MetaOAuthState.state_hash == state_hash)
        )
        oauth_state = result.scalar_one_or_none()
        if (
            not oauth_state
            or oauth_state.used_at is not None
            or _as_utc(oauth_state.expires_at) <= now
        ):
            return RedirectResponse(
                _app_redirect("/add-accounts", meta_status="expired_state"),
                status_code=303,
            )
        claim = await session.execute(
            update(MetaOAuthState)
            .where(
                MetaOAuthState.id == oauth_state.id,
                MetaOAuthState.used_at.is_(None),
            )
            .values(used_at=now)
        )
        if int(claim.rowcount or 0) != 1:
            await session.rollback()
            return RedirectResponse(
                _app_redirect("/add-accounts", meta_status="expired_state"),
                status_code=303,
            )
        await session.commit()
        owner_user_id = oauth_state.owner_user_id
        return_path = _safe_return_path(oauth_state.return_path)

    try:
        client = _oauth_client()
        result = await client.exchange_code(code)
        encrypted_token = encrypt_meta_token(result["access_token"])
        identity = result["identity"]
        debug = result["debug"]
        provider_user_id = str(identity["id"])
        provider_user_name = str(identity.get("name") or "Meta user")
        scopes = debug.get("scopes") if isinstance(debug.get("scopes"), list) else []

        async with async_session_maker() as session:
            owner = await session.get(User, owner_user_id)
            if not owner:
                raise MetaOAuthRemoteError("Buyerly user no longer exists")
            existing = (
                await session.execute(
                    select(MetaConnection).where(
                        MetaConnection.owner_user_id == owner_user_id,
                        MetaConnection.provider_user_id == provider_user_id,
                    )
                )
            ).scalar_one_or_none()
            connection = existing or MetaConnection(
                workspace_id=owner.active_workspace_id,
                owner_user_id=owner_user_id,
                provider_user_id=provider_user_id,
                access_token_encrypted=encrypted_token,
            )
            connection.provider_user_name = provider_user_name
            connection.access_token_encrypted = encrypted_token
            connection.granted_scopes = json.dumps(scopes, ensure_ascii=False)
            connection.token_expires_at = meta_token_expiry(debug)
            connection.status = "active"
            connection.last_error = ""
            connection.last_validated_at = now
            connection.connected_at = now
            if not existing:
                session.add(connection)
            await session.commit()
            await session.refresh(connection)
            connection_id = connection.id
    except HTTPException:
        return RedirectResponse(
            _app_redirect(return_path, meta_status="not_configured"),
            status_code=303,
        )
    except (MetaOAuthRemoteError, MetaTokenError):
        logger.warning("Meta OAuth callback failed after state validation")
        return RedirectResponse(
            _app_redirect(return_path, meta_status="connection_failed"),
            status_code=303,
        )

    return RedirectResponse(
        _app_redirect(
            return_path,
            meta_status="connected",
            meta_connection=str(connection_id),
        ),
        status_code=303,
    )


@router.get("/connections")
async def list_connections(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(MetaConnection)
                .where(owned_by(MetaConnection, user))
                .order_by(MetaConnection.updated_at.desc())
            )
        ).scalars().all()
    return [
        {
            "id": item.id,
            "provider_user_id": item.provider_user_id,
            "provider_user_name": item.provider_user_name,
            "status": item.status,
            "granted_scopes": json.loads(item.granted_scopes or "[]"),
            "token_expires_at": item.token_expires_at.isoformat()
            if item.token_expires_at
            else None,
            "connected_at": item.connected_at.isoformat(),
        }
        for item in rows
    ]


@router.post("/connections/{connection_id}/discover")
async def discover_accounts(
    connection_id: int,
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        connection = await _owned_connection(session, connection_id, user)
        try:
            access_token = decrypt_meta_token(connection.access_token_encrypted)
            client = _oauth_client()
            debug = await client.debug_token(access_token)
            discovered = await client.discover_ad_accounts(access_token)
        except (MetaOAuthRemoteError, MetaTokenError) as exc:
            connection.status = "needs_reconnect"
            connection.last_error = str(exc)
            await session.commit()
            raise HTTPException(
                status_code=502,
                detail="Meta не подтвердила подключение. Подключите профиль заново.",
            ) from exc

        existing_assets = {
            item.meta_account_id: item
            for item in (
                await session.execute(
                    select(MetaConnectionAsset).where(
                        MetaConnectionAsset.connection_id == connection.id
                    )
                )
            ).scalars().all()
        }
        visible_ids: set[str] = set()
        for raw in discovered:
            raw_id = str(raw.get("id") or raw.get("account_id") or "").strip()
            if not raw_id:
                continue
            account_id = raw_id if raw_id.startswith("act_") else f"act_{raw_id}"
            business = raw.get("business") if isinstance(raw.get("business"), dict) else {}
            asset = existing_assets.get(account_id) or MetaConnectionAsset(
                connection_id=connection.id,
                owner_user_id=connection.owner_user_id,
                meta_account_id=account_id,
            )
            asset.name = str(raw.get("name") or account_id)
            asset.business_id = str(business.get("id") or "")
            asset.business_name = str(business.get("name") or "Без Business Manager")
            try:
                asset.account_status = int(raw.get("account_status") or 0)
            except (TypeError, ValueError):
                asset.account_status = 0
            asset.currency = normalize_currency(raw.get("currency"))
            asset.timezone_name = canonical_timezone_name(raw.get("timezone_name")) or "UNKNOWN"
            asset.discovered_at = now
            if account_id not in existing_assets:
                session.add(asset)
            visible_ids.add(account_id)

        stale_ids = set(existing_assets) - visible_ids
        if stale_ids:
            await session.execute(
                delete(MetaConnectionAsset).where(
                    MetaConnectionAsset.connection_id == connection.id,
                    MetaConnectionAsset.meta_account_id.in_(stale_ids),
                )
            )
        scopes = debug.get("scopes") if isinstance(debug.get("scopes"), list) else []
        connection.granted_scopes = json.dumps(scopes, ensure_ascii=False)
        connection.status = "active"
        connection.last_error = ""
        connection.last_validated_at = now
        await session.commit()

    return await list_connection_assets(connection_id, user)


@router.get("/connections/{connection_id}/assets")
async def list_connection_assets(
    connection_id: int,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        connection = await _owned_connection(session, connection_id, user)
        ws = await get_user_workspace(session, user)
        assets = (
            await session.execute(
                select(MetaConnectionAsset)
                .where(MetaConnectionAsset.connection_id == connection.id)
                .order_by(
                    MetaConnectionAsset.business_name.asc(),
                    MetaConnectionAsset.name.asc(),
                )
            )
        ).scalars().all()
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        imported_ids = set(
            (
                await session.execute(
                    select(Account.account_id).where(scope_clause)
                )
            ).scalars().all()
        )
        return {
            "connection": {
                "id": connection.id,
                "provider_user_name": connection.provider_user_name,
                "status": connection.status,
            },
            "accounts": [_serialize_asset(asset, imported_ids) for asset in assets],
            "count": len(assets),
            "imported_count": sum(
                1 for asset in assets if asset.meta_account_id in imported_ids
            ),
        }


@router.post("/connections/{connection_id}/import")
async def import_accounts(
    connection_id: int,
    payload: MetaAccountImportRequest,
    user: User = Depends(get_current_user),
):
    requested_ids: list[str] = []
    seen: set[str] = set()
    for raw_id in payload.account_ids:
        value = str(raw_id or "").strip()
        account_id = value if value.startswith("act_") else f"act_{value}"
        numeric_id = account_id.removeprefix("act_")
        if not numeric_id.isdigit() or not 5 <= len(numeric_id) <= 25:
            raise HTTPException(status_code=422, detail=f"Некорректный ID кабинета: {value}")
        if account_id not in seen:
            seen.add(account_id)
            requested_ids.append(account_id)

    added: list[dict] = []
    errors: list[dict] = []
    async with async_session_maker() as session:
        connection = await _owned_connection(session, connection_id, user)
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "импорта рекламных кабинетов")
        caller_role = member.role if member else "buyer"

        if connection.status != "active":
            raise HTTPException(status_code=409, detail="Сначала переподключите профиль Meta")
        try:
            access_token = decrypt_meta_token(connection.access_token_encrypted)
        except MetaTokenError as exc:
            raise HTTPException(status_code=503, detail="Ключ подключения Meta недоступен") from exc

        assets = {
            item.meta_account_id: item
            for item in (
                await session.execute(
                    select(MetaConnectionAsset).where(
                        MetaConnectionAsset.connection_id == connection.id,
                        MetaConnectionAsset.meta_account_id.in_(requested_ids),
                    )
                )
            ).scalars().all()
        }
        missing_ids = [account_id for account_id in requested_ids if account_id not in assets]
        if missing_ids:
            raise HTTPException(
                status_code=422,
                detail="Сначала обновите список доступных кабинетов Meta",
            )

        for account_id in requested_ids:
            asset = assets[account_id]
            try:
                account_info = await meta_client.get_account_info(account_id, access_token)
                timezone_name = canonical_timezone_name(account_info.get("timezone_name"))
                if resolve_account_clock(timezone_name) is None:
                    raise RuntimeError("Meta не вернула поддерживаемый часовой пояс")
                currency = normalize_currency(account_info.get("currency"))
                async with session.begin_nested():
                    existing = (
                        await session.execute(
                            select(Account).where(Account.account_id == account_id)
                        )
                    ).scalar_one_or_none()
                    if existing:
                        if (
                            existing.workspace_id is not None
                            and ws is not None
                            and existing.workspace_id != ws.id
                        ):
                            raise RuntimeError("Кабинет уже подключён в другом рабочем пространстве.")
                        if (
                            existing.workspace_id == (ws.id if ws else None)
                            and not entity_is_owned_by(existing, user)
                            and caller_role not in ("owner", "admin")
                        ):
                            raise RuntimeError("Кабинет добавлен другим байером в этом воркспейсе. Изменение разрешено только владельцу или администратору воркспейса.")

                    status_code = int(account_info.get("account_status") or 0)
                    status_label = str(account_info.get("status_label") or f"Статус #{status_code}")
                    account = existing or Account(
                        account_id=account_id,
                        name=str(account_info.get("name") or asset.name or account_id),
                        workspace_id=ws.id if ws else None,
                        owner_user_id=user.id,
                        access_token="",
                        rules_enabled=False,
                        is_active=True,
                    )
                    if existing and existing.timezone_name != timezone_name:
                        existing.last_day_start_date = ""
                    account.name = str(account_info.get("name") or asset.name or account_id)
                    account.workspace_id = ws.id if ws else account.workspace_id
                    account.owner_user_id = user.id
                    account.batch_name = asset.business_name if asset.business_id else ""
                    account.access_token = ""
                    account.meta_connection_id = connection.id
                    account.timezone_name = timezone_name
                    account.currency = currency
                    account.account_status = status_code
                    account.status_label = status_label
                    account.is_active = True
                    if not existing:
                        session.add(account)
                    await session.flush()
                added.append(
                    {
                        "account_id": account_id,
                        "name": account.name,
                        "business_name": asset.business_name,
                        "timezone_name": timezone_name,
                        "currency": currency,
                    }
                )
            except Exception as exc:
                logger.warning("Meta account import failed for %s", account_id)
                errors.append(
                    {"account_id": account_id, "error": redact_secrets(str(exc))}
                )

        await session.commit()
    return {
        "success_count": len(added),
        "error_count": len(errors),
        "added": added,
        "errors": errors,
    }
