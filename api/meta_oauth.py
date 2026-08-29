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
from sqlalchemy.exc import IntegrityError

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
    AuditEvent,
    MetaConnection,
    MetaConnectionAsset,
    MetaConnectionInvite,
    MetaOAuthState,
    User,
)
from meta_api.client import MetaClient
from meta_api.oauth import (
    REQUIRED_META_SCOPES,
    MetaOAuthClient,
    MetaOAuthRemoteError,
    evaluate_meta_connection_health,
    meta_token_expiry,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/meta", tags=["Meta OAuth"])
OAUTH_STATE_TTL_MINUTES = 10
INVITE_DEFAULT_TTL_HOURS = 24
meta_client = MetaClient()


class MetaAccountImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[str] = Field(min_length=1, max_length=500)


class CreateMetaInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=100, description="Human label for this invite, e.g. 'Buyer Ivan — Profile #3'")
    expires_in_hours: int = Field(default=INVITE_DEFAULT_TTL_HOURS, ge=1, le=168, description="TTL in hours (1–168)")


class MetaInviteItem(BaseModel):
    id: int
    label: str
    status: str
    token_prefix: str
    invite_url: str
    created_at: str
    expires_at: str
    used_at: str | None
    created_by_user_name: str | None


class PublicMetaInviteInfoResponse(BaseModel):
    valid: bool
    status: str
    workspace_name: str
    workspace_logo_url: str | None
    label: str
    inviter_name: str | None


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return decoded if isinstance(decoded, list) else []
    return []


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
    path = str(value or "").strip()
    if path.endswith("/facebook-accounts") or path.endswith("/add-accounts") or path.endswith("/settings"):
        return path
    return "/facebook-accounts"


def _app_redirect(path: str, **params: str) -> str:
    base = settings.WEBAPP_URL.rstrip("/")
    suffix = f"?{urlencode(params)}" if params else ""
    return f"{base}{path}{suffix}" if base else f"{path}{suffix}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _workspace_connection(
    session,
    connection_id: int,
    user: User,
    *,
    require_write: bool = False,
) -> tuple[MetaConnection, object, object]:
    ws, member = await get_user_workspace_member(session, user)
    if not ws:
        raise HTTPException(status_code=404, detail="Рабочее пространство не найдено")
    if require_write:
        ensure_workspace_write_access(user, member, "изменения подключения Meta")
    caller_role = member.role if member else "buyer"
    stmt = select(MetaConnection).where(
        MetaConnection.id == connection_id,
        MetaConnection.workspace_id == ws.id,
    )
    if caller_role not in ("owner", "admin"):
        stmt = stmt.where(MetaConnection.owner_user_id == user.id)

    connection = (await session.execute(stmt)).scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Подключение Meta не найдено")
    return connection, ws, member


def _serialize_asset(
    asset: MetaConnectionAsset,
    existing_accounts: dict[str, Account] | set[str],
    current_connection_id: int | None = None,
) -> dict:
    if isinstance(existing_accounts, set):
        is_in_set = asset.meta_account_id in existing_accounts
        return {
            "account_id": asset.meta_account_id,
            "name": asset.name,
            "business_id": asset.business_id,
            "business_name": asset.business_name,
            "account_status": asset.account_status,
            "currency": asset.currency,
            "timezone_name": asset.timezone_name,
            "imported": is_in_set,
            "import_status": "this_connection" if is_in_set else "not_imported",
            "can_migrate": False,
            "rules_count": 0,
            "rules_enabled": False,
            "custom_name": "",
            "discovered_at": asset.discovered_at.isoformat(),
        }

    existing = existing_accounts.get(asset.meta_account_id)
    if existing is None:
        import_status = "not_imported"
        can_migrate = False
        rules_count = 0
        rules_enabled = False
        custom_name = ""
        imported = False
    elif current_connection_id is not None and existing.meta_connection_id == current_connection_id:
        import_status = "this_connection"
        can_migrate = False
        rules_list = _json_list(existing.active_rules)
        rules_count = len(rules_list)
        rules_enabled = bool(existing.rules_enabled)
        custom_name = existing.custom_name or ""
        imported = True
    elif existing.meta_connection_id is None:
        import_status = "manual_token"
        can_migrate = True
        rules_list = _json_list(existing.active_rules)
        rules_count = len(rules_list)
        rules_enabled = bool(existing.rules_enabled)
        custom_name = existing.custom_name or ""
        imported = False
    else:
        import_status = "other_connection"
        can_migrate = True
        rules_list = _json_list(existing.active_rules)
        rules_count = len(rules_list)
        rules_enabled = bool(existing.rules_enabled)
        custom_name = existing.custom_name or ""
        imported = False

    return {
        "account_id": asset.meta_account_id,
        "name": asset.name,
        "business_id": asset.business_id,
        "business_name": asset.business_name,
        "account_status": asset.account_status,
        "currency": asset.currency,
        "timezone_name": asset.timezone_name,
        "imported": imported,
        "import_status": import_status,
        "can_migrate": can_migrate,
        "rules_count": rules_count,
        "rules_enabled": rules_enabled,
        "custom_name": custom_name,
        "discovered_at": asset.discovered_at.isoformat(),
    }



# ---------------------------------------------------------------------------
# Invite endpoints
# ---------------------------------------------------------------------------

def _invite_url(token: str) -> str:
    """Build public invite URL from raw token."""
    base = settings.WEBAPP_URL.rstrip("/")
    return f"{base}/connect/meta/{token}"


def _serialize_invite(invite: MetaConnectionInvite, invite_url: str, creator_name: str | None = None) -> dict:
    return {
        "id": invite.id,
        "label": invite.label or "",
        "status": invite.status,
        "token_prefix": invite.token_prefix,
        "invite_url": invite_url,
        "created_at": invite.created_at.isoformat(),
        "expires_at": invite.expires_at.isoformat(),
        "used_at": invite.used_at.isoformat() if invite.used_at else None,
        "created_by_user_name": creator_name,
    }


@router.post(
    "/invites",
    dependencies=[Depends(rate_limit_dep(limit=20, window_seconds=60, scope="meta_invite_create"))],
)
async def create_meta_invite(
    payload: CreateMetaInviteRequest,
    user: User = Depends(get_current_user),
):
    """Create a one-time invite link for connecting a Facebook profile without sharing passwords."""
    now = datetime.now(timezone.utc)
    raw_token = "inv_fb_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    token_prefix = raw_token[:16] + "..."

    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        if not ws:
            raise HTTPException(status_code=404, detail="Рабочее пространство не найдено")
        ensure_workspace_write_access(user, member, "создания инвайт-ссылки Meta")

        expires_at = now + timedelta(hours=payload.expires_in_hours)
        invite = MetaConnectionInvite(
            workspace_id=ws.id,
            created_by_user_id=user.id,
            token_hash=token_hash,
            token_prefix=token_prefix,
            label=payload.label.strip(),
            status="pending",
            expires_at=expires_at,
        )
        session.add(invite)
        session.add(
            AuditEvent(
                workspace_id=ws.id,
                owner_user_id=user.id,
                actor_type="user",
                actor_id=str(user.telegram_id or user.id),
                category="MANUAL_ACTION",
                event_type="META_INVITE_CREATED",
                status="SUCCESS",
                action="CREATE_INVITE",
                message=f"Создана инвайт-ссылка для подключения Facebook-профиля. Метка: «{payload.label}».",
                details={"label": payload.label, "expires_in_hours": payload.expires_in_hours},
                correlation_id=secrets.token_hex(16),
            )
        )
        await session.commit()
        await session.refresh(invite)

    url = _invite_url(raw_token)
    return {
        **_serialize_invite(invite, url, user.full_name or user.username),
        "invite_url": url,
        "raw_token": raw_token,  # Only returned on creation; never stored or re-exposed
    }


@router.get("/invites")
async def list_meta_invites(user: User = Depends(get_current_user)):
    """List all invite links for the current workspace (admin/owner/buyer)."""
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        if not ws:
            return []
        caller_role = member.role if member else "buyer"
        stmt = select(MetaConnectionInvite, User).outerjoin(
            User, MetaConnectionInvite.created_by_user_id == User.id
        ).where(MetaConnectionInvite.workspace_id == ws.id)
        if caller_role not in ("owner", "admin"):
            stmt = stmt.where(MetaConnectionInvite.created_by_user_id == user.id)
        stmt = stmt.order_by(MetaConnectionInvite.created_at.desc())
        rows = (await session.execute(stmt)).all()

    now = datetime.now(timezone.utc)
    result = []
    for invite, creator in rows:
        # Mark expired invites without writing to DB (lazy expiry display)
        displayed_status = invite.status
        if invite.status == "pending" and invite.expires_at.replace(tzinfo=timezone.utc) <= now:
            displayed_status = "expired"
        url = _invite_url("[hidden]")  # Don't re-expose raw token
        result.append({
            "id": invite.id,
            "label": invite.label or "",
            "status": displayed_status,
            "token_prefix": invite.token_prefix,
            "invite_url": None,  # Raw token not stored — only shown on creation
            "created_at": invite.created_at.isoformat(),
            "expires_at": invite.expires_at.isoformat(),
            "used_at": invite.used_at.isoformat() if invite.used_at else None,
            "created_by_user_name": (creator.full_name or creator.username) if creator else None,
        })
    return result


@router.delete("/invites/{invite_id}")
async def revoke_meta_invite(
    invite_id: int,
    user: User = Depends(get_current_user),
):
    """Revoke (cancel) an invite link before it is used."""
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        if not ws:
            raise HTTPException(status_code=404, detail="Рабочее пространство не найдено")

        stmt = select(MetaConnectionInvite).where(
            MetaConnectionInvite.id == invite_id,
            MetaConnectionInvite.workspace_id == ws.id,
        )
        caller_role = member.role if member else "buyer"
        if caller_role not in ("owner", "admin"):
            stmt = stmt.where(MetaConnectionInvite.created_by_user_id == user.id)

        invite = (await session.execute(stmt)).scalar_one_or_none()
        if not invite:
            raise HTTPException(status_code=404, detail="Инвайт-ссылка не найдена")
        if invite.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Нельзя отозвать инвайт со статусом «{invite.status}»",
            )

        invite.status = "revoked"
        session.add(
            AuditEvent(
                workspace_id=ws.id,
                owner_user_id=user.id,
                actor_type="user",
                actor_id=str(user.telegram_id or user.id),
                category="MANUAL_ACTION",
                event_type="META_INVITE_REVOKED",
                status="SUCCESS",
                action="REVOKE_INVITE",
                message=f"Инвайт-ссылка «{invite.label}» отозвана.",
                details={"invite_id": invite.id, "label": invite.label},
                correlation_id=secrets.token_hex(16),
            )
        )
        await session.commit()
    return {"success": True, "invite_id": invite_id, "status": "revoked"}


@router.get(
    "/invites/public/{token}",
    dependencies=[Depends(rate_limit_dep(limit=30, window_seconds=60, scope="meta_invite_public"))],
)
async def public_invite_info(token: str):
    """Public endpoint (no auth): validate invite token and return safe workspace info for the landing page."""
    from database.models import Workspace  # local import to avoid circular
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        invite = (
            await session.execute(
                select(MetaConnectionInvite).where(MetaConnectionInvite.token_hash == token_hash)
            )
        ).scalar_one_or_none()

        if not invite:
            raise HTTPException(status_code=404, detail="Инвайт-ссылка не найдена или уже использована")

        # Determine effective status
        if invite.status == "pending" and _as_utc(invite.expires_at) <= now:
            effective_status = "expired"
        else:
            effective_status = invite.status

        ws = await session.get(Workspace, invite.workspace_id)
        creator = await session.get(User, invite.created_by_user_id) if invite.created_by_user_id else None

    return PublicMetaInviteInfoResponse(
        valid=(effective_status == "pending"),
        status=effective_status,
        workspace_name=ws.name if ws else "Buyerly",
        workspace_logo_url=ws.logo_url if ws else None,
        label=invite.label or "",
        inviter_name=(creator.full_name or creator.username) if creator else None,
    )


@router.get(
    "/oauth/invite/{token}",
    include_in_schema=False,
    dependencies=[Depends(rate_limit_dep(limit=10, window_seconds=60, scope="meta_invite_oauth"))],
)
async def start_oauth_via_invite(token: str):
    """Public endpoint (no auth): start Meta OAuth flow triggered by an invite link."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        invite = (
            await session.execute(
                select(MetaConnectionInvite).where(MetaConnectionInvite.token_hash == token_hash)
            )
        ).scalar_one_or_none()

        if not invite or invite.status != "pending" or _as_utc(invite.expires_at) <= now:
            base = settings.WEBAPP_URL.rstrip("/")
            return RedirectResponse(
                f"{base}/connect/meta/{token}?meta_status=invite_invalid",
                status_code=303,
            )

        client = _oauth_client()
        raw_state = secrets.token_urlsafe(32)
        state_hash = hashlib.sha256(raw_state.encode("utf-8")).hexdigest()

        return_path = f"/connect/meta/success"
        session.add(
            MetaOAuthState(
                state_hash=state_hash,
                workspace_id=invite.workspace_id,
                owner_user_id=invite.created_by_user_id,
                return_path=return_path,
                reconnect_connection_id=None,
                invite_id=invite.id,
                expires_at=now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
            )
        )
        await session.commit()

    return RedirectResponse(client.build_authorization_url(raw_state), status_code=303)


# ---------------------------------------------------------------------------
# OAuth config and standard flow
# ---------------------------------------------------------------------------

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
    return_path: str = Query(default="/facebook-accounts"),
    reconnect_connection_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
):
    client = _oauth_client()
    raw_state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(raw_state.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        if not ws:
            raise HTTPException(status_code=404, detail="Рабочее пространство не найдено")
        ensure_workspace_write_access(user, member, "подключения Facebook-профиля")

        if reconnect_connection_id:
            await _workspace_connection(
                session,
                reconnect_connection_id,
                user,
                require_write=True,
            )

        session.add(
            MetaOAuthState(
                state_hash=state_hash,
                workspace_id=ws.id,
                owner_user_id=user.id,
                return_path=_safe_return_path(return_path),
                reconnect_connection_id=reconnect_connection_id,
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
            _app_redirect("/facebook-accounts", meta_status="cancelled"),
            status_code=303,
        )
    if not state or not code:
        return RedirectResponse(
            _app_redirect("/facebook-accounts", meta_status="invalid_callback"),
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
                _app_redirect("/facebook-accounts", meta_status="expired_state"),
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
                _app_redirect("/facebook-accounts", meta_status="expired_state"),
                status_code=303,
            )
        await session.commit()
        owner_user_id = oauth_state.owner_user_id
        target_workspace_id = oauth_state.workspace_id
        return_path = _safe_return_path(oauth_state.return_path)
        reconnect_id = oauth_state.reconnect_connection_id
        invite_id = oauth_state.invite_id  # Track invite if present

    try:
        client = _oauth_client()
        result = await client.exchange_code(code)
        encrypted_token = encrypt_meta_token(result["access_token"])
        identity = result["identity"]
        debug = result["debug"]
        provider_user_id = str(identity["id"])
        provider_user_name = str(identity.get("name") or "Meta user")
        health = evaluate_meta_connection_health(debug, now)

        async with async_session_maker() as session:
            owner = await session.get(User, owner_user_id)
            if not owner:
                raise MetaOAuthRemoteError("Buyerly user no longer exists")

            ws, member = await get_user_workspace_member(
                session,
                owner,
                workspace_id=target_workspace_id,
            )
            if not ws or not member or member.role == "viewer":
                return RedirectResponse(
                    _app_redirect(return_path, meta_status="permission_denied"),
                    status_code=303,
                )

            if reconnect_id:
                reconnect_target = await session.get(MetaConnection, reconnect_id)
                if (
                    reconnect_target
                    and reconnect_target.workspace_id == target_workspace_id
                    and (
                        reconnect_target.owner_user_id == owner_user_id
                        or member.role in ("owner", "admin")
                    )
                ):
                    if reconnect_target.provider_user_id != provider_user_id:
                        return RedirectResponse(
                            _app_redirect(
                                return_path,
                                meta_status="identity_mismatch",
                            ),
                            status_code=303,
                        )
                    connection = reconnect_target
                else:
                    reconnect_id = None

            if not reconnect_id:
                existing = (
                    await session.execute(
                        select(MetaConnection).where(
                            MetaConnection.workspace_id == target_workspace_id,
                            MetaConnection.provider_user_id == provider_user_id,
                        )
                    )
                ).scalar_one_or_none()
                connection = existing or MetaConnection(
                    workspace_id=target_workspace_id,
                    owner_user_id=owner_user_id,
                    provider_user_id=provider_user_id,
                )
                if not existing:
                    connection.connected_at = now
                    session.add(connection)

            connection.provider_user_name = provider_user_name
            connection.access_token_encrypted = encrypted_token
            connection.granted_scopes = health["granted_scopes"]
            connection.token_expires_at = health["token_expires_at"]
            connection.status = health["status"]
            connection.last_error = health["error"]
            connection.last_validated_at = now

            if reconnect_id:
                # Re-activate accounts linked to this connection in this workspace
                await session.execute(
                    update(Account)
                    .where(
                        Account.workspace_id == target_workspace_id,
                        Account.meta_connection_id == connection.id,
                    )
                    .values(is_active=True, status_label="Активен")
                )

            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # Handle concurrent OAuth callback creation race
                existing = (
                    await session.execute(
                        select(MetaConnection).where(
                            MetaConnection.workspace_id == target_workspace_id,
                            MetaConnection.provider_user_id == provider_user_id,
                        )
                    )
                ).scalar_one()
                existing.provider_user_name = provider_user_name
                existing.access_token_encrypted = encrypted_token
                existing.granted_scopes = health["granted_scopes"]
                existing.token_expires_at = health["token_expires_at"]
                existing.status = health["status"]
                existing.last_error = health["error"]
                existing.last_validated_at = now
                connection = existing

            connection_id = connection.id

            # Claim the invite in the same transaction as the connection write. This
            # prevents a revoked/expired invite from completing after OAuth started.
            if invite_id:
                invite_claim = await session.execute(
                    update(MetaConnectionInvite)
                    .where(
                        MetaConnectionInvite.id == invite_id,
                        MetaConnectionInvite.workspace_id == target_workspace_id,
                        MetaConnectionInvite.status == "pending",
                        MetaConnectionInvite.expires_at > now,
                    )
                    .values(
                        status="used",
                        used_at=now,
                        connected_meta_id=connection_id,
                    )
                )
                if int(invite_claim.rowcount or 0) != 1:
                    await session.rollback()
                    return RedirectResponse(
                        _app_redirect(return_path, meta_status="invite_invalid"),
                        status_code=303,
                    )

            session.add(
                AuditEvent(
                    workspace_id=target_workspace_id,
                    owner_user_id=owner_user_id,
                    actor_type="user",
                    actor_id=str(owner.telegram_id or owner.id),
                    category="MANUAL_ACTION",
                    event_type="META_CONNECTION_RECONNECTED" if reconnect_id else "META_CONNECTION_CONNECTED",
                    status="SUCCESS",
                    action="RECONNECT_CONNECTION" if reconnect_id else "CONNECT_CONNECTION",
                    message="Подключение Meta успешно обновлено." if reconnect_id else "Подключение Meta успешно создано.",
                    details={
                        "provider_user_id": provider_user_id,
                        "provider_user_name": provider_user_name,
                        "status": connection.status,
                    },
                    correlation_id=secrets.token_hex(16),
                )
            )
            await session.commit()
            await session.refresh(connection)

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
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        if not ws:
            return []
        caller_role = member.role if member else "buyer"
        stmt = select(MetaConnection).where(MetaConnection.workspace_id == ws.id)
        if caller_role not in ("owner", "admin"):
            stmt = stmt.where(MetaConnection.owner_user_id == user.id)
        stmt = stmt.order_by(MetaConnection.updated_at.desc())
        rows = (await session.execute(stmt)).scalars().all()
        connection_ids = [item.id for item in rows]
        linked_accounts = []
        if connection_ids:
            linked_accounts = (
                await session.execute(
                    select(Account).where(
                        Account.workspace_id == ws.id,
                        Account.meta_connection_id.in_(connection_ids),
                    )
                )
            ).scalars().all()
        accounts_by_connection: dict[int, list[Account]] = {}
        for account in linked_accounts:
            accounts_by_connection.setdefault(account.meta_connection_id, []).append(account)
    output = []
    for item in rows:
        scopes = _json_list(item.granted_scopes)
        item_accounts = accounts_by_connection.get(item.id, [])
        business_managers = sorted(
            {
                str(account.batch_name).strip()
                for account in item_accounts
                if str(account.batch_name or "").strip()
            }
        )
        days_left: int | None = None
        if item.token_expires_at:
            diff = (_as_utc(item.token_expires_at) - now).total_seconds()
            days_left = max(0, int(diff // 86400))
        output.append(
            {
                "id": item.id,
                "provider_user_id": item.provider_user_id,
                "provider_user_name": item.provider_user_name,
                "status": item.status,
                "granted_scopes": scopes,
                "missing_scopes": [s for s in REQUIRED_META_SCOPES if s not in scopes],
                "connected_account_count": len(item_accounts),
                "business_manager_count": len(business_managers),
                "business_managers": business_managers,
                "token_expires_at": item.token_expires_at.isoformat()
                if item.token_expires_at
                else None,
                "days_until_expiration": days_left,
                "last_validated_at": item.last_validated_at.isoformat()
                if item.last_validated_at
                else None,
                "last_error": item.last_error,
                "connected_at": item.connected_at.isoformat(),
            }
        )
    return output


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: int,
    revoke_permissions: bool = Query(default=False),
    user: User = Depends(get_current_user),
):
    """Delete Buyerly credentials and optionally revoke Meta permissions after explicit confirmation."""

    async with async_session_maker() as session:
        connection, ws, _ = await _workspace_connection(
            session,
            connection_id,
            user,
            require_write=True,
        )

        permissions_revoked: bool | None = None
        if revoke_permissions:
            shared_profile_connection_id = (
                await session.execute(
                    select(MetaConnection.id)
                    .where(
                        MetaConnection.provider_user_id == connection.provider_user_id,
                        MetaConnection.id != connection.id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if shared_profile_connection_id is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Этот Facebook-профиль используется в другом workspace Buyerly. "
                        "Отзыв разрешений Meta заблокирован: используйте обычное отключение, "
                        "чтобы не нарушить другое подключение."
                    ),
                )
            permissions_revoked = False
            try:
                raw_token = decrypt_meta_token(connection.access_token_encrypted)
                client = _oauth_client()
                permissions_revoked = await client.revoke_permissions(raw_token)
            except Exception:
                logger.info(
                    "Meta Graph API permission revocation failed for connection %s",
                    connection_id,
                )
            if not permissions_revoked:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Meta не подтвердила отзыв разрешений. Подключение сохранено, "
                        "чтобы вы могли повторить действие."
                    ),
                )

        linked_accounts = (
            await session.execute(
                select(Account).where(
                    Account.workspace_id == ws.id,
                    Account.meta_connection_id == connection.id,
                )
            )
        ).scalars().all()
        account_ids = [account.account_id for account in linked_accounts]
        for account in linked_accounts:
            account.meta_connection_id = None
            account.rules_enabled = False
            account.is_active = False
            account.status_label = "Требуется подключение Meta"

        session.add(
            AuditEvent(
                workspace_id=ws.id,
                owner_user_id=connection.owner_user_id,
                actor_type="user",
                actor_id=str(user.telegram_id or user.id),
                category="MANUAL_ACTION",
                event_type="META_CONNECTION_DISCONNECTED",
                status="SUCCESS",
                action=(
                    "REVOKE_AND_DELETE_CONNECTION"
                    if revoke_permissions
                    else "DELETE_CONNECTION"
                ),
                message=(
                    "Подключение Meta удалено; связанные кабинеты безопасно отключены."
                ),
                before_state={
                    "connection_id": connection.id,
                    "status": connection.status,
                    "linked_account_count": len(linked_accounts),
                },
                after_state={
                    "connection_deleted": True,
                    "accounts_disabled": True,
                    "permissions_revoke_requested": revoke_permissions,
                    "permissions_revoked": permissions_revoked,
                },
                details={
                    "provider_user_id": connection.provider_user_id,
                    "account_ids": account_ids,
                },
                correlation_id=secrets.token_hex(16),
            )
        )
        await session.delete(connection)
        await session.commit()

    return {
        "success": True,
        "connection_id": connection_id,
        "detached_account_count": len(account_ids),
        "detached_account_ids": account_ids,
        "permissions_revoke_requested": revoke_permissions,
        "permissions_revoked": permissions_revoked,
        "message": (
            "Подключение Buyerly удалено. Разрешения Meta отозваны."
            if permissions_revoked is True
            else "Подключение Buyerly удалено. Связанные кабинеты отключены до переподключения."
        ),
    }


@router.post("/connections/{connection_id}/validate")
async def validate_connection(
    connection_id: int,
    user: User = Depends(get_current_user),
):
    """Explicitly validate Meta token and update health status."""
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        connection, ws, _ = await _workspace_connection(
            session,
            connection_id,
            user,
            require_write=True,
        )
        try:
            access_token = decrypt_meta_token(connection.access_token_encrypted)
            client = _oauth_client()
            debug = await client.debug_token(access_token)
            health = evaluate_meta_connection_health(debug, now)
        except (MetaOAuthRemoteError, MetaTokenError) as exc:
            health = {
                "status": "needs_reconnect",
                "days_until_expiration": None,
                "missing_scopes": list(REQUIRED_META_SCOPES),
                "granted_scopes": [],
                "token_expires_at": None,
                "error": redact_secrets(str(exc)),
            }

        connection.status = health["status"]
        connection.granted_scopes = health["granted_scopes"]
        connection.token_expires_at = health["token_expires_at"]
        connection.last_error = health["error"]
        connection.last_validated_at = now
        await session.commit()

        return {
            "connection_id": connection.id,
            "provider_user_id": connection.provider_user_id,
            "provider_user_name": connection.provider_user_name,
            "status": connection.status,
            "days_until_expiration": health["days_until_expiration"],
            "granted_scopes": health["granted_scopes"],
            "missing_scopes": health["missing_scopes"],
            "token_expires_at": connection.token_expires_at.isoformat()
            if connection.token_expires_at
            else None,
            "last_validated_at": connection.last_validated_at.isoformat(),
            "last_error": connection.last_error,
        }


@router.post("/connections/{connection_id}/discover")
async def discover_accounts(
    connection_id: int,
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        connection, _, _ = await _workspace_connection(
            session,
            connection_id,
            user,
            require_write=True,
        )
        try:
            access_token = decrypt_meta_token(connection.access_token_encrypted)
            client = _oauth_client()
            debug = await client.debug_token(access_token)
            discovered = await client.discover_ad_accounts(access_token)
        except (MetaOAuthRemoteError, MetaTokenError) as exc:
            connection.status = "needs_reconnect"
            connection.last_error = redact_secrets(str(exc))
            connection.last_validated_at = now
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

        health = evaluate_meta_connection_health(debug, now)
        connection.granted_scopes = health["granted_scopes"]
        connection.token_expires_at = health["token_expires_at"]
        connection.status = health["status"]
        connection.last_error = health["error"]
        connection.last_validated_at = now
        await session.commit()

    return await list_connection_assets(connection_id, user)


@router.get("/connections/{connection_id}/assets")
async def list_connection_assets(
    connection_id: int,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        connection, ws, _ = await _workspace_connection(
            session,
            connection_id,
            user,
        )
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
        existing_accounts = {
            acc.account_id: acc
            for acc in (
                await session.execute(
                    select(Account).where(scope_clause)
                )
            ).scalars().all()
        }
        serialized = [
            _serialize_asset(asset, existing_accounts, connection.id)
            for asset in assets
        ]
        imported_count = sum(1 for item in serialized if item["import_status"] == "this_connection")
        migratable_count = sum(1 for item in serialized if item["can_migrate"])
        return {
            "connection": {
                "id": connection.id,
                "provider_user_name": connection.provider_user_name,
                "status": connection.status,
            },
            "accounts": serialized,
            "count": len(assets),
            "imported_count": imported_count,
            "migratable_count": migratable_count,
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
        connection, ws, member = await _workspace_connection(
            session,
            connection_id,
            user,
            require_write=True,
        )
        caller_role = member.role if member else "buyer"

        if connection.status not in ("active", "expiring"):
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
                            existing.workspace_id is None
                            and not entity_is_owned_by(existing, user)
                        ):
                            raise RuntimeError(
                                "Кабинет принадлежит другому пользователю и не может быть перенесён."
                            )
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
                    was_migrated = False
                    rules_count = 0
                    prev_connection_type = "none"
                    prev_connection_id = None
                    if existing:
                        rules_list = _json_list(existing.active_rules)
                        rules_count = len(rules_list)
                        if existing.meta_connection_id != connection.id:
                            was_migrated = True
                            prev_connection_type = (
                                "system_user" if existing.meta_connection_id is None else "other_oauth"
                            )
                            prev_connection_id = existing.meta_connection_id

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
                    if not existing:
                        account.owner_user_id = user.id
                    account.batch_name = asset.business_name if asset.business_id else ""
                    account.access_token = ""
                    account.access_token_encrypted = ""
                    account.meta_connection_id = connection.id
                    account.timezone_name = timezone_name
                    account.currency = currency
                    account.account_status = status_code
                    account.status_label = status_label
                    account.is_active = True
                    if not existing:
                        session.add(account)

                    if was_migrated:
                        session.add(
                            AuditEvent(
                                workspace_id=ws.id if ws else None,
                                owner_user_id=user.id,
                                actor_type="user",
                                actor_id=str(user.telegram_id or user.id),
                                category="MANUAL_ACTION",
                                event_type="ACCOUNT_MIGRATED_TO_OAUTH",
                                status="SUCCESS",
                                account_id=account_id,
                                account_name=account.name,
                                action="MIGRATE_TO_OAUTH",
                                message=(
                                    f"Кабинет {account_id} успешно переведён на OAuth-подключение "
                                    f"({connection.provider_user_name or connection.provider_user_id}). "
                                    f"Назначенные правила сохранены ({rules_count} шт.)."
                                ),
                                before_state={
                                    "connection_type": prev_connection_type,
                                    "previous_connection_id": prev_connection_id,
                                    "rules_enabled": account.rules_enabled,
                                    "rules_count": rules_count,
                                },
                                after_state={
                                    "connection_type": "facebook_login",
                                    "meta_connection_id": connection.id,
                                    "rules_enabled": account.rules_enabled,
                                    "rules_count": rules_count,
                                },
                                details={
                                    "provider_user_id": connection.provider_user_id,
                                    "provider_user_name": connection.provider_user_name,
                                },
                                correlation_id=secrets.token_hex(16),
                            )
                        )
                    await session.flush()
                added.append(
                    {
                        "account_id": account_id,
                        "name": account.name,
                        "business_name": asset.business_name,
                        "timezone_name": timezone_name,
                        "currency": currency,
                        "migrated": was_migrated,
                        "rules_count": rules_count,
                        "rules_enabled": account.rules_enabled,
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
