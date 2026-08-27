import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select

from api.auth import get_current_user
from api.deps import (
    RESERVED_WORKSPACE_SLUGS,
    _utc_iso,
    get_user_workspace,
    get_user_workspaces_list,
)
from api.schemas import (
    CheckSlugResponse,
    OnboardingBulkInvitesRequest,
    OnboardingBulkInvitesResponse,
    OnboardingStatusResponse,
    OnboardingWorkspaceRequest,
    PersonalDetailsRequest,
    UserProfileResponse,
    WorkspaceInviteItem,
    WorkspaceItem,
)
from core.email import send_workspace_invitation_email
from core.rate_limit import rate_limit_dep
from core.workspace_slugs import normalize_workspace_slug
from database.db import async_session_maker
from database.models import AuditEvent, User, Workspace, WorkspaceInvite, WorkspaceMember
from services.image_uploads import (
    InvalidImageUpload,
    MAX_UPLOAD_BYTES,
    cleanup_stale_workspace_logos,
    delete_local_upload,
    is_owned_workspace_logo,
    save_image_upload,
)
from services.workspace_slugs import allocate_workspace_slug

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Onboarding"])


@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(user: User = Depends(get_current_user)):
    """Return the current onboarding status and progress step for the user."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        workspaces = await get_user_workspaces_list(session, db_user)
        active_ws = next((w for w in workspaces if w.is_active), workspaces[0] if workspaces else None)

        step = db_user.onboarding_step or "personal_details"
        if not db_user.first_name and not db_user.full_name:
            step = "personal_details"
        elif not workspaces:
            step = "workspace"
        elif not db_user.onboarding_completed:
            step = "invites"
        else:
            step = "completed"

        profile = UserProfileResponse(
            telegram_id=db_user.telegram_id,
            username=db_user.username or "",
            full_name=db_user.full_name or "",
            first_name=getattr(db_user, "first_name", "") or "",
            last_name=getattr(db_user, "last_name", "") or "",
            email=db_user.email,
            email_verified=bool(getattr(db_user, "email_verified_at", None)),
            unconfirmed_email=getattr(db_user, "unconfirmed_email", None),
            avatar_url=getattr(db_user, "avatar_url", "") or "",
            role=db_user.role,
            is_approved=db_user.is_approved,
            onboarding_step=step,
            onboarding_completed=bool(db_user.onboarding_completed),
            active_workspace=active_ws,
            workspaces=workspaces,
        )

        return OnboardingStatusResponse(
            onboarding_step=step,
            onboarding_completed=bool(db_user.onboarding_completed),
            user=profile,
            active_workspace=active_ws,
        )


@router.post("/onboarding/personal-details", response_model=OnboardingStatusResponse)
async def submit_onboarding_personal_details(
    req: PersonalDetailsRequest,
    user: User = Depends(get_current_user),
):
    """Save user first name, last name, and optional email during onboarding."""
    first_name = req.first_name.strip()
    last_name = req.last_name.strip()
    if not first_name:
        raise HTTPException(status_code=400, detail="Имя обязательно для заполнения")

    clean_email = req.email.strip().lower() if req.email and req.email.strip() else None

    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        db_user.first_name = first_name
        db_user.last_name = last_name
        db_user.full_name = f"{first_name} {last_name}".strip()
        if clean_email:
            existing_email = (db_user.email or "").strip().lower() or None
            if clean_email != existing_email:
                db_user.email = clean_email
                db_user.email_verified_at = None

        if db_user.onboarding_step in ("personal_details", ""):
            db_user.onboarding_step = "workspace"

        await session.commit()

        workspaces = await get_user_workspaces_list(session, db_user)
        active_ws = next((w for w in workspaces if w.is_active), workspaces[0] if workspaces else None)

        profile = UserProfileResponse(
            telegram_id=db_user.telegram_id,
            username=db_user.username or "",
            full_name=db_user.full_name or "",
            first_name=db_user.first_name or "",
            last_name=db_user.last_name or "",
            email=db_user.email,
            email_verified=bool(getattr(db_user, "email_verified_at", None)),
            unconfirmed_email=getattr(db_user, "unconfirmed_email", None),
            avatar_url=db_user.avatar_url or "",
            role=db_user.role,
            is_approved=db_user.is_approved,
            onboarding_step=db_user.onboarding_step,
            onboarding_completed=bool(db_user.onboarding_completed),
            active_workspace=active_ws,
            workspaces=workspaces,
        )

        return OnboardingStatusResponse(
            onboarding_step=db_user.onboarding_step,
            onboarding_completed=bool(db_user.onboarding_completed),
            user=profile,
            active_workspace=active_ws,
        )


@router.post("/onboarding/avatar")
async def upload_onboarding_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload user avatar during onboarding or settings."""
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        avatar_url = save_image_upload(
            content,
            filename=file.filename or "avatar.png",
            content_type=file.content_type,
            category="avatars",
            owner_user_id=user.id,
        )
    except InvalidImageUpload as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        async with async_session_maker() as session:
            db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
            old_avatar = db_user.avatar_url
            db_user.avatar_url = avatar_url
            await session.commit()
    except Exception:
        delete_local_upload(
            avatar_url,
            "avatars",
            owner_prefix=f"avatar_{user.id}_",
        )
        raise

    delete_local_upload(
        old_avatar,
        "avatars",
        owner_prefix=f"avatar_{user.id}_",
    )

    return {"status": "ok", "avatar_url": avatar_url}


@router.delete("/onboarding/avatar")
async def delete_onboarding_avatar(user: User = Depends(get_current_user)):
    """Reset user avatar to default initial badge."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        old_avatar = db_user.avatar_url
        db_user.avatar_url = ""
        await session.commit()

    delete_local_upload(
        old_avatar,
        "avatars",
        owner_prefix=f"avatar_{user.id}_",
    )

    return {"status": "ok", "avatar_url": ""}


@router.get(
    "/onboarding/check-slug",
    response_model=CheckSlugResponse,
    dependencies=[Depends(rate_limit_dep(limit=30, window_seconds=60, scope="check_slug"))],
)
async def check_workspace_slug(
    slug: str = Query(..., min_length=2, max_length=60),
    user: User = Depends(get_current_user),
):
    """Check availability and validity of workspace slug in real-time."""
    raw_slug = slug.strip().lower()
    cleaned_slug = normalize_workspace_slug(raw_slug)

    if not cleaned_slug or len(cleaned_slug) < 2:
        return CheckSlugResponse(
            slug=raw_slug,
            available=False,
            message="Слаг должен содержать минимум 2 символа (латиница, цифры, дефис)",
        )

    if cleaned_slug in RESERVED_WORKSPACE_SLUGS:
        return CheckSlugResponse(
            slug=cleaned_slug,
            available=False,
            message="Этот слаг зарезервирован системой",
        )

    async with async_session_maker() as session:
        existing = (await session.execute(select(Workspace).where(Workspace.slug == cleaned_slug))).scalar_one_or_none()
        if existing:
            return CheckSlugResponse(
                slug=cleaned_slug,
                available=False,
                message="Этот адрес воркспейса уже занят",
            )

    return CheckSlugResponse(
        slug=cleaned_slug,
        available=True,
        message="Адрес доступен",
    )


@router.post("/onboarding/workspace", response_model=WorkspaceItem)
async def submit_onboarding_workspace(
    req: OnboardingWorkspaceRequest,
    user: User = Depends(get_current_user),
):
    """Create a new workspace during onboarding and set it as active."""
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
        if db_user.onboarding_step in ("personal_details", "workspace", ""):
            db_user.onboarding_step = "invites"

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


@router.post("/onboarding/workspace/logo")
async def upload_workspace_logo(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload workspace logo image."""
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    async with async_session_maker() as session:
        await cleanup_stale_workspace_logos(session)
    try:
        logo_url = save_image_upload(
            content,
            filename=file.filename or "logo.png",
            content_type=file.content_type,
            category="workspaces",
            owner_user_id=user.id,
        )
    except InvalidImageUpload as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "logo_url": logo_url}


@router.post("/onboarding/invites", response_model=OnboardingBulkInvitesResponse)
async def submit_onboarding_invites(
    req: OnboardingBulkInvitesRequest,
    user: User = Depends(get_current_user),
):
    """Create bulk team member invitations and finalize onboarding."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        active_ws = await get_user_workspace(session, db_user)
        if not active_ws:
            raise HTTPException(status_code=400, detail="Не найден активный воркспейс для создания приглашений")

        created_invites: List[WorkspaceInviteItem] = []
        now_dt = datetime.now(timezone.utc)
        expires_at = now_dt + timedelta(days=7)

        for item in req.invites:
            clean_email = item.email.strip().lower()
            if not clean_email or "@" not in clean_email:
                continue

            role = item.role if item.role in ("admin", "buyer", "viewer") else "buyer"
            token = f"inv_{secrets.token_urlsafe(24)}"
            invite = WorkspaceInvite(
                workspace_id=active_ws.id,
                token=token,
                email=clean_email,
                role=role,
                inviter_user_id=db_user.id,
                status="pending",
                max_uses=1,
                used_count=0,
                expires_at=expires_at,
                created_at=now_dt,
            )
            session.add(invite)
            await session.flush()

            session.add(
                AuditEvent(
                    workspace_id=active_ws.id,
                    owner_user_id=db_user.id,
                    actor_type="user",
                    actor_id=str(db_user.id),
                    category="WORKSPACE_INVITE",
                    event_type="INVITE_CREATE",
                    status="SUCCESS",
                    message=f"Создано приглашение для {clean_email}",
                    details={
                        "invite_id": invite.id,
                        "email": clean_email,
                        "role": invite.role,
                        "max_uses": invite.max_uses,
                    },
                )
            )

            created_invites.append(
                WorkspaceInviteItem(
                    id=invite.id,
                    workspace_id=active_ws.id,
                    workspace_name=active_ws.name,
                    token=invite.token,
                    invite_url=f"/invite/{invite.token}",
                    email=invite.email,
                    role=invite.role,
                    status=invite.status,
                    max_uses=invite.max_uses,
                    used_count=invite.used_count,
                    inviter_name=db_user.full_name or db_user.username or "Команда",
                    expires_at=_utc_iso(invite.expires_at),
                    created_at=_utc_iso(invite.created_at),
                )
            )

            if clean_email:
                send_ok = True
                try:
                    inviter_name = db_user.full_name or db_user.username or "Коллега"
                    await send_workspace_invitation_email(
                        to_email=clean_email,
                        workspace_name=active_ws.name,
                        inviter_name=inviter_name,
                        role=invite.role,
                        invite_token=invite.token,
                    )
                except Exception as e:
                    send_ok = False
                    logger.error("Failed to send onboarding invite email to %s: %s", clean_email, e)

                session.add(
                    AuditEvent(
                        workspace_id=active_ws.id,
                        owner_user_id=db_user.id,
                        actor_type="user",
                        actor_id=str(db_user.id),
                        category="WORKSPACE_INVITE",
                        event_type="INVITE_SEND",
                        status="SUCCESS" if send_ok else "FAILED",
                        message=f"Отправка приглашения на {clean_email}: {'успешно' if send_ok else 'ошибка'}",
                        details={"invite_id": invite.id, "email": clean_email},
                    )
                )

        db_user.onboarding_step = "completed"
        db_user.onboarding_completed = True
        await session.commit()

        return OnboardingBulkInvitesResponse(
            sent_count=len(created_invites),
            invites=created_invites,
            onboarding_completed=True,
            redirect_url="/",
        )


@router.post("/onboarding/skip")
async def skip_onboarding(user: User = Depends(get_current_user)):
    """Skip remaining onboarding steps and mark onboarding completed."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        db_user.onboarding_step = "completed"
        db_user.onboarding_completed = True
        await session.commit()
    return {"status": "ok", "onboarding_completed": True, "redirect_url": "/"}
