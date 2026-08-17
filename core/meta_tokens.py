"""Encryption and resolution helpers for Meta access tokens."""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings


class MetaTokenError(RuntimeError):
    """Safe operational error for a missing or unusable Meta authorization."""


def _fernet() -> MultiFernet:
    raw_key = settings.META_TOKEN_ENCRYPTION_KEY.strip()
    if not raw_key:
        raise MetaTokenError("META_TOKEN_ENCRYPTION_KEY is not configured")
    try:
        keys = [part.strip() for part in raw_key.split(",") if part.strip()]
        if not keys:
            raise ValueError
        # New tokens use the first key; older keys remain decrypt-only. This
        # allows zero-downtime rotation by prepending a new key.
        return MultiFernet([Fernet(key.encode("ascii")) for key in keys])
    except (TypeError, ValueError) as exc:
        raise MetaTokenError("META_TOKEN_ENCRYPTION_KEY is invalid") from exc


def encrypt_meta_token(access_token: str) -> str:
    token = str(access_token or "").strip()
    if not token:
        raise MetaTokenError("Meta access token is empty")
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_meta_token(encrypted_token: str) -> str:
    try:
        return _fernet().decrypt(str(encrypted_token).encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise MetaTokenError("Stored Meta access token cannot be decrypted") from exc


async def resolve_account_access_token(session: AsyncSession, account) -> str:
    """Resolve OAuth tokens by connection while preserving legacy manual imports."""

    if account.meta_connection_id:
        from database.models import MetaConnection

        result = await session.execute(
            select(MetaConnection).where(MetaConnection.id == account.meta_connection_id)
        )
        connection = result.scalar_one_or_none()
        if not connection:
            raise MetaTokenError("Meta connection was not found")
        if connection.status != "active":
            raise MetaTokenError("Meta connection requires reconnection")
        if (
            account.owner_user_id is not None
            and connection.owner_user_id != account.owner_user_id
        ):
            raise MetaTokenError("Meta connection ownership mismatch")
        return decrypt_meta_token(connection.access_token_encrypted)

    legacy_token = str(account.access_token or "").strip()
    if legacy_token:
        return legacy_token
    raise MetaTokenError("Meta authorization is missing")
