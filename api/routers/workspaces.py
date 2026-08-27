import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select

from api.auth import get_current_user
from api.deps import (
    _active_support_grant,
    get_user_workspaces_list,
    invalidate_summary_cache,
    record_security_event_and_raise,
)
from api.schemas import (
    CreateWorkspaceRequest,
    SwitchWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceItem,
)
from database.db import async_session_maker
from database.models import User, Workspace, WorkspaceMember
from services.image_uploads import (
    delete_workspace_logo_if_unreferenced,
    is_owned_workspace_logo,
)
from services.workspace_slugs import allocate_workspace_slug

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Workspaces"])


@router.get("/workspaces", response_model=List[WorkspaceItem])
async def list_workspaces(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        return await get_user_workspaces_list(session, user)


@router.post("/workspaces", response_model=WorkspaceItem)
async def create_workspace(req: CreateWorkspaceRequest, user: User = Depends(get_current_user)):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название воркспейса обязательно")

    requested_slug = req.slug.strip() if req.slug else name
    badge_color = req.badge_color or "#F5A300"
    badge_text = req.badge_text.strip() if req.badge_text else name[:1].upper()
    logo_url = req.logo_url.strip() if req.logo_url else ""
    if logo_url.startswith("/uploads/workspaces/") and not is_owned_workspace_logo(
        logo_url,
        user.id,
    ):
        raise HTTPException(status_code=400, detail="Логотип не найден или принадлежит другому пользователю")

    async with async_session_maker() as session:
        slug = await allocate_workspace_slug(session, requested_slug)
        ws = Workspace(
            name=name,
            slug=slug,
            badge_text=badge_text,
            badge_color=badge_color,
            logo_url=logo_url,
            owner_user_id=user.id,
        )
        session.add(ws)
        await session.flush()

        member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
        session.add(member)

        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        db_user.active_workspace_id = ws.id
        db_user.onboarding_completed = True
        db_user.onboarding_step = "completed"
        await session.commit()

        return WorkspaceItem(
            id=ws.id,
            name=ws.name,
            slug=ws.slug,
            badge_text=ws.badge_text,
            badge_color=ws.badge_color,
            logo_url=ws.logo_url,
            role="owner",
            is_active=True,
            accounts_count=0,
            members_count=1,
        )


@router.get("/workspaces/current", response_model=WorkspaceItem)
async def get_current_workspace_info(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        workspaces = await get_user_workspaces_list(session, user)
        active_ws = next((w for w in workspaces if w.is_active), workspaces[0] if workspaces else None)
        if not active_ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")
        return active_ws


@router.post("/workspaces/switch")
async def switch_workspace(req: SwitchWorkspaceRequest, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        target_ws = None
        if req.workspace_id:
            target_ws = (await session.execute(select(Workspace).where(Workspace.id == req.workspace_id))).scalar_one_or_none()
        elif req.slug:
            target_ws = (await session.execute(select(Workspace).where(Workspace.slug == req.slug))).scalar_one_or_none()

        if not target_ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == target_ws.id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not member and user.role == "admin":
            grant = await _active_support_grant(session, user.id, target_ws.id)
            if not grant:
                await record_security_event_and_raise(
                    session,
                    status_code=403,
                    detail="Нет доступа к данному воркспейсу",
                    user=user,
                    workspace_id=target_ws.id,
                    action="SWITCH_WORKSPACE",
                    resource_type="workspace",
                    resource_id=str(target_ws.id),
                )
        elif not member:
            await record_security_event_and_raise(
                session,
                status_code=403,
                detail="Нет доступа к данному воркспейсу",
                user=user,
                workspace_id=target_ws.id,
                action="SWITCH_WORKSPACE",
                resource_type="workspace",
                resource_id=str(target_ws.id),
            )

        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        db_user.active_workspace_id = target_ws.id
        await session.commit()

        workspaces = await get_user_workspaces_list(session, user)
        active_ws = next((w for w in workspaces if w.id == target_ws.id), None)
        return {"status": "ok", "active_workspace": active_ws}


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceItem)
async def update_workspace(
    workspace_id: int,
    req: UpdateWorkspaceRequest,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == ws.id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        caller_role = member.role if member else None
        if not caller_role and user.role == "admin":
            grant = await _active_support_grant(session, user.id, ws.id)
            if grant:
                caller_role = grant.role or "admin"

        if not caller_role or caller_role not in ("owner", "admin"):
            await record_security_event_and_raise(
                session,
                status_code=403,
                detail="Недостаточно прав для редактирования воркспейса",
                user=user,
                workspace_id=ws.id,
                action="UPDATE_WORKSPACE",
                resource_type="workspace",
                resource_id=str(ws.id),
            )

        if req.name and req.name.strip():
            ws.name = req.name.strip()
        if req.badge_color and req.badge_color.strip():
            ws.badge_color = req.badge_color.strip()
        if req.badge_text and req.badge_text.strip():
            ws.badge_text = req.badge_text.strip()
        old_logo_url = ws.logo_url
        if req.logo_url is not None:
            new_logo_url = req.logo_url.strip()
            if new_logo_url.startswith(
                "/uploads/workspaces/"
            ) and not is_owned_workspace_logo(new_logo_url, user.id):
                raise HTTPException(
                    status_code=400,
                    detail="Логотип не найден или принадлежит другому пользователю",
                )
            ws.logo_url = new_logo_url

        await session.commit()
        if old_logo_url and old_logo_url != ws.logo_url:
            await delete_workspace_logo_if_unreferenced(session, old_logo_url)
        invalidate_summary_cache(workspace_id=workspace_id)
        workspaces = await get_user_workspaces_list(session, user)
        return next((w for w in workspaces if w.id == ws.id), None)


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: int, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")
        if ws.owner_user_id != user.id:
            await record_security_event_and_raise(
                session,
                status_code=403,
                detail="Только владелец может удалить воркспейс",
                user=user,
                workspace_id=workspace_id,
                action="DELETE_WORKSPACE",
                resource_type="workspace",
                resource_id=str(workspace_id),
            )

        other_member = (
            await session.execute(
                select(WorkspaceMember)
                .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                .where(
                    WorkspaceMember.user_id == user.id,
                    WorkspaceMember.workspace_id != workspace_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if not other_member:
            raise HTTPException(status_code=400, detail="Нельзя удалить единственный воркспейс")

        old_logo_url = ws.logo_url
        await session.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id))
        await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        db_user.active_workspace_id = other_member.workspace_id
        await session.commit()
        if old_logo_url:
            await delete_workspace_logo_if_unreferenced(session, old_logo_url)
        invalidate_summary_cache(workspace_id=workspace_id)
        return {
            "status": "ok",
            "message": "Воркспейс удалён",
            "next_workspace_id": other_member.workspace_id,
        }
