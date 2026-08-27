import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update

from api.auth import get_current_user
from api.deps import _utc_iso, get_user_workspaces_list
from api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RequestTemporaryPasswordRequest,
    UpdateProfileRequest,
    UserProfileResponse,
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
    AnalyticsViewPreference,
    AuditEvent,
    AutomationScheduleState,
    EmailVerificationCode,
    RuleExecutionState,
    RuleGroup,
    RulePreset,
    SummarySnapshot,
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth & Profile"])


@router.post(
    "/auth/request-temporary-password",
    dependencies=[Depends(rate_limit_dep(limit=5, window_seconds=60, scope="otp_req"))],
)
async def request_temporary_password(req: RequestTemporaryPasswordRequest):
    """Generate and email a 6-digit one-time password (OTP) for login/registration."""
    email_clean = req.email.strip().lower()
    if "@" not in email_clean or "." not in email_clean:
        raise HTTPException(status_code=400, detail="Некорректный адрес электронной почты")

    async with async_session_maker() as session:
        now_dt = datetime.now(timezone.utc)

        # Rate limiting: max 1 request per 60 seconds per email
        recent_otp = (
            await session.execute(
                select(EmailVerificationCode)
                .where(
                    (func.lower(EmailVerificationCode.email) == email_clean)
                    & (EmailVerificationCode.created_at > now_dt - timedelta(seconds=60))
                )
                .order_by(EmailVerificationCode.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if recent_otp:
            raise HTTPException(
                status_code=429,
                detail="Код уже был отправлен недавно. Подождите 1 минуту перед повторным запросом.",
            )

        stmt = select(User).where(
            (func.lower(User.email) == email_clean)
            | (func.lower(User.username) == email_clean)
        )
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if not user:
            user = User(
                username=email_clean,
                email=email_clean,
                role="buyer",
                is_approved=True,
                onboarding_completed=False,
                onboarding_step="personal_details",
                auth_token=secrets.token_urlsafe(32),
            )
            session.add(user)
            await session.flush()
        elif not user.email:
            user.email = email_clean

        # Generate 6-digit random code
        code = str(secrets.randbelow(900000) + 100000)
        expires_at = now_dt + timedelta(minutes=15)

        otp_record = EmailVerificationCode(
            email=email_clean,
            code=code,
            expires_at=expires_at,
            is_used=False,
            failed_attempts=0,
            created_at=now_dt,
        )
        session.add(otp_record)
        await session.commit()

        # Send email via Resend / SMTP
        sent = False
        try:
            sent = await send_otp_verification_email(email_clean, code)
        except Exception as e:
            logger.error("Failed to send OTP email to %s: %s", email_clean, e)

        if not sent and settings.RESEND_API_KEY:
            raise HTTPException(
                status_code=502,
                detail="Не удалось доставить письмо с проверочным кодом. Пожалуйста, попробуйте позже.",
            )

        return {"ok": True, "message": "Временный пароль отправлен на вашу почту"}


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    dependencies=[Depends(rate_limit_dep(limit=10, window_seconds=60, scope="login"))],
)
async def login_user(req: LoginRequest):
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

        is_password_valid = user and verify_password(req.password, user.password_hash)
        otp_valid = False

        if not is_password_valid and user:
            # Check for valid unexpired OTP code
            now_dt = datetime.now(timezone.utc)
            check_email = (user.email or uname).lower()
            otp_stmt = (
                select(EmailVerificationCode)
                .where(
                    (func.lower(EmailVerificationCode.email) == check_email)
                    & (EmailVerificationCode.is_used == False)
                    & (EmailVerificationCode.expires_at > now_dt)
                )
                .order_by(EmailVerificationCode.id.desc())
                .limit(1)
            )
            otp_res = await session.execute(otp_stmt)
            otp_record = otp_res.scalar_one_or_none()
            if otp_record:
                import hmac
                entered_code = req.password.strip()
                if hmac.compare_digest(otp_record.code, entered_code):
                    otp_record.is_used = True
                    otp_valid = True
                else:
                    otp_record.failed_attempts = (otp_record.failed_attempts or 0) + 1
                    if otp_record.failed_attempts >= 5:
                        otp_record.is_used = True
                        await session.commit()
                        raise HTTPException(
                            status_code=401,
                            detail="Превышено максимальное количество попыток ввода кода. Запросите новый код.",
                        )
                    await session.commit()

        if not user or (not is_password_valid and not otp_valid):
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")

        if not user.is_approved:
            raise HTTPException(status_code=403, detail="Ваш аккаунт ожидает одобрения администратора.")

        credentials_changed = False
        if is_password_valid and password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(req.password)
            credentials_changed = True

        if not user.auth_token:
            user.auth_token = secrets.token_urlsafe(32)
            credentials_changed = True

        if credentials_changed or otp_valid:
            await session.commit()

        return LoginResponse(
            token=user.auth_token,
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
            db_user.email = clean_email
        if req.avatar_url is not None:
            db_user.avatar_url = req.avatar_url.strip()
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
async def logout_user(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        res = await session.execute(select(User).where(User.id == user.id))
        db_user = res.scalar_one_or_none()
        if db_user:
            db_user.auth_token = secrets.token_urlsafe(32)
            await session.commit()
    return {"message": "Успешный выход"}


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
