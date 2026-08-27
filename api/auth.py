import hmac
import hashlib
import json
import secrets
import urllib.parse
import time
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from fastapi import Header, HTTPException, Query, Request, Response, status
from sqlalchemy import select, update

from core.config import settings
from database.db import async_session_maker
from database.models import User, WebSession

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "buyerly_session"
CSRF_COOKIE_NAME = "buyerly_csrf"
CSRF_HEADER_NAME = "x-csrf-token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _session_max_age(expires_at: datetime) -> int:
    return max(0, int((_as_utc(expires_at) - _utc_now()).total_seconds()))


def set_session_cookies(
    response: Response,
    *,
    token: str,
    csrf_token: Optional[str],
    expires_at: datetime,
) -> None:
    max_age = _session_max_age(expires_at)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max_age,
        expires=expires_at,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    if csrf_token is not None:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            csrf_token,
            max_age=max_age,
            expires=expires_at,
            path="/",
            secure=settings.SESSION_COOKIE_SECURE,
            httponly=False,
            samesite="strict",
        )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=False,
        samesite="strict",
    )


async def create_web_session(
    session,
    *,
    user: User,
    request: Request,
    response: Response,
) -> WebSession:
    now = _utc_now()
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    web_session = WebSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=_secret_hash(token),
        csrf_hash=_secret_hash(csrf_token),
        user_agent=(request.headers.get("user-agent") or "")[:500],
        ip_address=(request.client.host if request.client and request.client.host else "")[:64],
        created_at=now,
        expires_at=now + timedelta(hours=settings.WEB_SESSION_TTL_HOURS),
        last_seen_at=now,
        rotated_at=now,
    )
    session.add(web_session)
    await session.flush()
    set_session_cookies(
        response,
        token=token,
        csrf_token=csrf_token,
        expires_at=web_session.expires_at,
    )
    return web_session

def validate_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: Optional[int] = None,
    max_age_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Validates Telegram WebApp initData using HMAC-SHA256 signature verification.
    Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not bot_token or len(init_data) > 16_384:
        return None

    try:
        clean_init_data = init_data.strip()
        if clean_init_data.startswith("tma "):
            clean_init_data = clean_init_data[4:].strip()

        parsed_data = dict(urllib.parse.parse_qsl(clean_init_data, keep_blank_values=True))
        received_hash = parsed_data.pop("hash", None)
        if not received_hash:
            logger.warning("Telegram initData rejected: signature is missing")
            return None

        # Data check string: alphabetically sorted key=value pairs separated by \n
        data_check_list = [f"{k}={v}" for k, v in sorted(parsed_data.items())]
        data_check_string = "\n".join(data_check_list)

        # Secret key: HMAC-SHA256 of bot_token with key "WebAppData"
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()

        # Calculated hash: HMAC-SHA256 of data_check_string with secret_key
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            logger.warning("Telegram initData rejected: signature mismatch")
            return None

        auth_date = int(parsed_data.get("auth_date", ""))
        current_time = int(time.time()) if now is None else now
        max_age = (
            settings.TELEGRAM_INIT_DATA_MAX_AGE_SECONDS
            if max_age_seconds is None
            else max_age_seconds
        )
        if auth_date > current_time + 60 or current_time - auth_date > max_age:
            logger.warning("Telegram initData rejected: auth_date is outside the allowed window")
            return None

        # Parse user JSON if present
        if "user" in parsed_data:
            if isinstance(parsed_data["user"], str):
                parsed_data["user"] = json.loads(parsed_data["user"])

        return parsed_data
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Telegram initData rejected: malformed payload")
        return None


async def get_current_user(
    request: Request,
    response: Response,
    authorization: Optional[str] = Header(None),
    x_init_data: Optional[str] = Header(None),
    x_auth_token: Optional[str] = Header(None),
    dev_user_id: Optional[str] = Query(None, alias="dev_user_id")
) -> User:
    """
    FastAPI dependency that extracts and validates the authenticated user from:
    1. Secure HttpOnly browser session cookie
    2. Temporary legacy Bearer/X-Auth-Token during migration
    3. Authorization 'tma <initData>' (Telegram Mini App)
    4. Dev fallback if enabled
    """
    bearer_token = ""
    if authorization and authorization.startswith("Bearer "):
        bearer_token = authorization[7:].strip()
    elif x_auth_token:
        bearer_token = x_auth_token.strip()

    raw_init_data = ""
    if authorization and authorization.startswith("tma "):
        raw_init_data = authorization[4:].strip()
    elif authorization and not authorization.startswith("Bearer "):
        raw_init_data = authorization.strip()
    elif x_init_data:
        raw_init_data = x_init_data.strip()

    cookie_token = request.cookies.get(SESSION_COOKIE_NAME, "") if not raw_init_data else ""
    token = bearer_token or cookie_token
    token_source = "bearer" if bearer_token else "cookie"

    async with async_session_maker() as session:
        # Browser sessions store only token hashes. A legacy User.auth_token is
        # converted to a short-lived server session on its first use.
        if token:
            now = _utc_now()
            web_session = (
                await session.execute(
                    select(WebSession).where(WebSession.token_hash == _secret_hash(token))
                )
            ).scalar_one_or_none()

            user = None
            csrf_token_to_set = None
            if web_session is None and bearer_token:
                user = (
                    await session.execute(
                        select(User)
                        .where(User.auth_token == bearer_token)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if user is not None:
                    csrf_token_to_set = secrets.token_urlsafe(32)
                    web_session = WebSession(
                        id=str(uuid.uuid4()),
                        user_id=user.id,
                        token_hash=_secret_hash(bearer_token),
                        csrf_hash=_secret_hash(csrf_token_to_set),
                        user_agent=(request.headers.get("user-agent") or "Legacy browser")[:500],
                        ip_address=(request.client.host if request.client and request.client.host else "")[:64],
                        created_at=now,
                        expires_at=now + timedelta(hours=settings.WEB_SESSION_TTL_HOURS),
                        last_seen_at=now,
                        rotated_at=now,
                    )
                    session.add(web_session)
                    user.auth_token = None
                else:
                    web_session = (
                        await session.execute(
                            select(WebSession).where(
                                WebSession.token_hash == _secret_hash(bearer_token)
                            )
                        )
                    ).scalar_one_or_none()

            if web_session is not None:
                if web_session.revoked_at is not None or _as_utc(web_session.expires_at) <= now:
                    web_session = None
                else:
                    if user is None:
                        user = (
                            await session.execute(select(User).where(User.id == web_session.user_id))
                        ).scalar_one_or_none()

            if web_session is not None and user is not None:
                if not user.is_approved:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Ваш аккаунт ожидает одобрения администратора."
                    )

                if token_source == "cookie" and request.method.upper() not in _SAFE_METHODS:
                    csrf_token = request.headers.get(CSRF_HEADER_NAME, "")
                    if not csrf_token or not hmac.compare_digest(
                        web_session.csrf_hash,
                        _secret_hash(csrf_token),
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="CSRF-проверка не пройдена",
                        )

                rotated_token = None
                if _as_utc(web_session.rotated_at) <= now - timedelta(
                    minutes=settings.WEB_SESSION_ROTATE_MINUTES
                ):
                    candidate_token = secrets.token_urlsafe(32)
                    rotated_id = (
                        await session.execute(
                            update(WebSession)
                            .where(
                                WebSession.id == web_session.id,
                                WebSession.token_hash == _secret_hash(token),
                            )
                            .values(
                                token_hash=_secret_hash(candidate_token),
                                rotated_at=now,
                            )
                            .returning(WebSession.id)
                            .execution_options(synchronize_session=False)
                        )
                    ).scalar_one_or_none()
                    if rotated_id is not None:
                        rotated_token = candidate_token

                if _as_utc(web_session.last_seen_at) <= now - timedelta(minutes=5):
                    web_session.last_seen_at = now

                if token_source == "bearer":
                    csrf_token_to_set = secrets.token_urlsafe(32)
                    web_session.csrf_hash = _secret_hash(csrf_token_to_set)

                await session.commit()
                request.state.web_session_id = web_session.id
                request.state.auth_channel = "browser_session"
                if token_source == "bearer" or rotated_token is not None:
                    set_session_cookies(
                        response,
                        token=rotated_token or token,
                        csrf_token=csrf_token_to_set,
                        expires_at=web_session.expires_at,
                    )
                return user

        # Check Telegram WebApp initData
        tg_user_info = None
        if raw_init_data and settings.BOT_TOKEN:
            validated = validate_telegram_init_data(raw_init_data, settings.BOT_TOKEN)
            if validated and "user" in validated:
                tg_user_info = validated["user"]

        if tg_user_info:
            try:
                tg_id_number = int(tg_user_info.get("id"))
                if tg_id_number <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Некорректные данные пользователя Telegram",
                )
            tg_id = str(tg_id_number)
            username = tg_user_info.get("username", "")
            first_name = tg_user_info.get("first_name", "")
            last_name = tg_user_info.get("last_name", "")
            full_name = f"{first_name} {last_name}".strip()

            is_super_admin = (tg_id == str(settings.ADMIN_CHAT_ID))

            res = await session.execute(select(User).where(User.telegram_id == tg_id))
            user = res.scalar_one_or_none()

            if not user:
                user = User(
                    telegram_id=tg_id,
                    username=username or f"user_{tg_id}",
                    full_name=full_name,
                    role="admin" if is_super_admin else "buyer",
                    is_approved=True if is_super_admin else False
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)

            if not user.is_approved:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ваш аккаунт ожидает одобрения администратора."
                )

            return user

        # Strict Authentication Requirement (Production)
        if not settings.ENABLE_DEV_AUTH:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Требуется авторизация на сайте или через Telegram"
            )

        # Dev / Local preview fallback (ONLY active when ENABLE_DEV_AUTH=True)
        target_tg_id = dev_user_id or str(settings.ADMIN_CHAT_ID)
        if target_tg_id:
            res = await session.execute(select(User).where(User.telegram_id == target_tg_id))
            user = res.scalar_one_or_none()
            if user and user.is_approved:
                return user

        # If no users exist yet in dev mode, check for any approved admin or create default
        res = await session.execute(select(User).where(User.is_approved == True).limit(1))
        any_user = res.scalar_one_or_none()
        if any_user:
            return any_user

        # Create fallback superadmin if DB is empty in dev mode
        fallback_id = str(settings.ADMIN_CHAT_ID) or "123456789"
        fallback_user = User(
            telegram_id=fallback_id,
            username="admin",
            full_name="Administrator",
            role="admin",
            is_approved=True
        )
        session.add(fallback_user)
        await session.commit()
        await session.refresh(fallback_user)
        return fallback_user
