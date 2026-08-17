"""Authenticated Meta connection lifecycle and unauthenticated OAuth callback."""

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from api.auth import get_current_user
from core.config import settings
from core.meta_tokens import MetaTokenError, encrypt_meta_token
from core.ownership import owned_by
from database.db import async_session_maker
from database.models import MetaConnection, MetaOAuthState, TelegramUser
from meta_api.oauth import MetaOAuthClient, MetaOAuthRemoteError, meta_token_expiry


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/meta", tags=["Meta OAuth"])
OAUTH_STATE_TTL_MINUTES = 10


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


@router.get("/oauth/config")
async def oauth_config(user: TelegramUser = Depends(get_current_user)):
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


@router.post("/oauth/start")
async def start_oauth(
    return_path: str = Query(default="/add-accounts"),
    user: TelegramUser = Depends(get_current_user),
):
    client = _oauth_client()
    raw_state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(raw_state.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        session.add(
            MetaOAuthState(
                state_hash=state_hash,
                owner_id=str(user.telegram_id or ""),
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
        owner_id = oauth_state.owner_id
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
            owner = await session.get(TelegramUser, owner_user_id)
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
                owner_id=owner_id,
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
async def list_connections(user: TelegramUser = Depends(get_current_user)):
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
