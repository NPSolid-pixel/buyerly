"""Durable, secret-safe account health state and failure classification."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from core.logging_config import redact_secrets
from database.models import Account, AccountHealth


USER_MARKERS = (
    "token", "permission", "scope", "reconnect", "oauth", "disabled",
    "unsettled", "payment", "account status", "access denied",
)
META_MARKERS = (
    "meta", "facebook", "graph", "rate limit", "quota", "throttl",
    "temporarily unavailable", "service unavailable",
)


def classify_health_error(error: Any) -> tuple[str, str]:
    """Return a stable error code and responsible domain."""

    text = str(error or "unknown error")
    lowered = text.lower()
    if any(marker in lowered for marker in USER_MARKERS):
        cause = "user"
    elif any(marker in lowered for marker in META_MARKERS):
        cause = "meta"
    else:
        cause = "system"
    code = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")[:64] or "unknown_error"
    return code, cause


def safe_health_message(error: Any) -> str:
    return redact_secrets(str(error or "unknown error"))[:240]


async def record_account_health(
    session,
    account: Account,
    *,
    success: bool,
    error: Any = None,
    cause: str | None = None,
    signals: dict[str, Any] | None = None,
) -> tuple[AccountHealth | None, bool]:
    """Upsert one health row and report whether its status/cause changed."""

    if account.workspace_id is None:
        return None, False
    now = datetime.now(timezone.utc)
    row = (
        await session.execute(
            select(AccountHealth).where(AccountHealth.account_pk == account.id)
        )
    ).scalar_one_or_none()
    created = row is None
    if row is None:
        row = AccountHealth(
            workspace_id=account.workspace_id,
            account_pk=account.id,
            status="unknown",
            cause="none",
            last_checked_at=now,
        )
        session.add(row)
    previous = (row.status, row.cause)
    merged_signals = dict(row.signals or {})
    merged_signals.update(signals or {})
    merged_signals["data_fresh_at"] = now.isoformat() if success else merged_signals.get("data_fresh_at")
    row.workspace_id = account.workspace_id
    row.last_checked_at = now
    row.signals = merged_signals
    if success:
        row.status = "healthy"
        row.cause = "none"
        row.consecutive_failures = 0
        row.last_success_at = now
        row.last_error_code = ""
        row.last_error_message = ""
    else:
        code, classified_cause = classify_health_error(error)
        row.consecutive_failures = int(row.consecutive_failures or 0) + 1
        row.status = "critical" if row.consecutive_failures >= 3 or (cause or classified_cause) == "user" else "degraded"
        row.cause = cause or classified_cause
        row.last_error_at = now
        row.last_error_code = code
        row.last_error_message = safe_health_message(error)
    transitioned = previous != (row.status, row.cause) and not (created and success)
    if transitioned:
        row.last_transition_at = now
    await session.flush()
    return row, transitioned


def health_payload(row: AccountHealth | None) -> dict[str, Any]:
    if row is None:
        return {
            "status": "unknown", "cause": "none", "signals": {},
            "consecutive_failures": 0, "last_success_at": None,
            "last_error_at": None, "last_error_code": "", "last_error_message": "",
            "last_checked_at": None,
        }

    def iso(value):
        return value.isoformat() if value else None

    return {
        "status": row.status,
        "cause": row.cause,
        "signals": dict(row.signals or {}),
        "consecutive_failures": row.consecutive_failures,
        "last_success_at": iso(row.last_success_at),
        "last_error_at": iso(row.last_error_at),
        "last_error_code": row.last_error_code,
        "last_error_message": row.last_error_message,
        "last_checked_at": iso(row.last_checked_at),
    }
