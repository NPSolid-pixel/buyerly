"""Workspace-scoped SLI overview and per-account health endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from api.auth import get_current_user
from api.deps import get_user_workspace
from database.db import async_session_maker
from database.models import Account, AccountHealth, AppSettings, AuditEvent, AutomationRuntimeState, MetaConnection, User
from services.account_health import health_payload


router = APIRouter(tags=["Reliability"])


def _iso(value):
    return value.isoformat() if value else None


def _runtime_payload(row):
    return dict(row.payload or {}) if row else {}


def _age_hours(value):
    if not value:
        return None
    try:
        measured_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return round(max(0, (datetime.now(timezone.utc) - measured_at).total_seconds()) / 3600, 2)
    except (TypeError, ValueError):
        return None


@router.get("/health/overview")
async def health_overview(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        workspace = await get_user_workspace(session, user)
        if workspace is None:
            return {"overall_status": "unknown", "counts": {}, "accounts": [], "signals": {}}

        accounts = (
            await session.execute(
                select(Account).where(Account.workspace_id == workspace.id).order_by(Account.name, Account.id)
            )
        ).scalars().all()
        account_pks = [account.id for account in accounts]
        health_rows = []
        if account_pks:
            health_rows = (
                await session.execute(
                    select(AccountHealth).where(
                        AccountHealth.account_pk.in_(account_pks),
                        AccountHealth.workspace_id == workspace.id,
                    )
                )
            ).scalars().all()
        health_by_account = {row.account_pk: row for row in health_rows}

        runtime_row = await session.get(AutomationRuntimeState, "monitoring")
        runtime = _runtime_payload(runtime_row)
        finished_at = runtime.get("finished_at")
        worker_lag_seconds = None
        if finished_at:
            try:
                finished = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
                worker_lag_seconds = max(0, int((datetime.now(timezone.utc) - finished).total_seconds()))
            except (TypeError, ValueError):
                pass

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        action_total = (
            await session.execute(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.category == "RULE_ACTION",
                    AuditEvent.created_at >= since,
                )
            )
        ).scalar_one()
        action_errors = (
            await session.execute(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.category == "RULE_ACTION",
                    AuditEvent.status == "ERROR",
                    AuditEvent.created_at >= since,
                )
            )
        ).scalar_one()
        action_error_rate = round((action_errors / action_total * 100), 2) if action_total else 0.0

        connection_ids = {account.meta_connection_id for account in accounts if account.meta_connection_id}
        connection_rows = []
        if connection_ids:
            connection_rows = (
                await session.execute(
                    select(MetaConnection).where(
                        MetaConnection.workspace_id == workspace.id,
                        MetaConnection.id.in_(connection_ids),
                    )
                )
            ).scalars().all()
        token_problem_count = sum(
            1 for row in connection_rows if row.status in {"expired", "needs_reconnect", "missing_scopes", "error"}
        )

        settings = (await session.execute(select(AppSettings).limit(1))).scalar_one_or_none()
        counts = {"healthy": 0, "degraded": 0, "critical": 0, "unknown": 0}
        account_items = []
        for account in accounts:
            payload = health_payload(health_by_account.get(account.id))
            counts[payload["status"]] += 1
            account_items.append({
                "id": account.id,
                "account_id": account.account_id,
                "name": account.custom_name or account.name,
                **payload,
            })
        overall = "unknown" if not accounts else "critical" if counts["critical"] else "degraded" if counts["degraded"] else "unknown" if counts["unknown"] else "healthy"
        usage_percent = int(((runtime.get("usage") or {}).get("max_percent") or 0))
        return {
            "overall_status": overall,
            "counts": counts,
            "accounts": account_items,
            "signals": {
                "api_availability_target_percent": 99.9,
                "api_latency_p95_target_ms": 500,
                "worker_cycle_lag_seconds": worker_lag_seconds,
                "worker_cycle_lag_warning_seconds": 180,
                "worker_cycle_lag_critical_seconds": 360,
                "action_error_rate_24h_percent": action_error_rate,
                "action_error_rate_warning_percent": 2,
                "action_error_rate_critical_percent": 5,
                "meta_quota_percent": usage_percent,
                "meta_quota_warning_percent": settings.usage_soft_limit_percent if settings else 60,
                "meta_quota_critical_percent": settings.usage_hard_limit_percent if settings else 80,
                "token_problem_count": token_problem_count,
                "data_freshness_warning_minutes": 20,
                "data_freshness_critical_minutes": 45,
                "backup_age_hours": _age_hours(runtime.get("last_backup_at")),
                "backup_age_warning_hours": 26,
                "backup_age_critical_hours": 48,
            },
            "alert_routes": {
                "account_transition": "Telegram owner/admin + Audit Log",
                "release_or_platform": "GitHub Actions + production runbook",
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/accounts/{account_id}/health")
async def account_health(account_id: str, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        workspace = await get_user_workspace(session, user)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Кабинет не найден")
        account = (
            await session.execute(
                select(Account).where(Account.workspace_id == workspace.id, Account.account_id == account_id)
            )
        ).scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=404, detail="Кабинет не найден")
        row = (
            await session.execute(
                select(AccountHealth).where(
                    AccountHealth.account_pk == account.id,
                    AccountHealth.workspace_id == workspace.id,
                )
            )
        ).scalar_one_or_none()
        return {"account_id": account.account_id, "name": account.custom_name or account.name, **health_payload(row)}
