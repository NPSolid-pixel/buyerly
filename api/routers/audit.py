import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false, func, or_, select

from api.auth import get_current_user
from api.deps import (
    _load_json_object,
    _utc_iso,
    ensure_workspace_write_access,
    get_user_workspace_member,
)
from core.action_undo import (
    MUTATING_EVENT_TYPES,
    REVERSIBLE_EVENT_TYPES,
    UndoError,
    event_is_within_undo_window,
    undo_audit_action,
)
from database.db import async_session_maker
from database.models import AuditEvent, User
from meta_api.client import MetaClient
from services.inventory_cache import PostgreSQLInventoryCache

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Audit & Undo"])
meta_client = MetaClient(cache_provider=PostgreSQLInventoryCache())


@router.get("/audit-events")
async def list_audit_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    category: Optional[str] = Query(None, max_length=40),
    event_status: Optional[str] = Query(None, alias="status", max_length=20),
    account_id: Optional[str] = Query(None, max_length=80),
    rule_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    user: User = Depends(get_current_user),
):
    """Return an owner-isolated, filterable audit history for the web UI."""
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        workspace_id = ws.id if ws else None
        can_write_workspace = bool(member and member.role != "viewer")
        # Legacy NULL rows are intentionally quarantined from the tenant UI.
        # owner_user_id cannot identify one active workspace when the same user
        # belongs to multiple workspaces.
        filters = [
            AuditEvent.workspace_id == workspace_id
            if workspace_id is not None
            else false()
        ]
        if category:
            filters.append(AuditEvent.category == category.upper())
        if account_id:
            filters.append(AuditEvent.account_id == account_id)
        if rule_id is not None:
            filters.append(AuditEvent.rule_id == rule_id)
        if date_from:
            filters.append(AuditEvent.created_at >= date_from)
        if date_to:
            filters.append(AuditEvent.created_at <= date_to)
        if search and search.strip():
            search_pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    AuditEvent.account_name.ilike(search_pattern),
                    AuditEvent.account_id.ilike(search_pattern),
                    AuditEvent.adset_name.ilike(search_pattern),
                    AuditEvent.adset_id.ilike(search_pattern),
                    AuditEvent.rule_name.ilike(search_pattern),
                    AuditEvent.message.ilike(search_pattern),
                )
            )

        status_filters = list(filters)
        if event_status:
            normalized_status = event_status.upper()
            if normalized_status == "REVERTED":
                filters.append(
                    AuditEvent.id.in_(
                        select(AuditEvent.reverts_event_id).where(
                            AuditEvent.reverts_event_id.is_not(None),
                            AuditEvent.workspace_id == workspace_id,
                        )
                    )
                )
            else:
                filters.append(AuditEvent.status == normalized_status)

        total = (
            await session.execute(
                select(func.count()).select_from(AuditEvent).where(*filters)
            )
        ).scalar_one()

        status_rows = (
            await session.execute(
                select(AuditEvent.status, func.count(AuditEvent.id))
                .where(*status_filters)
                .group_by(AuditEvent.status)
            )
        ).all()
        reverted_count = (
            await session.execute(
                select(func.count(AuditEvent.reverts_event_id)).where(
                    AuditEvent.reverts_event_id.is_not(None),
                    *status_filters,
                )
            )
        ).scalar_one()

        rows = (
            await session.execute(
                select(AuditEvent)
                .where(*filters)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()

        row_ids = [row.id for row in rows]
        reversal_rows = []
        if row_ids:
            reversal_rows = (
                await session.execute(
                    select(AuditEvent.reverts_event_id, AuditEvent.id).where(
                        AuditEvent.reverts_event_id.in_(row_ids),
                        AuditEvent.workspace_id == workspace_id,
                    )
                )
            ).all()
        reversed_by = {
            original_id: reversal_id
            for original_id, reversal_id in reversal_rows
            if original_id is not None
        }

        target_keys = {
            (row.account_id, row.adset_id)
            for row in rows
            if row.account_id and row.adset_id
        }
        latest_mutating_by_target = {}
        if target_keys:
            account_ids = {account_key for account_key, _ in target_keys}
            adset_ids = {adset_key for _, adset_key in target_keys}
            latest_rows = (
                await session.execute(
                    select(
                        AuditEvent.account_id,
                        AuditEvent.adset_id,
                        func.max(AuditEvent.id),
                    )
                    .where(
                        AuditEvent.account_id.in_(account_ids),
                        AuditEvent.adset_id.in_(adset_ids),
                        AuditEvent.status == "SUCCESS",
                        AuditEvent.event_type.in_(MUTATING_EVENT_TYPES),
                        AuditEvent.workspace_id == workspace_id,
                    )
                    .group_by(AuditEvent.account_id, AuditEvent.adset_id)
                )
            ).all()
            latest_mutating_by_target = {
                (account_key, adset_key): latest_id
                for account_key, adset_key, latest_id in latest_rows
            }

    items = []
    for row in rows:
        reversal_id = reversed_by.get(row.id)
        is_reversible = (
            row.status == "SUCCESS"
            and row.event_type in REVERSIBLE_EVENT_TYPES
            and bool(row.account_id and row.adset_id)
        )
        latest_id = latest_mutating_by_target.get((row.account_id, row.adset_id))
        can_undo = bool(
            can_write_workspace
            and is_reversible
            and reversal_id is None
            and latest_id == row.id
            and event_is_within_undo_window(row)
        )
        if not can_write_workspace:
            undo_reason = "Роль Наблюдатель (Viewer) не может отменять действия."
        elif reversal_id is not None:
            undo_reason = "Действие уже отменено."
        elif not is_reversible:
            undo_reason = "Это событие не меняется обратной командой."
        elif latest_id != row.id:
            undo_reason = "После этого события ad set уже изменялся."
        elif not event_is_within_undo_window(row):
            undo_reason = "Окно безопасной отмены 24 часа закрыто."
        else:
            undo_reason = ""
        items.append({
            "id": row.id,
            "owner_user_id": row.owner_user_id if user.role == "admin" else None,
            "workspace_id": row.workspace_id,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "category": row.category,
            "event_type": row.event_type,
            "status": row.status,
            "account_id": row.account_id,
            "account_name": row.account_name,
            "adset_id": row.adset_id,
            "adset_name": row.adset_name,
            "rule_id": row.rule_id,
            "rule_name": row.rule_name,
            "action": row.action,
            "message": row.message,
            "before_state": _load_json_object(row.before_state),
            "after_state": _load_json_object(row.after_state),
            "details": _load_json_object(row.details),
            "correlation_id": row.correlation_id,
            "reverts_event_id": row.reverts_event_id,
            "reverted_by_event_id": reversal_id,
            "is_reverted": reversal_id is not None,
            "display_status": "REVERTED" if reversal_id is not None else row.status,
            "can_undo": can_undo,
            "undo_reason": undo_reason,
            "duration_ms": row.duration_ms,
            "created_at": _utc_iso(row.created_at),
        })

    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "status_counts": {
            **{status_name: count for status_name, count in status_rows},
            "REVERTED": reverted_count,
        },
    }


@router.post("/audit-events/{event_id}/undo")
async def undo_audit_event(
    event_id: int,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "отмены действия")
        try:
            return await undo_audit_action(
                session,
                meta_client=meta_client,
                event_id=event_id,
                actor_type="user",
                actor_id=str(user.telegram_id or user.id),
                owner_user_id=user.id,
                workspace_id=ws.id if ws else None,
                is_admin=user.role == "admin",
            )
        except UndoError as error:
            raise HTTPException(status_code=error.status_code, detail=error.message) from error
