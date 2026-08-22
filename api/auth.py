import hmac
import hashlib
import json
import urllib.parse
import time
import logging
from typing import Optional, Dict, Any
from fastapi import Header, HTTPException, Depends, Query, status
from sqlalchemy import select

from core.config import settings
from database.db import async_session_maker
from database.models import User

logger = logging.getLogger(__name__)

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
    authorization: Optional[str] = Header(None),
    x_init_data: Optional[str] = Header(None),
    x_auth_token: Optional[str] = Header(None),
    dev_user_id: Optional[str] = Query(None, alias="dev_user_id")
) -> User:
    """
    FastAPI dependency that extracts and validates the authenticated user from:
    1. Bearer <auth_token> (Web site direct login)
    2. Header X-Auth-Token (Web site direct login)
    3. Authorization 'tma <initData>' (Telegram Mini App)
    4. Dev fallback if enabled
    """
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif x_auth_token:
        token = x_auth_token.strip()

    raw_init_data = ""
    if authorization and authorization.startswith("tma "):
        raw_init_data = authorization[4:].strip()
    elif authorization and not authorization.startswith("Bearer "):
        raw_init_data = authorization.strip()
    elif x_init_data:
        raw_init_data = x_init_data.strip()

    async with async_session_maker() as session:
        # Check Bearer Token (Direct Web Login)
        if token:
            res = await session.execute(select(User).where(User.auth_token == token))
            user = res.scalar_one_or_none()
            if user:
                if not user.is_approved:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Ваш аккаунт ожидает одобрения администратора."
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
