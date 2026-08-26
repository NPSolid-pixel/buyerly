import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, delete, select

from api.auth import get_current_user
from api.deps import _utc_iso, invalidate_summary_cache
from api.schemas import (
    CreateWorkspaceInviteRequest,
    PublicInviteInfoResponse,
    TransferOwnershipRequest,
    UpdateMemberRoleRequest,
    WorkspaceInviteItem,
    WorkspaceMemberItem,
)
from core.config import settings
from core.email import send_workspace_invitation_email
from core.rate_limit import rate_limit_dep
from database.db import async_session_maker
from database.models import AuditEvent, User, Workspace, WorkspaceInvite, WorkspaceMember

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Members & Invites"])


@router.get("/workspaces/{workspace_id}/members", response_model=List[WorkspaceMemberItem])
async def list_workspace_members(
    workspace_id: int,
    user: User = Depends(get_current_user),
):
    """List all members of a workspace with their roles and profile information."""
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        caller_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not caller_member and user.role != "admin":
            raise HTTPException(status_code=403, detail="Нет доступа к данному воркспейсу")

        rows = (
            await session.execute(
                select(WorkspaceMember, User)
                .join(User, User.id == WorkspaceMember.user_id)
                .where(WorkspaceMember.workspace_id == workspace_id)
                .order_by(
                    case(
                        (WorkspaceMember.role == "owner", 1),
                        (WorkspaceMember.role == "admin", 2),
                        (WorkspaceMember.role == "buyer", 3),
                        else_=4,
                    ),
                    WorkspaceMember.joined_at.asc(),
                )
            )
        ).all()

        return [
            WorkspaceMemberItem(
                id=member.id,
                user_id=u.id,
                username=u.username or "",
                full_name=u.full_name or u.username or "",
                first_name=getattr(u, "first_name", "") or "",
                last_name=getattr(u, "last_name", "") or "",
                email=getattr(u, "email", None),
                avatar_url=getattr(u, "avatar_url", "") or "",
                telegram_id=u.telegram_id,
                role=member.role,
                joined_at=_utc_iso(member.joined_at),
                is_current_user=(u.id == user.id),
            )
            for member, u in rows
        ]


@router.patch("/workspaces/{workspace_id}/members/{member_user_id}", response_model=WorkspaceMemberItem)
async def update_workspace_member_role(
    workspace_id: int,
    member_user_id: int,
    req: UpdateMemberRoleRequest,
    user: User = Depends(get_current_user),
):
    """Change the role of an existing workspace member."""
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        caller_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not caller_member and user.role != "admin":
            raise HTTPException(status_code=403, detail="Нет доступа к данному воркспейсу")

        caller_role = caller_member.role if caller_member else "admin"
        if caller_role not in ("owner", "admin") and user.role != "admin":
            raise HTTPException(status_code=403, detail="Недостаточно прав для изменения ролей участников")

        if member_user_id == user.id:
            raise HTTPException(status_code=400, detail="Нельзя изменить собственную роль")

        target_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == member_user_id,
                )
            )
        ).scalar_one_or_none()
        if not target_member:
            raise HTTPException(status_code=404, detail="Участник не найден в воркспейсе")

        if target_member.role == "owner":
            raise HTTPException(status_code=400, detail="Нельзя изменить роль владельца. Используйте передачу владения.")

        if caller_role == "admin" and target_member.role == "admin" and user.role != "admin":
            raise HTTPException(status_code=403, detail="Только владелец может менять роль администратора")

        target_member.role = req.role
        await session.commit()

        target_user = (await session.execute(select(User).where(User.id == member_user_id))).scalar_one()
        return WorkspaceMemberItem(
            id=target_member.id,
            user_id=target_user.id,
            username=target_user.username or "",
            full_name=target_user.full_name or target_user.username or "",
            first_name=getattr(target_user, "first_name", "") or "",
            last_name=getattr(target_user, "last_name", "") or "",
            email=getattr(target_user, "email", None),
            avatar_url=getattr(target_user, "avatar_url", "") or "",
            telegram_id=target_user.telegram_id,
            role=target_member.role,
            joined_at=_utc_iso(target_member.joined_at),
            is_current_user=False,
        )


@router.delete("/workspaces/{workspace_id}/members/{member_user_id}")
async def remove_workspace_member(
    workspace_id: int,
    member_user_id: int,
    user: User = Depends(get_current_user),
):
    """Remove a member from the workspace."""
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        caller_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not caller_member and user.role != "admin":
            raise HTTPException(status_code=403, detail="Нет доступа к данному воркспейсу")

        caller_role = caller_member.role if caller_member else "admin"
        if caller_role not in ("owner", "admin") and user.role != "admin":
            raise HTTPException(status_code=403, detail="Недостаточно прав для исключения участников")

        if member_user_id == user.id:
            raise HTTPException(status_code=400, detail="Для выхода из воркспейса используйте метод leave")

        target_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == member_user_id,
                )
            )
        ).scalar_one_or_none()
        if not target_member:
            raise HTTPException(status_code=404, detail="Участник не найден в воркспейсе")

        if target_member.role == "owner":
            raise HTTPException(status_code=400, detail="Нельзя исключить владельца воркспейса")

        if caller_role == "admin" and target_member.role == "admin" and user.role != "admin":
            raise HTTPException(status_code=403, detail="Только владелец может исключить администратора")

        await session.execute(
            delete(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == member_user_id,
            )
        )

        target_user = (await session.execute(select(User).where(User.id == member_user_id))).scalar_one_or_none()
        if target_user and target_user.active_workspace_id == workspace_id:
            other_m = (
                await session.execute(
                    select(WorkspaceMember).where(WorkspaceMember.user_id == member_user_id).limit(1)
                )
            ).scalar_one_or_none()
            if other_m:
                target_user.active_workspace_id = other_m.workspace_id
            else:
                def_slug = f"buyerly-{target_user.id}"
                new_ws = Workspace(
                    name="Buyerly",
                    slug=def_slug,
                    badge_text="B",
                    badge_color="#F5A300",
                    owner_user_id=target_user.id,
                )
                session.add(new_ws)
                await session.flush()
                session.add(WorkspaceMember(workspace_id=new_ws.id, user_id=target_user.id, role="owner"))
                target_user.active_workspace_id = new_ws.id

        await session.commit()
        invalidate_summary_cache(workspace_id=workspace_id)
        return {"status": "ok", "message": "Участник успешно исключён из воркспейса"}


@router.post("/workspaces/{workspace_id}/leave")
async def leave_workspace(
    workspace_id: int,
    user: User = Depends(get_current_user),
):
    """Leave the workspace voluntarily."""
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        caller_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not caller_member:
            raise HTTPException(status_code=404, detail="Вы не являетесь участником данного воркспейса")

        if caller_member.role == "owner":
            raise HTTPException(
                status_code=400,
                detail="Владелец не может покинуть воркспейс. Передайте права владения или удалите воркспейс.",
            )

        await session.execute(
            delete(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )

        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        next_ws_id = None
        if db_user.active_workspace_id == workspace_id:
            other_m = (
                await session.execute(
                    select(WorkspaceMember).where(WorkspaceMember.user_id == user.id).limit(1)
                )
            ).scalar_one_or_none()
            if other_m:
                db_user.active_workspace_id = other_m.workspace_id
                next_ws_id = other_m.workspace_id
            else:
                def_slug = f"buyerly-{user.id}"
                new_ws = Workspace(
                    name="Buyerly",
                    slug=def_slug,
                    badge_text="B",
                    badge_color="#F5A300",
                    owner_user_id=user.id,
                )
                session.add(new_ws)
                await session.flush()
                session.add(WorkspaceMember(workspace_id=new_ws.id, user_id=user.id, role="owner"))
                db_user.active_workspace_id = new_ws.id
                next_ws_id = new_ws.id

        await session.commit()
        invalidate_summary_cache(workspace_id=workspace_id)
        return {"status": "ok", "message": "Вы вышли из воркспейса", "next_workspace_id": next_ws_id}


@router.post("/workspaces/{workspace_id}/transfer-ownership")
async def transfer_workspace_ownership(
    workspace_id: int,
    req: TransferOwnershipRequest,
    user: User = Depends(get_current_user),
):
    """Transfer workspace ownership to another member."""
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        if ws.owner_user_id != user.id and user.role != "admin":
            raise HTTPException(status_code=403, detail="Только владелец может передать права владения воркспейсом")

        if req.new_owner_user_id == user.id:
            raise HTTPException(status_code=400, detail="Вы уже являетесь владельцем этого воркспейса")

        target_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == req.new_owner_user_id,
                )
            )
        ).scalar_one_or_none()
        if not target_member:
            raise HTTPException(status_code=404, detail="Новый владелец должен состоять в данном воркспейсе")

        caller_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()

        ws.owner_user_id = req.new_owner_user_id
        target_member.role = "owner"
        if caller_member:
            caller_member.role = "admin"

        await session.commit()
        invalidate_summary_cache(workspace_id=workspace_id)
        return {
            "status": "ok",
            "message": "Права владения успешно переданы",
            "new_owner_user_id": req.new_owner_user_id,
        }


@router.post("/workspaces/{workspace_id}/invites", response_model=WorkspaceInviteItem)
async def create_workspace_invite(
    workspace_id: int,
    req: CreateWorkspaceInviteRequest,
    user: User = Depends(get_current_user),
):
    """Create a new workspace invitation (targeted email or public link)."""
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        caller_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not caller_member and user.role != "admin":
            raise HTTPException(status_code=403, detail="Нет доступа к данному воркспейсу")

        caller_role = caller_member.role if caller_member else "admin"
        if caller_role not in ("owner", "admin") and user.role != "admin":
            raise HTTPException(status_code=403, detail="Недостаточно прав для создания приглашений")

        token = f"inv_{secrets.token_urlsafe(24)}"
        now_dt = datetime.now(timezone.utc)
        expires_at = now_dt + timedelta(days=req.expires_in_days) if req.expires_in_days > 0 else None
        target_email = req.email.strip().lower() if req.email and req.email.strip() else None

        invite = WorkspaceInvite(
            workspace_id=workspace_id,
            token=token,
            email=target_email,
            role=req.role,
            inviter_user_id=user.id,
            status="pending",
            max_uses=req.max_uses,
            used_count=0,
            expires_at=expires_at,
        )
        session.add(invite)
        await session.flush()

        session.add(
            AuditEvent(
                workspace_id=workspace_id,
                owner_user_id=user.id,
                actor_type="user",
                actor_id=str(user.id),
                category="WORKSPACE_INVITE",
                event_type="INVITE_CREATE",
                status="SUCCESS",
                message=f"Создано приглашение для {target_email or 'публичной ссылки'}",
                details={
                    "invite_id": invite.id,
                    "email": target_email,
                    "role": invite.role,
                    "max_uses": invite.max_uses,
                },
            )
        )
        await session.commit()
        await session.refresh(invite)

        if target_email:
            send_ok = True
            try:
                inviter_name = user.full_name or user.username or "Коллега"
                await send_workspace_invitation_email(
                    to_email=target_email,
                    workspace_name=ws.name,
                    inviter_name=inviter_name,
                    role=invite.role,
                    invite_token=invite.token,
                )
            except Exception as e:
                send_ok = False
                logger.error("Failed to send invitation email to %s: %s", target_email, e)

            session.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    owner_user_id=user.id,
                    actor_type="user",
                    actor_id=str(user.id),
                    category="WORKSPACE_INVITE",
                    event_type="INVITE_SEND",
                    status="SUCCESS" if send_ok else "FAILED",
                    message=f"Отправка приглашения на {target_email}: {'успешно' if send_ok else 'ошибка'}",
                    details={"invite_id": invite.id, "email": target_email},
                )
            )
            await session.commit()

        base_url = settings.WEBAPP_URL.rstrip("/") if settings.WEBAPP_URL else ""
        invite_url = f"{base_url}/invite/{invite.token}" if base_url else f"/invite/{invite.token}"

        return WorkspaceInviteItem(
            id=invite.id,
            workspace_id=ws.id,
            workspace_name=ws.name,
            token=invite.token,
            invite_url=invite_url,
            email=invite.email,
            role=invite.role,
            status=invite.status,
            max_uses=invite.max_uses,
            used_count=invite.used_count,
            inviter_name=user.full_name or user.username or "",
            expires_at=_utc_iso(invite.expires_at),
            created_at=_utc_iso(invite.created_at),
        )


@router.get("/workspaces/{workspace_id}/invites", response_model=List[WorkspaceInviteItem])
async def list_workspace_invites(
    workspace_id: int,
    user: User = Depends(get_current_user),
):
    """List all invites of a workspace."""
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        caller_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not caller_member and user.role != "admin":
            raise HTTPException(status_code=403, detail="Нет доступа к данному воркспейсу")

        caller_role = caller_member.role if caller_member else "admin"
        if caller_role not in ("owner", "admin") and user.role != "admin":
            raise HTTPException(status_code=403, detail="Недостаточно прав для просмотра приглашений")

        rows = (
            await session.execute(
                select(WorkspaceInvite, User)
                .outerjoin(User, User.id == WorkspaceInvite.inviter_user_id)
                .where(WorkspaceInvite.workspace_id == workspace_id)
                .order_by(WorkspaceInvite.id.desc())
            )
        ).all()

        now_dt = datetime.now(timezone.utc)
        items = []
        base_url = settings.WEBAPP_URL.rstrip("/") if settings.WEBAPP_URL else ""

        for invite, inviter in rows:
            current_status = invite.status
            if current_status == "pending" and invite.expires_at and now_dt > invite.expires_at:
                current_status = "expired"

            invite_url = f"{base_url}/invite/{invite.token}" if base_url else f"/invite/{invite.token}"
            inviter_name = (inviter.full_name or inviter.username) if inviter else ""

            items.append(
                WorkspaceInviteItem(
                    id=invite.id,
                    workspace_id=ws.id,
                    workspace_name=ws.name,
                    token=invite.token,
                    invite_url=invite_url,
                    email=invite.email,
                    role=invite.role,
                    status=current_status,
                    max_uses=invite.max_uses,
                    used_count=invite.used_count,
                    inviter_name=inviter_name,
                    expires_at=_utc_iso(invite.expires_at),
                    created_at=_utc_iso(invite.created_at),
                )
            )
        return items


@router.delete("/workspaces/{workspace_id}/invites/{invite_id}")
async def revoke_workspace_invite(
    workspace_id: int,
    invite_id: int,
    user: User = Depends(get_current_user),
):
    """Revoke an active invitation."""
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        caller_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not caller_member and user.role != "admin":
            raise HTTPException(status_code=403, detail="Нет доступа к данному воркспейсу")

        caller_role = caller_member.role if caller_member else "admin"
        if caller_role not in ("owner", "admin") and user.role != "admin":
            raise HTTPException(status_code=403, detail="Недостаточно прав для отзыва приглашений")

        invite = (
            await session.execute(
                select(WorkspaceInvite).where(
                    WorkspaceInvite.id == invite_id,
                    WorkspaceInvite.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if not invite:
            raise HTTPException(status_code=404, detail="Приглашение не найдено")

        invite.status = "revoked"
        session.add(
            AuditEvent(
                workspace_id=workspace_id,
                owner_user_id=user.id,
                actor_type="user",
                actor_id=str(user.id),
                category="WORKSPACE_INVITE",
                event_type="INVITE_REVOKE",
                status="SUCCESS",
                message="Приглашение отозвано",
                details={"invite_id": invite.id, "email": invite.email},
            )
        )
        await session.commit()
        return {"status": "ok", "message": "Приглашение успешно отозвано"}


@router.get(
    "/invites/{token}",
    response_model=PublicInviteInfoResponse,
    dependencies=[Depends(rate_limit_dep(limit=30, window_seconds=60, scope="invite_info"))],
)
async def get_public_invite_info(token: str):
    """Public endpoint to inspect an invite before joining."""
    async with async_session_maker() as session:
        invite = (
            await session.execute(
                select(WorkspaceInvite).where(WorkspaceInvite.token == token)
            )
        ).scalar_one_or_none()
        if not invite:
            return PublicInviteInfoResponse(
                valid=False,
                status="not_found",
                message="Приглашение не найдено или ссылка недействительна",
            )

        now_dt = datetime.now(timezone.utc)
        if invite.status == "revoked":
            return PublicInviteInfoResponse(
                valid=False,
                status="revoked",
                message="Это приглашение было отозвано администратором",
            )

        if invite.expires_at and now_dt > invite.expires_at:
            return PublicInviteInfoResponse(
                valid=False,
                status="expired",
                message="Срок действия приглашения истёк",
            )

        if invite.max_uses > 0 and invite.used_count >= invite.max_uses:
            return PublicInviteInfoResponse(
                valid=False,
                status="accepted",
                message="Лимит использований данного приглашения исчерпан",
            )

        ws = (await session.execute(select(Workspace).where(Workspace.id == invite.workspace_id))).scalar_one_or_none()
        if not ws:
            return PublicInviteInfoResponse(
                valid=False,
                status="not_found",
                message="Воркспейс больше не существует",
            )

        inviter = None
        if invite.inviter_user_id:
            inviter = (
                await session.execute(
                    select(User).where(User.id == invite.inviter_user_id)
                )
            ).scalar_one_or_none()

        inviter_name = (inviter.full_name or inviter.username) if inviter else "Команда"

        return PublicInviteInfoResponse(
            valid=True,
            status="pending",
            workspace_name=ws.name,
            workspace_slug=ws.slug,
            workspace_badge_text=ws.badge_text or ws.name[:1].upper(),
            workspace_badge_color=ws.badge_color or "#F5A300",
            inviter_name=inviter_name,
            role=invite.role,
            target_email=invite.email,
            expires_at=_utc_iso(invite.expires_at),
            message="Приглашение действительно",
        )


@router.post(
    "/invites/{token}/accept",
    dependencies=[Depends(rate_limit_dep(limit=10, window_seconds=60, scope="invite_accept"))],
)
async def accept_workspace_invite(
    token: str,
    user: User = Depends(get_current_user),
):
    """Accept a workspace invite and join the workspace."""
    async with async_session_maker() as session:
        invite = (
            await session.execute(
                select(WorkspaceInvite)
                .where(WorkspaceInvite.token == token)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not invite:
            raise HTTPException(status_code=404, detail="Приглашение не найдено")

        now_dt = datetime.now(timezone.utc)
        if invite.status == "revoked":
            session.add(
                AuditEvent(
                    workspace_id=invite.workspace_id,
                    owner_user_id=user.id,
                    actor_type="user",
                    actor_id=str(user.id),
                    category="WORKSPACE_INVITE",
                    event_type="INVITE_REJECT",
                    status="FAILED",
                    message="Попытка принятия отозванного приглашения",
                    details={"invite_id": invite.id, "reason": "revoked"},
                )
            )
            await session.commit()
            raise HTTPException(status_code=400, detail="Это приглашение было отозвано")

        if invite.expires_at and now_dt > invite.expires_at:
            invite.status = "expired"
            session.add(
                AuditEvent(
                    workspace_id=invite.workspace_id,
                    owner_user_id=user.id,
                    actor_type="user",
                    actor_id=str(user.id),
                    category="WORKSPACE_INVITE",
                    event_type="INVITE_REJECT",
                    status="FAILED",
                    message="Попытка принятия просроченного приглашения",
                    details={"invite_id": invite.id, "reason": "expired"},
                )
            )
            await session.commit()
            raise HTTPException(status_code=400, detail="Срок действия приглашения истёк")

        if invite.max_uses > 0 and invite.used_count >= invite.max_uses:
            session.add(
                AuditEvent(
                    workspace_id=invite.workspace_id,
                    owner_user_id=user.id,
                    actor_type="user",
                    actor_id=str(user.id),
                    category="WORKSPACE_INVITE",
                    event_type="INVITE_REJECT",
                    status="FAILED",
                    message="Лимит использований приглашения исчерпан",
                    details={"invite_id": invite.id, "reason": "max_uses_reached"},
                )
            )
            await session.commit()
            raise HTTPException(status_code=400, detail="Лимит использований приглашения исчерпан")

        ws = (await session.execute(select(Workspace).where(Workspace.id == invite.workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        # Targeted invite protection: verify user has verified email matching invite.email
        if invite.email:
            target_email = invite.email.strip().lower()
            user_email = (user.email or "").strip().lower()

            if not user_email or not getattr(user, "email_verified_at", None):
                session.add(
                    AuditEvent(
                        workspace_id=invite.workspace_id,
                        owner_user_id=user.id,
                        actor_type="user",
                        actor_id=str(user.id),
                        category="WORKSPACE_INVITE",
                        event_type="INVITE_REJECT",
                        status="FAILED",
                        message="Попытка принятия targeted invite без подтверждённого email",
                        details={"invite_id": invite.id, "reason": "unverified_email"},
                    )
                )
                await session.commit()
                raise HTTPException(
                    status_code=403,
                    detail="Для принятия персонального приглашения требуется подтверждённый адрес электронной почты.",
                )

            if user_email != target_email:
                session.add(
                    AuditEvent(
                        workspace_id=invite.workspace_id,
                        owner_user_id=user.id,
                        actor_type="user",
                        actor_id=str(user.id),
                        category="WORKSPACE_INVITE",
                        event_type="INVITE_REJECT",
                        status="FAILED",
                        message="Попытка принятия targeted invite с несовпадающим email",
                        details={"invite_id": invite.id, "reason": "email_mismatch"},
                    )
                )
                await session.commit()
                raise HTTPException(
                    status_code=403,
                    detail="Это приглашение предназначено для другого email-адреса.",
                )

        existing_m = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == invite.workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()

        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        db_user.active_workspace_id = ws.id

        if not existing_m:
            member = WorkspaceMember(
                workspace_id=invite.workspace_id,
                user_id=user.id,
                role=invite.role,
            )
            session.add(member)
            invite.used_count += 1
            if invite.max_uses > 0 and invite.used_count >= invite.max_uses:
                invite.status = "accepted"

        session.add(
            AuditEvent(
                workspace_id=invite.workspace_id,
                owner_user_id=user.id,
                actor_type="user",
                actor_id=str(user.id),
                category="WORKSPACE_INVITE",
                event_type="INVITE_ACCEPT",
                status="SUCCESS",
                message=f"Приглашение принято пользователем {user.username}",
                details={
                    "invite_id": invite.id,
                    "workspace_id": ws.id,
                    "user_id": user.id,
                    "role": existing_m.role if existing_m else invite.role,
                },
            )
        )

        await session.commit()
        invalidate_summary_cache(workspace_id=ws.id)
        return {
            "status": "ok",
            "message": f"Вы успешно присоединились к воркспейсу {ws.name}",
            "workspace_id": ws.id,
            "workspace_slug": ws.slug,
            "role": existing_m.role if existing_m else invite.role,
        }
