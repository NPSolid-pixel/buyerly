import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.auth import get_current_user
from api.deps import _utc_iso
from database.db import async_session_maker
from database.models import AuditEvent, User, Workspace, WorkspaceSupportGrant

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin Support Sessions"])


class CreateSupportSessionRequest(BaseModel):
    workspace_id: int
    reason: str = Field(..., min_length=10, max_length=500, description="Обоснование для сессии техподдержки")
    duration_minutes: int = Field(default=30, ge=5, le=240, description="Длительность сессии от 5 до 240 минут")


class SupportSessionItem(BaseModel):
    id: int
    workspace_id: int
    workspace_name: str
    user_id: int
    role: str
    reason: str
    expires_at: str
    created_at: str
    revoked_at: Optional[str] = None
    is_active: bool


def _require_platform_admin(user: User):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Требуются права глобального администратора платформы")


@router.post("/support-sessions", response_model=SupportSessionItem)
async def create_support_session(
    payload: CreateSupportSessionRequest,
    user: User = Depends(get_current_user),
):
    _require_platform_admin(user)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=payload.duration_minutes)

    async with async_session_maker() as session:
        ws = (
            await session.execute(
                select(Workspace).where(Workspace.id == payload.workspace_id)
            )
        ).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Целевой воркспейс не найден")

        # Revoke any prior active grants for this admin in this workspace
        existing_grants = (
            await session.execute(
                select(WorkspaceSupportGrant).where(
                    WorkspaceSupportGrant.workspace_id == ws.id,
                    WorkspaceSupportGrant.user_id == user.id,
                    WorkspaceSupportGrant.expires_at > now,
                    WorkspaceSupportGrant.revoked_at.is_(None),
                )
            )
        ).scalars().all()
        for eg in existing_grants:
            eg.revoked_at = now

        grant = WorkspaceSupportGrant(
            workspace_id=ws.id,
            user_id=user.id,
            role="admin",
            reason=payload.reason.strip(),
            expires_at=expires_at,
            created_at=now,
        )
        session.add(grant)
        await session.flush()

        audit_event = AuditEvent(
            workspace_id=ws.id,
            owner_user_id=user.id,
            actor_type="user",
            actor_id=str(user.telegram_id or user.id),
            category="SECURITY",
            event_type="SUPPORT_SESSION_GRANTED",
            status="SUCCESS",
            action="CREATE_SUPPORT_SESSION",
            message=f"Создана сессия техподдержки для воркспейса '{ws.name}' на {payload.duration_minutes} мин. Причина: {payload.reason.strip()}",
            details={
                "grant_id": grant.id,
                "workspace_id": ws.id,
                "workspace_name": ws.name,
                "duration_minutes": payload.duration_minutes,
                "expires_at": _utc_iso(expires_at),
                "reason": payload.reason.strip(),
            },
        )
        session.add(audit_event)
        await session.commit()
        await session.refresh(grant)

        return SupportSessionItem(
            id=grant.id,
            workspace_id=ws.id,
            workspace_name=ws.name,
            user_id=grant.user_id,
            role=grant.role,
            reason=grant.reason,
            expires_at=_utc_iso(grant.expires_at),
            created_at=_utc_iso(grant.created_at),
            revoked_at=_utc_iso(grant.revoked_at) if grant.revoked_at else None,
            is_active=True,
        )


@router.get("/support-sessions", response_model=List[SupportSessionItem])
async def list_support_sessions(
    active_only: bool = Query(True),
    user: User = Depends(get_current_user),
):
    _require_platform_admin(user)

    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        stmt = (
            select(WorkspaceSupportGrant, Workspace.name)
            .join(Workspace, Workspace.id == WorkspaceSupportGrant.workspace_id)
            .where(WorkspaceSupportGrant.user_id == user.id)
            .order_by(WorkspaceSupportGrant.created_at.desc())
        )
        if active_only:
            stmt = stmt.where(
                WorkspaceSupportGrant.expires_at > now,
                WorkspaceSupportGrant.revoked_at.is_(None),
            )

        rows = (await session.execute(stmt)).all()
        return [
            SupportSessionItem(
                id=grant.id,
                workspace_id=grant.workspace_id,
                workspace_name=ws_name,
                user_id=grant.user_id,
                role=grant.role,
                reason=grant.reason,
                expires_at=_utc_iso(grant.expires_at),
                created_at=_utc_iso(grant.created_at),
                revoked_at=_utc_iso(grant.revoked_at) if grant.revoked_at else None,
                is_active=(grant.revoked_at is None and grant.expires_at > now),
            )
            for grant, ws_name in rows
        ]


@router.post("/support-sessions/{grant_id}/revoke")
async def revoke_support_session(
    grant_id: int,
    user: User = Depends(get_current_user),
):
    _require_platform_admin(user)

    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        grant = (
            await session.execute(
                select(WorkspaceSupportGrant).where(
                    WorkspaceSupportGrant.id == grant_id,
                    WorkspaceSupportGrant.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not grant:
            raise HTTPException(status_code=404, detail="Сессия техподдержки не найдена")

        if grant.revoked_at is None:
            grant.revoked_at = now
            audit_event = AuditEvent(
                workspace_id=grant.workspace_id,
                owner_user_id=user.id,
                actor_type="user",
                actor_id=str(user.telegram_id or user.id),
                category="SECURITY",
                event_type="SUPPORT_SESSION_REVOKED",
                status="SUCCESS",
                action="REVOKE_SUPPORT_SESSION",
                message=f"Сессия техподдержки #{grant.id} отозвана администратором.",
                details={"grant_id": grant.id, "workspace_id": grant.workspace_id},
            )
            session.add(audit_event)
            await session.commit()

        return {"status": "ok", "message": "Сессия техподдержки отозвана", "grant_id": grant_id}
