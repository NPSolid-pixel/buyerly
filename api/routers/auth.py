import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, or_, select, update

from api.auth import clear_session_cookies, create_web_session, get_current_user
from api.deps import _utc_iso, get_user_workspaces_list
from api.schemas import (
    AddAllowedEmailRequest,
    AllowedEmailItem,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RequestEmailChangeRequest,
    RequestTemporaryPasswordRequest,
    UpdateProfileRequest,
    UserProfileResponse,
    VerifyTemporaryPasswordRequest,
    WebSessionItem,
    VerifyEmailChangeRequest,
)
from core.config import settings
from core.email import send_otp_verification_email
from core.rate_limit import rate_limit_dep
from database.db import (
    async_session_maker,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from database.models import (
    Account,
    ActionUndoState,
    AllowedEmail,
    AnalyticsViewPreference,
    AuditEvent,
    AutomationScheduleState,
    RuleExecutionState,
    RuleGroup,
    RulePreset,
    SummarySnapshot,
    User,
    WebSession,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
)
from services.image_uploads import delete_local_upload, is_owned_avatar
from services.otp import (
    OTP_EMAIL_CHANGE,
    OTP_EMAIL_VERIFICATION,
    OTP_LOGIN,
    consume_otp,
    create_otp,
    email_scope,
    has_recent_active_otp,
    invalidate_otp,
    login_scope,
    mark_otp_delivered,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth & Profile"])


async def is_email_allowed_for_login(session, email: str) -> bool:
    """Check if the normalized email is authorized to request an OTP and log in/register.

    Allowed when:
    1. Present in AllowedEmail (the whitelist).
    2. Has a valid active WorkspaceInvite addressed specifically to this email.
    """
    clean_email = email.strip().lower()
    if not clean_email or "@" not in clean_email:
        return False

    # 1. Check AllowedEmail whitelist
    allowed_res = await session.execute(
        select(AllowedEmail).where(func.lower(AllowedEmail.email) == clean_email)
    )
    if allowed_res.scalar_one_or_none() is not None:
        return True

    # 2. Check active WorkspaceInvite
    now = datetime.now(timezone.utc)
    invite_res = await session.execute(
        select(WorkspaceInvite).where(
            func.lower(WorkspaceInvite.email) == clean_email,
            WorkspaceInvite.expires_at > now,
            or_(
                WorkspaceInvite.max_uses.is_(None),
                WorkspaceInvite.uses_count < WorkspaceInvite.max_uses,
            ),
        )
    )
    if invite_res.scalar_one_or_none() is not None:
        return True

    return False


@router.post(
    "/auth/request-temporary-password",
    dependencies=[Depends(rate_limit_dep(
        limit=5,
        window_seconds=60,
        scope="otp_req",
        identity_fields=("email",),
    ))],
)
async def request_temporary_password(req: RequestTemporaryPasswordRequest):
    """Generate and email a 6-digit one-time password (OTP) for login/registration."""
    email_clean = req.email.strip().lower()
    if "@" not in email_clean or "." not in email_clean:
        raise HTTPException(status_code=400, detail="Некорректный адрес электронной почты")

    async with async_session_maker() as session:
        if not await is_email_allowed_for_login(session, email_clean):
            raise HTTPException(
                status_code=403,
                detail="Доступ ограничен. Данный email не найден в списке разрешенных. Обратитесь к администратору.",
            )

        scope = login_scope(email_clean)
        if await has_recent_active_otp(session, scope=scope):
            raise HTTPException(
                status_code=429,
                detail="Код уже был отправлен недавно. Подождите 1 минуту перед повторным запросом.",
            )

        issued = await create_otp(
            session,
            email=email_clean,
            purpose=OTP_LOGIN,
            scope=scope,
        )
        await session.commit()

        sent = False
        try:
            sent = await send_otp_verification_email(email_clean, issued.code)
        except Exception as e:
            logger.error("Failed to send OTP email to %s: %s", email_clean, e)

        if not sent and settings.RESEND_API_KEY:
            await invalidate_otp(session, issued.record_id)
            await session.commit()
            raise HTTPException(
                status_code=502,
                detail="Не удалось доставить письмо с проверочным кодом. Пожалуйста, попробуйте позже.",
            )

        if not await mark_otp_delivered(session, issued.record_id):
            await session.rollback()
            raise HTTPException(status_code=409, detail="Запрос кода был заменён более новым запросом")
        await session.commit()

        return {"ok": True, "message": "Временный пароль отправлен на вашу почту"}


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    dependencies=[Depends(rate_limit_dep(
        limit=10,
        window_seconds=60,
        scope="login",
        identity_fields=("username",),
    ))],
)
async def login_user(req: LoginRequest, request: Request, response: Response):
    async with async_session_maker() as session:
        uname = req.username.strip()

        # Prefer stable identifiers (username, email, or telegram_id). A display name is accepted only when unique.
        stmt = select(User).where(
            (func.lower(User.username) == uname.lower())
            | (func.lower(User.email) == uname.lower())
            | (User.telegram_id == uname)
        )
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if not user and uname:
            display_name_result = await session.execute(
                select(User)
                .where(func.lower(User.full_name) == uname.lower())
                .limit(2)
            )
            display_name_matches = display_name_result.scalars().all()
            if len(display_name_matches) == 1:
                user = display_name_matches[0]

        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")

        if not user.is_approved:
            raise HTTPException(status_code=403, detail="Ваш аккаунт ожидает одобрения администратора.")

        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(req.password)

        await create_web_session(
            session,
            user=user,
            request=request,
            response=response,
        )
        await session.commit()

        return LoginResponse(
            username=user.username,
            full_name=user.full_name or user.username,
            role=user.role,
            message="Авторизация успешна",
        )


@router.post(
    "/auth/verify-temporary-password",
    response_model=LoginResponse,
    dependencies=[Depends(rate_limit_dep(
        limit=10,
        window_seconds=60,
        scope="verify_login_otp",
        identity_fields=("email",),
    ))],
)
async def verify_temporary_password(
    req: VerifyTemporaryPasswordRequest,
    request: Request,
    response: Response,
):
    """Atomically verify a delivered login OTP, then create/sign in the user."""
    email_clean = req.email.strip().lower()
    if "@" not in email_clean or "." not in email_clean:
        raise HTTPException(status_code=400, detail="Некорректный адрес электронной почты")

    async with async_session_maker() as session:
        result = await consume_otp(
            session,
            scope=login_scope(email_clean),
            entered_code=req.code,
        )
        if result.status != "consumed" or result.purpose != OTP_LOGIN:
            await session.commit()
            if result.status == "locked":
                raise HTTPException(
                    status_code=401,
                    detail="Превышено максимальное количество попыток ввода кода. Запросите новый код.",
                )
            raise HTTPException(status_code=401, detail="Неверный или просроченный временный пароль")

        is_allowed = await is_email_allowed_for_login(session, email_clean)
        if not is_allowed:
            await session.commit()
            raise HTTPException(status_code=403, detail="Доступ ограничен. Данный email не найден в списке разрешенных.")

        user = (
            await session.execute(
                select(User).where(
                    (func.lower(User.email) == email_clean)
                    | (func.lower(User.username) == email_clean)
                )
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(
                username=email_clean,
                email=email_clean,
                email_verified_at=datetime.now(timezone.utc),
                role="buyer",
                is_approved=True,
                onboarding_completed=False,
                onboarding_step="personal_details",
            )
            session.add(user)
            await session.flush()
        else:
            if not user.email_verified_at:
                user.email_verified_at = datetime.now(timezone.utc)
            if not user.is_approved and is_allowed:
                user.is_approved = True

        if not user.is_approved:
            await session.commit()
            raise HTTPException(status_code=403, detail="Ваш аккаунт ожидает одобрения администратора.")

        await create_web_session(
            session,
            user=user,
            request=request,
            response=response,
        )
        await session.commit()
        return LoginResponse(
            username=user.username,
            full_name=user.full_name or user.username,
            role=user.role,
            message="Авторизация успешна",
        )


@router.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, user: User = Depends(get_current_user)):
    new_pw = req.new_password
    if len(new_pw) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 8 символов")

    async with async_session_maker() as session:
        res = await session.execute(select(User).where(User.id == user.id))
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        if db_user.password_hash:
            if not req.old_password or not verify_password(req.old_password, db_user.password_hash):
                raise HTTPException(status_code=400, detail="Старый пароль указан неверно")

        db_user.password_hash = hash_password(new_pw)
        await session.commit()
        return {"message": "Пароль успешно обновлен"}


async def _deliver_otp(session, *, issued, email: str, log_context: str) -> None:
    sent = False
    try:
        sent = await send_otp_verification_email(email, issued.code)
    except Exception as exc:
        logger.error("Failed to send %s to %s: %s", log_context, email, exc)

    if not sent and settings.RESEND_API_KEY:
        await invalidate_otp(session, issued.record_id)
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail="Не удалось доставить письмо с проверочным кодом. Пожалуйста, попробуйте позже.",
        )
    if not await mark_otp_delivered(session, issued.record_id):
        await session.rollback()
        raise HTTPException(status_code=409, detail="Запрос кода был заменён более новым запросом")


@router.post(
    "/auth/request-email-verification",
    dependencies=[Depends(rate_limit_dep(limit=5, window_seconds=60, scope="verify_req"))],
)
async def request_email_verification(user: User = Depends(get_current_user)):
    """Request OTP code to verify the current unverified email of logged-in user."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        target_email = (db_user.email or "").strip().lower()
        if not target_email or "@" not in target_email or "." not in target_email:
            raise HTTPException(status_code=400, detail="У вас не указан корректный адрес электронной почты")
        if db_user.email_verified_at is not None:
            return {"ok": True, "message": "Email уже подтвержден", "already_verified": True}

        scope = email_scope(db_user.id)
        if await has_recent_active_otp(session, scope=scope):
            raise HTTPException(
                status_code=429,
                detail="Код уже был отправлен недавно. Подождите 1 минуту перед повторным запросом.",
            )

        issued = await create_otp(
            session,
            email=target_email,
            purpose=OTP_EMAIL_VERIFICATION,
            scope=scope,
        )
        await session.commit()
        await _deliver_otp(session, issued=issued, email=target_email, log_context="verification OTP")
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        db_user.unconfirmed_email = target_email
        await session.commit()

        return {"ok": True, "message": "Код подтверждения отправлен на вашу почту"}


@router.post(
    "/auth/request-email-change",
    dependencies=[Depends(rate_limit_dep(limit=5, window_seconds=60, scope="email_change_req"))],
)
async def request_email_change(
    req: RequestEmailChangeRequest,
    user: User = Depends(get_current_user),
):
    """Request OTP code to attach or change user email."""
    clean_email = req.new_email.strip().lower()
    if not clean_email or "@" not in clean_email or "." not in clean_email:
        raise HTTPException(status_code=400, detail="Некорректный адрес электронной почты")

    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()

        # If user is already verified with this exact email, no action needed
        if (db_user.email or "").strip().lower() == clean_email and db_user.email_verified_at is not None:
            return {"ok": True, "message": "Этот email уже подтвержден для вашего аккаунта", "already_verified": True}

        # Check collision: another user already has verified email
        collision = (
            await session.execute(
                select(User.id).where(
                    func.lower(User.email) == clean_email,
                    User.id != db_user.id,
                    User.email_verified_at.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if collision is not None:
            raise HTTPException(status_code=409, detail="Этот адрес электронной почты уже используется другим пользователем")

        scope = email_scope(db_user.id)
        if await has_recent_active_otp(session, scope=scope):
            raise HTTPException(
                status_code=429,
                detail="Код уже был отправлен недавно. Подождите 1 минуту перед повторным запросом.",
            )

        issued = await create_otp(
            session,
            email=clean_email,
            purpose=OTP_EMAIL_CHANGE,
            scope=scope,
        )
        await session.commit()
        await _deliver_otp(session, issued=issued, email=clean_email, log_context="email change OTP")
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        db_user.unconfirmed_email = clean_email
        await session.commit()

        return {"ok": True, "message": "Код подтверждения отправлен на новый email", "unconfirmed_email": clean_email}


@router.post(
    "/auth/verify-email-change",
    dependencies=[Depends(rate_limit_dep(limit=10, window_seconds=60, scope="verify_email_code"))],
)
async def verify_email_change(
    req: VerifyEmailChangeRequest,
    user: User = Depends(get_current_user),
):
    """Atomically verify OTP and activate the email bound to this user."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        target_email = db_user.unconfirmed_email or db_user.email
        if not target_email:
            raise HTTPException(status_code=400, detail="Нет активного запроса на подтверждение email")

        target_email = target_email.strip().lower()
        result = await consume_otp(
            session,
            scope=email_scope(db_user.id),
            entered_code=req.code,
        )
        if result.status != "consumed" or result.purpose not in {
            OTP_EMAIL_CHANGE,
            OTP_EMAIL_VERIFICATION,
        }:
            await session.commit()
            if result.status == "locked":
                raise HTTPException(
                    status_code=401,
                    detail="Превышено максимальное количество попыток ввода кода. Запросите новый код.",
                )
            raise HTTPException(status_code=400, detail="Неверный код подтверждения")
        if result.email != target_email:
            await session.rollback()
            raise HTTPException(status_code=400, detail="Код не соответствует активному запросу email")

        now_dt = datetime.now(timezone.utc)

        # Check collision right before committing
        collision = (
            await session.execute(
                select(User.id).where(
                    func.lower(User.email) == target_email,
                    User.id != db_user.id,
                    User.email_verified_at.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if collision is not None:
            raise HTTPException(status_code=409, detail="Этот email уже был подтвержден другим пользователем")

        # Clear legacy unverified duplicate holders
        other_unverified = (
            await session.execute(
                select(User).where(func.lower(User.email) == target_email, User.id != db_user.id)
            )
        ).scalars().all()
        for other_u in other_unverified:
            other_u.email = None

        db_user.email = target_email
        db_user.email_verified_at = now_dt
        db_user.unconfirmed_email = None

        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("Error activating email %s for user %s: %s", target_email, user.id, e)
            raise HTTPException(status_code=409, detail="Не удалось активировать email из-за конфликта уникальности")

        return {
            "ok": True,
            "message": "Email успешно подтвержден и привязан к аккаунту",
            "email": db_user.email,
            "email_verified": True,
        }


@router.post("/auth/update-profile")
async def update_profile(req: UpdateProfileRequest, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        res = await session.execute(select(User).where(User.id == user.id))
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        if req.first_name is not None:
            db_user.first_name = req.first_name.strip()
        if req.last_name is not None:
            db_user.last_name = req.last_name.strip()
        if req.email is not None:
            clean_email = req.email.strip().lower() if req.email.strip() else None
            existing_email = (db_user.email or "").strip().lower() or None
            if clean_email != existing_email:
                raise HTTPException(
                    status_code=400,
                    detail="Прямое изменение email без подтверждения запрещено. Используйте процедуру верификации через код.",
                )
        old_avatar_url = db_user.avatar_url
        if req.avatar_url is not None:
            new_avatar_url = req.avatar_url.strip()
            if new_avatar_url.startswith("/uploads/avatars/") and not is_owned_avatar(
                new_avatar_url,
                user.id,
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Аватар не найден или принадлежит другому пользователю",
                )
            db_user.avatar_url = new_avatar_url
        if req.full_name is not None:
            db_user.full_name = req.full_name.strip()
        elif req.first_name is not None or req.last_name is not None:
            db_user.full_name = f"{db_user.first_name} {db_user.last_name}".strip()

        if req.telegram_id is not None:
            new_telegram_id = req.telegram_id.strip()
            if not new_telegram_id:
                raise HTTPException(status_code=400, detail="Telegram ID не может быть пустым")
            collision = (
                await session.execute(
                    select(User.id).where(
                        User.telegram_id == new_telegram_id,
                        User.id != db_user.id,
                    )
                )
            ).scalar_one_or_none()
            if collision is not None:
                raise HTTPException(status_code=409, detail="Этот Telegram ID уже используется")

            db_user.telegram_id = new_telegram_id
        await session.commit()
        if old_avatar_url and old_avatar_url != db_user.avatar_url:
            delete_local_upload(
                old_avatar_url,
                "avatars",
                owner_prefix=f"avatar_{user.id}_",
            )
        return {
            "message": "Профиль успешно обновлен",
            "username": db_user.username,
            "full_name": db_user.full_name,
            "first_name": db_user.first_name,
            "last_name": db_user.last_name,
            "email": db_user.email,
            "avatar_url": db_user.avatar_url,
            "telegram_id": db_user.telegram_id,
        }


@router.post("/auth/logout")
async def logout_user(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
):
    session_id = getattr(request.state, "web_session_id", None)
    async with async_session_maker() as session:
        if session_id:
            web_session = (
                await session.execute(
                    select(WebSession).where(
                        WebSession.id == session_id,
                        WebSession.user_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if web_session and web_session.revoked_at is None:
                web_session.revoked_at = datetime.now(timezone.utc)
            await session.commit()
    clear_session_cookies(response)
    return {"message": "Успешный выход"}


@router.get("/auth/sessions", response_model=list[WebSessionItem])
async def list_web_sessions(
    request: Request,
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    current_id = getattr(request.state, "web_session_id", None)
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(WebSession)
                .where(
                    WebSession.user_id == user.id,
                    WebSession.revoked_at.is_(None),
                    WebSession.expires_at > now,
                )
                .order_by(WebSession.last_seen_at.desc(), WebSession.created_at.desc())
            )
        ).scalars().all()
    return [
        WebSessionItem(
            id=item.id,
            user_agent=item.user_agent,
            ip_address=item.ip_address,
            created_at=item.created_at,
            expires_at=item.expires_at,
            last_seen_at=item.last_seen_at,
            current=item.id == current_id,
        )
        for item in rows
    ]


@router.delete("/auth/sessions/{session_id}")
async def revoke_web_session(
    session_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        web_session = (
            await session.execute(
                select(WebSession).where(
                    WebSession.id == session_id,
                    WebSession.user_id == user.id,
                    WebSession.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if web_session is None:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        web_session.revoked_at = datetime.now(timezone.utc)
        await session.commit()

    if session_id == getattr(request.state, "web_session_id", None):
        clear_session_cookies(response)
    return {"message": "Сессия завершена"}


@router.post("/auth/logout-all")
async def logout_all_web_sessions(
    response: Response,
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        await session.execute(
            update(WebSession)
            .where(
                WebSession.user_id == user.id,
                WebSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await session.commit()
    clear_session_cookies(response)
    return {"message": "Все сессии завершены"}


@router.get("/me", response_model=UserProfileResponse)
async def get_me(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        workspaces = await get_user_workspaces_list(session, db_user)
        active_ws = next((w for w in workspaces if w.is_active), workspaces[0] if workspaces else None)

        onboarding_done = bool(getattr(db_user, "onboarding_completed", False))
        if not onboarding_done and len(workspaces) > 0 and getattr(db_user, "first_name", ""):
            onboarding_done = True
            db_user.onboarding_completed = True
            db_user.onboarding_step = "completed"
            await session.commit()

        return UserProfileResponse(
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
            onboarding_step=getattr(db_user, "onboarding_step", "completed") or "completed",
            onboarding_completed=onboarding_done,
            active_workspace=active_ws,
            workspaces=workspaces,
        )


@router.get("/admin/overview")
async def get_admin_overview(user: User = Depends(get_current_user)):
    """Return all users, workspaces, members, and invites for administrative inspection."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ только для администраторов")
    async with async_session_maker() as session:
        users = (await session.execute(select(User).order_by(User.id))).scalars().all()
        workspaces = (await session.execute(select(Workspace).order_by(Workspace.id))).scalars().all()
        members = (await session.execute(select(WorkspaceMember).order_by(WorkspaceMember.id))).scalars().all()
        invites = (await session.execute(select(WorkspaceInvite).order_by(WorkspaceInvite.id))).scalars().all()

        return {
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "full_name": u.full_name,
                    "first_name": getattr(u, "first_name", "") or "",
                    "last_name": getattr(u, "last_name", "") or "",
                    "email": u.email,
                    "role": u.role,
                    "is_approved": u.is_approved,
                    "onboarding_completed": u.onboarding_completed,
                    "active_workspace_id": u.active_workspace_id,
                    "created_at": _utc_iso(u.created_at),
                }
                for u in users
            ],
            "workspaces": [
                {
                    "id": w.id,
                    "name": w.name,
                    "slug": w.slug,
                    "badge_text": w.badge_text,
                    "badge_color": w.badge_color,
                    "owner_user_id": w.owner_user_id,
                    "created_at": _utc_iso(w.created_at),
                }
                for w in workspaces
            ],
            "members": [
                {
                    "workspace_id": m.workspace_id,
                    "user_id": m.user_id,
                    "role": m.role,
                    "joined_at": _utc_iso(m.joined_at),
                }
                for m in members
            ],
            "invites": [
                {
                    "id": inv.id,
                    "workspace_id": inv.workspace_id,
                    "email": inv.email,
                    "role": inv.role,
                    "status": inv.status,
                    "created_at": _utc_iso(inv.created_at),
                }
                for inv in invites
            ],
        }


@router.get("/auth/admin/allowed-emails", response_model=List[AllowedEmailItem])
async def list_allowed_emails(user: User = Depends(get_current_user)):
    """List all allowed email addresses in the whitelist (admin only)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ только для администраторов")
    async with async_session_maker() as session:
        result = await session.execute(
            select(AllowedEmail).order_by(AllowedEmail.created_at.desc())
        )
        emails = result.scalars().all()
        return [
            AllowedEmailItem(
                id=e.id,
                email=e.email,
                added_by=e.added_by,
                comment=e.comment,
                created_at=e.created_at,
            )
            for e in emails
        ]


@router.post("/auth/admin/allowed-emails", response_model=AllowedEmailItem)
async def add_allowed_email(req: AddAllowedEmailRequest, user: User = Depends(get_current_user)):
    """Add an email address to the whitelist (admin only)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ только для администраторов")
    clean_email = req.email.strip().lower()
    if "@" not in clean_email or "." not in clean_email or len(clean_email) < 5:
        raise HTTPException(status_code=400, detail="Некорректный адрес электронной почты")

    async with async_session_maker() as session:
        existing = (
            await session.execute(
                select(AllowedEmail).where(func.lower(AllowedEmail.email) == clean_email)
            )
        ).scalar_one_or_none()
        if existing:
            if req.comment and req.comment != existing.comment:
                existing.comment = req.comment.strip()
                await session.commit()
            return AllowedEmailItem(
                id=existing.id,
                email=existing.email,
                added_by=existing.added_by,
                comment=existing.comment,
                created_at=existing.created_at,
            )

        new_entry = AllowedEmail(
            email=clean_email,
            added_by=user.username or str(user.id),
            comment=req.comment.strip() if req.comment else None,
        )
        session.add(new_entry)
        await session.commit()
        await session.refresh(new_entry)

        return AllowedEmailItem(
            id=new_entry.id,
            email=new_entry.email,
            added_by=new_entry.added_by,
            comment=new_entry.comment,
            created_at=new_entry.created_at,
        )


@router.delete("/auth/admin/allowed-emails/{email_id}")
async def delete_allowed_email(email_id: int, user: User = Depends(get_current_user)):
    """Delete an email address from the whitelist and revoke active sessions (admin only)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ только для администраторов")

    async with async_session_maker() as session:
        entry = (
            await session.execute(
                select(AllowedEmail).where(AllowedEmail.id == email_id)
            )
        ).scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Email не найден в списке разрешенных")

        target_email = entry.email.lower()

        # Find matching users and revoke access / sessions
        matched_users = (
            await session.execute(
                select(User).where(func.lower(User.email) == target_email)
            )
        ).scalars().all()

        for u in matched_users:
            if u.role != "admin":
                u.is_approved = False
                await session.execute(
                    delete(WebSession).where(WebSession.user_id == u.id)
                )

        await session.delete(entry)
        await session.commit()

        return {"ok": True, "message": f"Email {target_email} удален из списка разрешенных"}

