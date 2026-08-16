import logging
import time
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy import select, delete

from core.config import settings
from database.db import async_session_maker
import json
from database.models import Account, StoppedAdSet, AppSettings, TelegramUser, EventLog, RulePreset
from meta_api.client import MetaClient
from bot.handlers import parse_fb_raw_accounts, get_short_account_label
from api.auth import get_current_user


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
meta_client = MetaClient()


# In-memory summary cache: key -> (timestamp, data)
_summary_cache: Dict[str, Any] = {}
SUMMARY_CACHE_TTL = 120  # 2 minutes cache


# ----------------------------------------------------
# Pydantic Schemas
# ----------------------------------------------------
class UserProfileResponse(BaseModel):
    telegram_id: str
    username: str
    full_name: str
    role: str
    is_approved: bool

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    username: str
    full_name: str
    role: str
    message: str = "Успешный вход"

class ChangePasswordRequest(BaseModel):
    old_password: Optional[str] = ""
    new_password: str

class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    telegram_id: Optional[str] = None


class ConditionItem(BaseModel):
    metric: str = "spend"  # spend, cpl, cpr, cpa, leads, registrations, purchases, ctr, cpc
    operator: str = "gte"  # gte, gt, lte, lt, eq
    value: float = 0.0
    time_window: str = "today"  # today, yesterday, last_3d, last_7d

class RulePresetItem(BaseModel):
    id: int
    name: str
    action: str
    conditions: List[ConditionItem]
    condition_logic: str = "and"
    cooldown_minutes: int = 0
    check_interval_minutes: int = 5
    notify_tg: bool = True
    budget_change_percent: float = 0.0
    budget_max_daily: float = 0.0
    created_at: str

class CreatePresetRequest(BaseModel):
    name: str
    action: Optional[str] = "turn_off"
    conditions: List[ConditionItem] = Field(default_factory=list)
    condition_logic: Optional[str] = "and"
    cooldown_minutes: Optional[int] = 0
    check_interval_minutes: Optional[int] = Field(default=5, ge=1, le=1440)
    notify_tg: Optional[bool] = True
    budget_change_percent: Optional[float] = 0.0
    budget_max_daily: Optional[float] = 0.0

class ApplyPresetRequest(BaseModel):
    preset_id: Optional[int] = None
    name: Optional[str] = ""
    action: Optional[str] = "turn_off"
    conditions: List[ConditionItem] = Field(default_factory=list)
    condition_logic: Optional[str] = "and"
    cooldown_minutes: Optional[int] = 0
    check_interval_minutes: Optional[int] = 5
    notify_tg: Optional[bool] = True
    budget_change_percent: Optional[float] = 0.0
    budget_max_daily: Optional[float] = 0.0

class AccountItem(BaseModel):
    id: int
    account_id: str
    name: str
    owner_id: str
    batch_name: str
    timezone_name: str
    account_status: int
    status_label: str
    rules_enabled: bool
    is_active: bool
    active_rules: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str

class ParseRawRequest(BaseModel):
    raw_text: str

class ParsedAccountItem(BaseModel):
    account_id: str
    parsed_name: str

class BatchAddAccountEntry(BaseModel):
    account_id: str
    name: Optional[str] = ""

class BatchAddRequest(BaseModel):
    accounts: List[BatchAddAccountEntry]
    batch_name: Optional[str] = "-"
    access_token: str
    rules_enabled: Optional[bool] = False

class SetIntervalRequest(BaseModel):
    minutes: int = Field(ge=1, le=1440)


# ----------------------------------------------------
# Helper to filter user accounts
# ----------------------------------------------------
async def get_user_accounts(session, user: TelegramUser) -> List[Account]:
    if user.role == "admin":
        stmt = select(Account).order_by(Account.id.desc())
    else:
        stmt = select(Account).where(Account.owner_id == user.telegram_id).order_by(Account.id.desc())
    res = await session.execute(stmt)
    return res.scalars().all()


def _load_active_rules(raw_rules: Any) -> List[Dict[str, Any]]:
    """Return only valid rule snapshots from an account JSON field."""
    try:
        rules = json.loads(raw_rules) if isinstance(raw_rules, str) else raw_rules
    except (TypeError, ValueError):
        return []
    if not isinstance(rules, list):
        return []
    return [rule for rule in rules if isinstance(rule, dict)]


def _preset_snapshot(preset: RulePreset) -> Dict[str, Any]:
    """Build the runtime rule format consumed by RuleEngine."""
    try:
        conditions = json.loads(preset.conditions) if isinstance(preset.conditions, str) else preset.conditions
    except (TypeError, ValueError):
        conditions = []

    return {
        "preset_id": preset.id,
        "name": preset.name,
        "action": preset.action,
        "conditions": conditions if isinstance(conditions, list) else [],
        "logic": preset.condition_logic,
        "cooldown_minutes": preset.cooldown_minutes,
        "check_interval": preset.check_interval_minutes,
        "notify_tg": preset.notify_tg,
        "budget_change_percent": preset.budget_change_percent,
        "budget_max_daily": preset.budget_max_daily,
    }


# ----------------------------------------------------
# Endpoints
# ----------------------------------------------------

import uuid
from database.db import hash_password

@router.post("/auth/login", response_model=LoginResponse)
async def login_user(req: LoginRequest):
    async with async_session_maker() as session:
        uname = req.username.strip()
        
        # Look up user by username, full_name, or telegram_id (case-insensitive)
        stmt = select(TelegramUser).where(
            (TelegramUser.username.ilike(uname)) |
            (TelegramUser.full_name.ilike(uname)) |
            (TelegramUser.telegram_id == uname)
        )
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        # Support alias mappings if typed
        if not user:
            lower_u = uname.lower()
            if lower_u in ["xxq322", "artem"]:
                res = await session.execute(select(TelegramUser).where((TelegramUser.username.ilike("Artem")) | (TelegramUser.telegram_id == "8634201356")))
                user = res.scalar_one_or_none()
            elif lower_u in ["nikolai_underdog", "nikolai"]:
                res = await session.execute(select(TelegramUser).where((TelegramUser.username.ilike("Nikolai")) | (TelegramUser.telegram_id == "8948797431")))
                user = res.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=400, detail="Пользователь не найден")

        pw_hash = hash_password(req.password.strip())
        if user.password_hash and user.password_hash != pw_hash:
            raise HTTPException(status_code=400, detail="Неверный пароль")

        if not user.is_approved:
            raise HTTPException(status_code=403, detail="Ваш аккаунт ожидает одобрения администратора.")

        if not user.auth_token:
            user.auth_token = str(uuid.uuid4())
            await session.commit()

        return LoginResponse(
            token=user.auth_token,
            username=user.username,
            full_name=user.full_name or user.username,
            role=user.role,
            message="Авторизация успешна"
        )


@router.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, user: TelegramUser = Depends(get_current_user)):
    new_pw = req.new_password.strip()
    if not new_pw or len(new_pw) < 4:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 4 символа")

    async with async_session_maker() as session:
        res = await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        if db_user.password_hash and req.old_password:
            old_hash = hash_password(req.old_password.strip())
            if db_user.password_hash != old_hash:
                raise HTTPException(status_code=400, detail="Старый пароль указан неверно")

        db_user.password_hash = hash_password(new_pw)
        await session.commit()
        return {"message": "Пароль успешно обновлен"}


@router.post("/auth/update-profile")
async def update_profile(req: UpdateProfileRequest, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        res = await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        if req.full_name is not None:
            db_user.full_name = req.full_name.strip()
        if req.telegram_id is not None:
            db_user.telegram_id = req.telegram_id.strip()
        await session.commit()
        return {
            "message": "Профиль успешно обновлен",
            "username": db_user.username,
            "full_name": db_user.full_name,
            "telegram_id": db_user.telegram_id
        }


@router.post("/auth/logout")
async def logout_user(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        res = await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))
        db_user = res.scalar_one_or_none()
        if db_user:
            db_user.auth_token = str(uuid.uuid4())
            await session.commit()
    return {"message": "Успешный выход"}



@router.get("/me", response_model=UserProfileResponse)
async def get_me(user: TelegramUser = Depends(get_current_user)):
    return UserProfileResponse(
        telegram_id=user.telegram_id,
        username=user.username or "",
        full_name=user.full_name or "",
        role=user.role,
        is_approved=user.is_approved
    )


@router.get("/accounts", response_model=List[AccountItem])
async def list_accounts(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        accounts = await get_user_accounts(session, user)
        
        items = []
        for a in accounts:
            active_rules_list = _load_active_rules(a.active_rules)
            
            items.append(AccountItem(
                id=a.id,
                account_id=a.account_id,
                name=a.name,
                owner_id=a.owner_id,
                batch_name=a.batch_name or "",
                timezone_name=a.timezone_name or "UTC",
                account_status=a.account_status,
                status_label=a.status_label or "🟢 Активен (ACTIVE)",
                rules_enabled=a.rules_enabled,
                is_active=a.is_active,
                active_rules=active_rules_list,
                created_at=a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else ""
            ))
        return items



# ----------------------------------------------------
# RULE PRESETS ENDPOINTS
# ----------------------------------------------------

@router.get("/presets", response_model=List[RulePresetItem])
async def list_presets(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        stmt = select(RulePreset).where(RulePreset.owner_id == user.telegram_id).order_by(RulePreset.id.desc())
        res = await session.execute(stmt)
        presets = res.scalars().all()
        result = []
        for p in presets:
            try:
                conds = json.loads(p.conditions) if p.conditions else []
            except Exception:
                conds = []
            result.append(RulePresetItem(
                id=p.id,
                name=p.name,
                action=p.action,
                conditions=[ConditionItem(**c) for c in conds if isinstance(c, dict)],
                condition_logic=p.condition_logic or "and",
                cooldown_minutes=p.cooldown_minutes or 0,
                check_interval_minutes=p.check_interval_minutes or 5,
                notify_tg=p.notify_tg if p.notify_tg is not None else True,
                budget_change_percent=p.budget_change_percent or 0.0,
                budget_max_daily=p.budget_max_daily or 0.0,
                created_at=p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else ""
            ))
        return result


@router.post("/presets", response_model=RulePresetItem)
async def create_preset(payload: CreatePresetRequest, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        conds_json = json.dumps([c.model_dump() for c in payload.conditions])
        preset = RulePreset(
            owner_id=user.telegram_id,
            name=payload.name.strip() or "Новое правило",
            action=payload.action or "turn_off",
            conditions=conds_json,
            condition_logic=payload.condition_logic or "and",
            cooldown_minutes=payload.cooldown_minutes or 0,
            check_interval_minutes=payload.check_interval_minutes or 5,
            notify_tg=payload.notify_tg if payload.notify_tg is not None else True,
            budget_change_percent=payload.budget_change_percent or 0.0,
            budget_max_daily=payload.budget_max_daily or 0.0
        )
        session.add(preset)
        await session.commit()
        await session.refresh(preset)
        return RulePresetItem(
            id=preset.id,
            name=preset.name,
            action=preset.action,
            conditions=payload.conditions,
            condition_logic=preset.condition_logic,
            cooldown_minutes=preset.cooldown_minutes,
            check_interval_minutes=preset.check_interval_minutes,
            notify_tg=preset.notify_tg,
            budget_change_percent=preset.budget_change_percent,
            budget_max_daily=preset.budget_max_daily,
            created_at=preset.created_at.strftime("%Y-%m-%d %H:%M") if preset.created_at else ""
        )


@router.put("/presets/{preset_id}", response_model=RulePresetItem)
async def update_preset(preset_id: int, payload: CreatePresetRequest, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        stmt = select(RulePreset).where(RulePreset.id == preset_id)
        if user.role != "admin":
            stmt = stmt.where(RulePreset.owner_id == user.telegram_id)
        res = await session.execute(stmt)
        preset = res.scalar_one_or_none()
        if not preset:
            raise HTTPException(status_code=404, detail="Пресет не найден")
        
        preset.name = payload.name.strip() or preset.name
        preset.action = payload.action or "turn_off"
        preset.conditions = json.dumps([c.model_dump() for c in payload.conditions])
        if payload.condition_logic is not None:
            preset.condition_logic = payload.condition_logic
        if payload.cooldown_minutes is not None:
            preset.cooldown_minutes = payload.cooldown_minutes
        if payload.check_interval_minutes is not None:
            preset.check_interval_minutes = payload.check_interval_minutes
        if payload.notify_tg is not None:
            preset.notify_tg = payload.notify_tg
        if payload.budget_change_percent is not None:
            preset.budget_change_percent = payload.budget_change_percent
        if payload.budget_max_daily is not None:
            preset.budget_max_daily = payload.budget_max_daily

        updated_snapshot = _preset_snapshot(preset)
        account_res = await session.execute(select(Account))
        for account in account_res.scalars().all():
            active_rules = _load_active_rules(account.active_rules)
            changed = False
            for index, active_rule in enumerate(active_rules):
                if active_rule.get("preset_id") == preset_id:
                    active_rules[index] = updated_snapshot.copy()
                    changed = True
            if changed:
                account.active_rules = json.dumps(active_rules)

        await session.commit()
        await session.refresh(preset)
        return RulePresetItem(
            id=preset.id,
            name=preset.name,
            action=preset.action,
            conditions=payload.conditions,
            condition_logic=preset.condition_logic,
            cooldown_minutes=preset.cooldown_minutes,
            check_interval_minutes=preset.check_interval_minutes,
            notify_tg=preset.notify_tg,
            budget_change_percent=preset.budget_change_percent,
            budget_max_daily=preset.budget_max_daily,
            created_at=preset.created_at.strftime("%Y-%m-%d %H:%M") if preset.created_at else ""
        )


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        stmt = select(RulePreset).where(RulePreset.id == preset_id)
        if user.role != "admin":
            stmt = stmt.where(RulePreset.owner_id == user.telegram_id)
        res = await session.execute(stmt)
        preset = res.scalar_one_or_none()
        if not preset:
            raise HTTPException(status_code=404, detail="Пресет не найден")

        # Remove the exact preset ID from every linked account snapshot.
        acc_res = await session.execute(select(Account))
        for acc in acc_res.scalars().all():
            active_rules = _load_active_rules(acc.active_rules)
            remaining_rules = [r for r in active_rules if r.get("preset_id") != preset_id]
            if len(remaining_rules) != len(active_rules):
                acc.active_rules = json.dumps(remaining_rules)
                if not remaining_rules:
                    acc.rules_enabled = False
        
        await session.execute(delete(RulePreset).where(RulePreset.id == preset_id))
        await session.commit()
        return {"success": True, "message": "Пресет удален"}


@router.post("/accounts/{account_id}/assign-rule")
async def assign_rule_to_account(
    account_id: str,
    payload: ApplyPresetRequest,
    user: TelegramUser = Depends(get_current_user)
):
    """Добавляет правило/пресет к списку правил кабинета."""
    async with async_session_maker() as session:
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(Account.owner_id == user.telegram_id)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        # If preset_id provided, load preset
        if payload.preset_id:
            p_stmt = select(RulePreset).where(RulePreset.id == payload.preset_id)
            p_res = await session.execute(p_stmt)
            preset = p_res.scalar_one_or_none()
            if not preset:
                raise HTTPException(status_code=404, detail="Пресет не найден.")
            
            new_rule = _preset_snapshot(preset)
        else:
            raise HTTPException(status_code=400, detail="Custom rules without preset are no longer supported.")

        active_rules = _load_active_rules(acc.active_rules)
            
        # Check if preset already attached
        if any(r.get("preset_id") == new_rule["preset_id"] for r in active_rules):
            raise HTTPException(status_code=400, detail="Это правило уже привязано к кабинету.")
            
        active_rules.append(new_rule)
        acc.active_rules = json.dumps(active_rules)
        acc.rules_enabled = True
        
        await session.commit()
        return {
            "account_id": acc.account_id,
            "active_rules": active_rules,
            "rules_enabled": acc.rules_enabled,
            "message": f"Правило '{new_rule['name']}' успешно добавлено к кабинету"
        }


@router.post("/accounts/{account_id}/detach-rule/{preset_id}")
async def detach_rule_from_account(
    account_id: str,
    preset_id: int,
    user: TelegramUser = Depends(get_current_user)
):
    """Удаляет конкретное правило из списка кабинета."""
    async with async_session_maker() as session:
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(Account.owner_id == user.telegram_id)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        active_rules = _load_active_rules(acc.active_rules)
            
        initial_len = len(active_rules)
        active_rules = [r for r in active_rules if r.get("preset_id") != preset_id]
        
        if len(active_rules) == initial_len:
            raise HTTPException(status_code=404, detail="Правило не найдено в этом кабинете.")

        acc.active_rules = json.dumps(active_rules)
        if len(active_rules) == 0:
            acc.rules_enabled = False
            
        await session.commit()
        return {"status": "ok", "message": "Правило успешно отвязано от кабинета.", "active_rules": active_rules, "rules_enabled": acc.rules_enabled}


@router.post("/accounts/{account_id}/toggle-rules")
async def toggle_rules(account_id: str, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(Account.owner_id == user.telegram_id)
        
        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        active_rules = _load_active_rules(acc.active_rules)
        if not acc.rules_enabled and not active_rules:
            raise HTTPException(
                status_code=400,
                detail="Сначала привяжите хотя бы одно правило к кабинету.",
            )

        acc.rules_enabled = not acc.rules_enabled
        await session.commit()
        return {
            "account_id": acc.account_id,
            "rules_enabled": acc.rules_enabled,
            "message": f"Авто-правила {'включены' if acc.rules_enabled else 'выключены'}"
        }



@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(Account.owner_id == user.telegram_id)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        await session.execute(delete(Account).where(Account.account_id == acc_id))
        await session.commit()
        return {"success": True, "message": f"Кабинет {acc_id} удален"}


@router.post("/accounts/parse-raw", response_model=List[ParsedAccountItem])
async def parse_raw_text(payload: ParseRawRequest, user: TelegramUser = Depends(get_current_user)):
    parsed = parse_fb_raw_accounts(payload.raw_text)
    return [ParsedAccountItem(account_id=p["account_id"], parsed_name=p["parsed_name"]) for p in parsed]


@router.post("/accounts/batch-add")
async def batch_add_accounts(payload: BatchAddRequest, user: TelegramUser = Depends(get_current_user)):
    if not payload.accounts:
        raise HTTPException(status_code=400, detail="Список кабинетов пуст.")
    if not payload.access_token.strip():
        raise HTTPException(status_code=400, detail="Укажите Access Token Meta.")

    token = payload.access_token.strip()
    batch_name = (payload.batch_name or "-").strip()
    owner_id = user.telegram_id

    added_list = []
    error_list = []

    async with async_session_maker() as session:
        for idx, item in enumerate(payload.accounts, start=1):
            acc_id = item.account_id if item.account_id.startswith("act_") else f"act_{item.account_id}"
            custom_name = item.name.strip() if item.name else ""

            try:
                acc_info = await meta_client.get_account_info(acc_id, token)
                timezone_name = acc_info.get("timezone_name", "UTC")
                fb_name = acc_info.get("name", acc_id)
                status_code = acc_info.get("account_status", 1)
                status_label = acc_info.get("status_label", "🟢 Активен (ACTIVE)")

                if batch_name != "-" and len(batch_name) > 0:
                    display_name = f"{batch_name} {idx}" if len(payload.accounts) > 1 else batch_name
                elif custom_name:
                    display_name = custom_name
                else:
                    display_name = fb_name

                res = await session.execute(select(Account).where(Account.account_id == acc_id))
                existing = res.scalar_one_or_none()

                if existing:
                    existing.name = display_name
                    existing.access_token = token
                    existing.timezone_name = timezone_name
                    existing.owner_id = owner_id
                    existing.batch_name = batch_name if batch_name != "-" else ""
                    existing.account_status = status_code
                    existing.status_label = status_label
                    existing.is_active = True
                else:
                    new_acc = Account(
                        account_id=acc_id,
                        name=display_name,
                        access_token=token,
                        owner_id=owner_id,
                        batch_name=batch_name if batch_name != "-" else "",
                        timezone_name=timezone_name,
                        account_status=status_code,
                        status_label=status_label,
                        rules_enabled=payload.rules_enabled or False,
                        is_active=True
                    )
                    session.add(new_acc)

                added_list.append({
                    "account_id": acc_id,
                    "name": display_name,
                    "timezone_name": timezone_name,
                    "status_label": status_label
                })

            except Exception as e:
                logger.error(f"Error in batch_add for {acc_id}: {e}")
                error_list.append({"account_id": acc_id, "error": str(e)})

        await session.commit()

    return {
        "success_count": len(added_list),
        "error_count": len(error_list),
        "added": added_list,
        "errors": error_list
    }


@router.get("/summary")
async def get_summary_report(
    period: str = Query("today", pattern="^(today|yesterday|last_3d|last_7d)$"),
    force: bool = Query(False),
    user: TelegramUser = Depends(get_current_user)
):
    cache_key = f"{user.telegram_id}:{period}"
    now_ts = time.time()

    # Return cached data if valid and force is False
    if not force and cache_key in _summary_cache:
        cached_ts, cached_data = _summary_cache[cache_key]
        if now_ts - cached_ts < SUMMARY_CACHE_TTL:
            return cached_data

    async with async_session_maker() as session:
        accounts = await get_user_accounts(session, user)
        if not accounts:
            empty_res = {
                "period": period,
                "total_spend": 0.0,
                "total_clicks": 0,
                "total_leads": 0,
                "total_regs": 0,
                "total_purchases": 0,
                "total_conversions": 0,
                "avg_cpa": 0.0,
                "avg_cpc": 0.0,
                "avg_ctr": 0.0,
                "accounts_count": 0,
                "accounts": []
            }
            _summary_cache[cache_key] = (now_ts, empty_res)
            return empty_res

        total_spend = 0.0
        total_clicks = 0
        total_impressions = 0
        total_leads = 0
        total_regs = 0
        total_purchases = 0

        account_results = []

        for acc in accounts:
            short_name = get_short_account_label(acc.name, acc.account_id)
            if not acc.is_active or acc.account_status in [2, 101]:
                account_results.append({
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "short_name": short_name,
                    "timezone_name": acc.timezone_name,
                    "account_status": acc.account_status,
                    "status_label": acc.status_label,
                    "rules_enabled": acc.rules_enabled,
                    "spend": 0.0,
                    "clicks": 0,
                    "impressions": 0,
                    "leads": 0,
                    "registrations": 0,
                    "purchases": 0,
                    "total_conversions": 0,
                    "cpa": 0.0,
                    "cpc": 0.0,
                    "ctr": 0.0,
                    "adsets": [],
                    "has_error": False,
                    "is_banned": True
                })
                continue

            try:
                adsets = await meta_client.get_adsets_insights(
                    account_id=acc.account_id,
                    access_token=acc.access_token,
                    date_preset=period
                )
                acc_spend = sum(a.get("spend", 0.0) for a in adsets)
                acc_clicks = sum(a.get("clicks", 0) for a in adsets)
                acc_impressions = sum(a.get("impressions", 0) for a in adsets)
                acc_leads = sum(a.get("leads", 0) for a in adsets)
                acc_regs = sum(a.get("registrations", 0) for a in adsets)
                acc_purchases = sum(a.get("purchases", 0) for a in adsets)
                acc_conv = acc_leads + acc_regs
                acc_cpa = (acc_spend / acc_conv) if acc_conv > 0 else 0.0
                acc_cpc = (acc_spend / acc_clicks) if acc_clicks > 0 else 0.0
                acc_ctr = ((acc_clicks / acc_impressions) * 100) if acc_impressions > 0 else 0.0

                total_spend += acc_spend
                total_clicks += acc_clicks
                total_impressions += acc_impressions
                total_leads += acc_leads
                total_regs += acc_regs
                total_purchases += acc_purchases

                account_results.append({
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "short_name": short_name,
                    "timezone_name": acc.timezone_name,
                    "account_status": acc.account_status,
                    "status_label": acc.status_label,
                    "rules_enabled": acc.rules_enabled,
                    "spend": round(acc_spend, 2),
                    "clicks": acc_clicks,
                    "impressions": acc_impressions,
                    "leads": acc_leads,
                    "registrations": acc_regs,
                    "purchases": acc_purchases,
                    "total_conversions": acc_conv,
                    "cpa": round(acc_cpa, 2),
                    "cpc": round(acc_cpc, 2),
                    "ctr": round(acc_ctr, 2),
                    "adsets": adsets,
                    "has_error": False,
                    "is_banned": False
                })
            except Exception as e:
                logger.error(f"Error fetching insights for {acc.account_id}: {e}")
                account_results.append({
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "short_name": short_name,
                    "timezone_name": acc.timezone_name,
                    "account_status": acc.account_status,
                    "status_label": "⚠️ Ошибка синхронизации",
                    "rules_enabled": acc.rules_enabled,
                    "spend": 0.0,
                    "clicks": 0,
                    "impressions": 0,
                    "leads": 0,
                    "registrations": 0,
                    "purchases": 0,
                    "total_conversions": 0,
                    "cpa": 0.0,
                    "cpc": 0.0,
                    "ctr": 0.0,
                    "adsets": [],
                    "has_error": True,
                    "is_banned": False
                })

        total_conversions = total_leads + total_regs
        avg_cpa = (total_spend / total_conversions) if total_conversions > 0 else 0.0
        avg_cpc = (total_spend / total_clicks) if total_clicks > 0 else 0.0
        avg_ctr = ((total_clicks / total_impressions) * 100) if total_impressions > 0 else 0.0

        res_data = {
            "period": period,
            "total_spend": round(total_spend, 2),
            "total_clicks": total_clicks,
            "total_leads": total_leads,
            "total_regs": total_regs,
            "total_purchases": total_purchases,
            "total_conversions": total_conversions,
            "avg_cpa": round(avg_cpa, 2),
            "avg_cpc": round(avg_cpc, 2),
            "avg_ctr": round(avg_ctr, 2),
            "accounts_count": len(accounts),
            "accounts": account_results
        }
        _summary_cache[cache_key] = (now_ts, res_data)
        return res_data



@router.get("/settings")
async def get_settings(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        interval = app_settings.poll_interval_minutes if app_settings else 10
        return {
            "poll_interval_minutes": interval,
            "admin_chat_id": settings.ADMIN_CHAT_ID,
            "user_role": user.role
        }


@router.post("/settings/interval")
async def set_poll_interval(payload: SetIntervalRequest, user: TelegramUser = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может изменять интервал опроса.")

    async with async_session_maker() as session:
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        if not app_settings:
            app_settings = AppSettings(poll_interval_minutes=payload.minutes)
            session.add(app_settings)
        else:
            app_settings.poll_interval_minutes = payload.minutes
        await session.commit()

    return {
        "success": True,
        "poll_interval_minutes": payload.minutes,
        "message": f"Базовый интервал мониторинга изменен на {payload.minutes} минут"
    }


@router.get("/adsets/stopped")
async def list_stopped_adsets(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        user_accounts = await get_user_accounts(session, user)
        acc_ids = [a.account_id for a in user_accounts]
        if not acc_ids:
            return []

        stmt = select(StoppedAdSet).where(
            StoppedAdSet.account_id.in_(acc_ids),
            StoppedAdSet.is_resolved == False
        ).order_by(StoppedAdSet.stopped_at.desc())
        res = await session.execute(stmt)
        records = res.scalars().all()

        return [
            {
                "id": r.id,
                "account_id": r.account_id,
                "adset_id": r.adset_id,
                "adset_name": r.adset_name,
                "stop_spend": r.stop_spend,
                "stop_leads": r.stop_leads,
                "stop_registrations": r.stop_registrations,
                "stopped_at": r.stopped_at.strftime("%Y-%m-%d %H:%M") if r.stopped_at else ""
            }
            for r in records
        ]


@router.post("/adsets/{adset_id}/reactivate")
async def reactivate_adset(adset_id: str, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        stopped_res = await session.execute(select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id))
        stopped_entry = stopped_res.scalar_one_or_none()
        if not stopped_entry:
            raise HTTPException(status_code=404, detail="Запись об остановленном адсете не найдена.")

        acc_res = await session.execute(select(Account).where(Account.account_id == stopped_entry.account_id))
        account = acc_res.scalar_one_or_none()
        if not account or (user.role != "admin" and account.owner_id != user.telegram_id):
            raise HTTPException(status_code=403, detail="Доступ запрещен.")

        try:
            await meta_client.set_adset_status(adset_id=adset_id, access_token=account.access_token, status="ACTIVE")
            stopped_entry.is_resolved = True
            await session.commit()
            return {"success": True, "message": f"Адсет {adset_id} успешно включен!"}
        except Exception as e:
            logger.error(f"Error reactivating adset {adset_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка Meta API: {e}")


@router.post("/adsets/{adset_id}/dismiss")
async def dismiss_adset(adset_id: str, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        stopped_res = await session.execute(select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id))
        stopped_entry = stopped_res.scalar_one_or_none()
        if not stopped_entry:
            raise HTTPException(status_code=404, detail="Запись не найдена.")

        stopped_entry.is_resolved = True
        await session.commit()
        return {"success": True, "message": "Оставлен выключенным."}
