import logging
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, func, or_

from core.audit import build_audit_event
from core.config import settings
from core.metrics import (
    SUMMARY_METRIC_DEFINITIONS,
    cost_per_event,
    normalize_rule_conditions,
    normalize_runtime_rule,
    validate_public_rule_conditions,
)
from database.db import async_session_maker, hash_password, password_needs_rehash, verify_password
import json
from database.models import (
    Account,
    AuditEvent,
    StoppedAdSet,
    AppSettings,
    TelegramUser,
    EventLog,
    RulePreset,
    RuleGroup,
    RuleGroupItem,
)
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
    old_password: str = ""
    new_password: str

class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    telegram_id: Optional[str] = None


class ConditionItem(BaseModel):
    metric: str = "spend"  # spend, cpl, cpreg, cpp, leads, registrations, purchases, ctr, cpc
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

class RuleGroupWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    preset_ids: List[int] = Field(min_length=1, max_length=50)

class RuleGroupResponse(BaseModel):
    id: int
    name: str
    description: str
    preset_ids: List[int]
    rules: List[RulePresetItem]
    created_at: str

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
    normalized = []
    for rule in rules:
        if isinstance(rule, dict):
            normalized_rule, _, _ = normalize_runtime_rule(rule)
            normalized.append(normalized_rule)
    return normalized


def _load_json_object(raw_value: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _utc_iso(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _cost_or_none(spend: float, count: int) -> Optional[float]:
    return cost_per_event(spend, count, digits=2)


def _summary_with_cache_metadata(
    payload: Dict[str, Any],
    *,
    is_cached: bool,
    age_seconds: float = 0.0,
) -> Dict[str, Any]:
    return {
        **payload,
        "cache": {
            "is_cached": is_cached,
            "age_seconds": round(max(0.0, age_seconds), 1),
            "ttl_seconds": SUMMARY_CACHE_TTL,
        },
    }


def _preset_snapshot(preset: RulePreset) -> Dict[str, Any]:
    """Build the runtime rule format consumed by RuleEngine."""
    try:
        conditions = json.loads(preset.conditions) if isinstance(preset.conditions, str) else preset.conditions
    except (TypeError, ValueError):
        conditions = []

    normalized_conditions, _, has_legacy_cpa = normalize_rule_conditions(conditions)
    snapshot = {
        "preset_id": preset.id,
        "name": preset.name,
        "action": preset.action,
        "conditions": normalized_conditions,
        "logic": preset.condition_logic,
        "cooldown_minutes": preset.cooldown_minutes,
        "check_interval": preset.check_interval_minutes,
        "notify_tg": preset.notify_tg,
        "budget_change_percent": preset.budget_change_percent,
        "budget_max_daily": preset.budget_max_daily,
    }
    if has_legacy_cpa:
        snapshot.update(
            {
                "enabled": False,
                "needs_review": True,
                "review_reason": "Замените старый общий CPA на CPL, CPReg или CPP.",
            }
        )
    return snapshot


def _preset_response(preset: RulePreset) -> RulePresetItem:
    try:
        raw_conditions = json.loads(preset.conditions) if preset.conditions else []
    except (TypeError, ValueError):
        raw_conditions = []
    normalized_conditions, _, _ = normalize_rule_conditions(raw_conditions)
    conditions = [
        ConditionItem(**condition)
        for condition in normalized_conditions
        if isinstance(condition, dict)
    ]
    return RulePresetItem(
        id=preset.id,
        name=preset.name,
        action=preset.action,
        conditions=conditions,
        condition_logic=preset.condition_logic or "and",
        cooldown_minutes=preset.cooldown_minutes or 0,
        check_interval_minutes=preset.check_interval_minutes or 5,
        notify_tg=preset.notify_tg if preset.notify_tg is not None else True,
        budget_change_percent=preset.budget_change_percent or 0.0,
        budget_max_daily=preset.budget_max_daily or 0.0,
        created_at=preset.created_at.strftime("%Y-%m-%d %H:%M") if preset.created_at else "",
    )


def _validated_condition_payloads(conditions: List[ConditionItem]) -> List[Dict[str, Any]]:
    payloads = [condition.model_dump() for condition in conditions]
    try:
        validate_public_rule_conditions(payloads)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="Используйте только актуальные метрики и операторы автоправил.",
        ) from error
    normalized, _, _ = normalize_rule_conditions(payloads)
    return normalized


def _unique_preset_ids(preset_ids: List[int]) -> List[int]:
    return list(dict.fromkeys(preset_ids))


def _clean_rule_group_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Введите название группы.")
    return name


async def _get_owned_presets(session, owner_id: str, preset_ids: List[int]) -> List[RulePreset]:
    ordered_ids = _unique_preset_ids(preset_ids)
    result = await session.execute(
        select(RulePreset).where(
            RulePreset.owner_id == owner_id,
            RulePreset.id.in_(ordered_ids),
        )
    )
    by_id = {preset.id: preset for preset in result.scalars().all()}
    missing_ids = [preset_id for preset_id in ordered_ids if preset_id not in by_id]
    if missing_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Правила не найдены или недоступны: {', '.join(map(str, missing_ids))}",
        )
    return [by_id[preset_id] for preset_id in ordered_ids]


def _rule_group_response(group: RuleGroup, presets: List[RulePreset]) -> RuleGroupResponse:
    return RuleGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description or "",
        preset_ids=[preset.id for preset in presets],
        rules=[_preset_response(preset) for preset in presets],
        created_at=group.created_at.strftime("%Y-%m-%d %H:%M") if group.created_at else "",
    )


async def _load_group_presets(session, group_ids: List[int]) -> Dict[int, List[RulePreset]]:
    if not group_ids:
        return {}
    item_rows = (
        await session.execute(
            select(RuleGroupItem)
            .where(RuleGroupItem.group_id.in_(group_ids))
            .order_by(RuleGroupItem.group_id, RuleGroupItem.position, RuleGroupItem.id)
        )
    ).scalars().all()
    preset_ids = list(dict.fromkeys(item.preset_id for item in item_rows))
    presets = (
        await session.execute(select(RulePreset).where(RulePreset.id.in_(preset_ids)))
    ).scalars().all() if preset_ids else []
    by_id = {preset.id: preset for preset in presets}
    grouped: Dict[int, List[RulePreset]] = {group_id: [] for group_id in group_ids}
    for item in item_rows:
        preset = by_id.get(item.preset_id)
        if preset is not None:
            grouped[item.group_id].append(preset)
    return grouped


# ----------------------------------------------------
# Endpoints
# ----------------------------------------------------

@router.post("/auth/login", response_model=LoginResponse)
async def login_user(req: LoginRequest):
    async with async_session_maker() as session:
        uname = req.username.strip()
        
        # Prefer stable identifiers. A display name is accepted only when unique.
        stmt = select(TelegramUser).where(
            (func.lower(TelegramUser.username) == uname.lower()) |
            (TelegramUser.telegram_id == uname)
        )
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if not user and uname:
            display_name_result = await session.execute(
                select(TelegramUser)
                .where(func.lower(TelegramUser.full_name) == uname.lower())
                .limit(2)
            )
            display_name_matches = display_name_result.scalars().all()
            if len(display_name_matches) == 1:
                user = display_name_matches[0]

        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")

        if not user.is_approved:
            raise HTTPException(status_code=403, detail="Ваш аккаунт ожидает одобрения администратора.")

        credentials_changed = False
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(req.password)
            credentials_changed = True

        if not user.auth_token:
            user.auth_token = str(uuid.uuid4())
            credentials_changed = True

        if credentials_changed:
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
    new_pw = req.new_password
    if len(new_pw) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 8 символов")

    async with async_session_maker() as session:
        res = await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))
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
        return [_preset_response(preset) for preset in presets]


@router.post("/presets", response_model=RulePresetItem)
async def create_preset(payload: CreatePresetRequest, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        condition_payloads = _validated_condition_payloads(payload.conditions)
        conds_json = json.dumps(condition_payloads)
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
            conditions=[ConditionItem(**condition) for condition in condition_payloads],
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
        condition_payloads = _validated_condition_payloads(payload.conditions)
        stmt = select(RulePreset).where(RulePreset.id == preset_id)
        if user.role != "admin":
            stmt = stmt.where(RulePreset.owner_id == user.telegram_id)
        res = await session.execute(stmt)
        preset = res.scalar_one_or_none()
        if not preset:
            raise HTTPException(status_code=404, detail="Пресет не найден")
        
        preset.name = payload.name.strip() or preset.name
        preset.action = payload.action or "turn_off"
        preset.conditions = json.dumps(condition_payloads)
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
            conditions=[ConditionItem(**condition) for condition in condition_payloads],
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

        await session.execute(delete(RuleGroupItem).where(RuleGroupItem.preset_id == preset_id))
        await session.execute(delete(RulePreset).where(RulePreset.id == preset_id))
        await session.commit()
        return {"success": True, "message": "Пресет удален"}


@router.get("/rule-groups", response_model=List[RuleGroupResponse])
async def list_rule_groups(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        groups = (
            await session.execute(
                select(RuleGroup)
                .where(RuleGroup.owner_id == user.telegram_id)
                .order_by(RuleGroup.id.desc())
            )
        ).scalars().all()
        presets_by_group = await _load_group_presets(session, [group.id for group in groups])
        return [
            _rule_group_response(group, presets_by_group.get(group.id, []))
            for group in groups
        ]


@router.post("/rule-groups", response_model=RuleGroupResponse)
async def create_rule_group(
    payload: RuleGroupWriteRequest,
    user: TelegramUser = Depends(get_current_user),
):
    async with async_session_maker() as session:
        presets = await _get_owned_presets(session, user.telegram_id, payload.preset_ids)
        group = RuleGroup(
            owner_id=user.telegram_id,
            name=_clean_rule_group_name(payload.name),
            description=payload.description.strip(),
        )
        session.add(group)
        await session.flush()
        session.add_all(
            RuleGroupItem(group_id=group.id, preset_id=preset.id, position=position)
            for position, preset in enumerate(presets)
        )
        await session.commit()
        await session.refresh(group)
        return _rule_group_response(group, presets)


@router.put("/rule-groups/{group_id}", response_model=RuleGroupResponse)
async def update_rule_group(
    group_id: int,
    payload: RuleGroupWriteRequest,
    user: TelegramUser = Depends(get_current_user),
):
    async with async_session_maker() as session:
        group = (
            await session.execute(
                select(RuleGroup).where(
                    RuleGroup.id == group_id,
                    RuleGroup.owner_id == user.telegram_id,
                )
            )
        ).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="Группа правил не найдена.")

        presets = await _get_owned_presets(session, user.telegram_id, payload.preset_ids)
        group.name = _clean_rule_group_name(payload.name)
        group.description = payload.description.strip()
        await session.execute(delete(RuleGroupItem).where(RuleGroupItem.group_id == group.id))
        session.add_all(
            RuleGroupItem(group_id=group.id, preset_id=preset.id, position=position)
            for position, preset in enumerate(presets)
        )
        await session.commit()
        await session.refresh(group)
        return _rule_group_response(group, presets)


@router.delete("/rule-groups/{group_id}")
async def delete_rule_group(
    group_id: int,
    user: TelegramUser = Depends(get_current_user),
):
    async with async_session_maker() as session:
        group = (
            await session.execute(
                select(RuleGroup).where(
                    RuleGroup.id == group_id,
                    RuleGroup.owner_id == user.telegram_id,
                )
            )
        ).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="Группа правил не найдена.")
        await session.execute(delete(RuleGroupItem).where(RuleGroupItem.group_id == group.id))
        await session.delete(group)
        await session.commit()
        return {"success": True, "message": "Группа удалена. Назначенные правила сохранены в кабинетах."}


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
            p_stmt = select(RulePreset).where(
                RulePreset.id == payload.preset_id,
                RulePreset.owner_id == acc.owner_id,
            )
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


@router.post("/accounts/{account_id}/assign-rule-group/{group_id}")
async def assign_rule_group_to_account(
    account_id: str,
    group_id: int,
    user: TelegramUser = Depends(get_current_user),
):
    """Atomically attach every rule in a reusable group, skipping duplicates."""

    async with async_session_maker() as session:
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        account_stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            account_stmt = account_stmt.where(Account.owner_id == user.telegram_id)
        account = (await session.execute(account_stmt)).scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        group = (
            await session.execute(
                select(RuleGroup).where(
                    RuleGroup.id == group_id,
                    RuleGroup.owner_id == account.owner_id,
                )
            )
        ).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="Группа правил не найдена.")

        group_items = (
            await session.execute(
                select(RuleGroupItem)
                .where(RuleGroupItem.group_id == group.id)
                .order_by(RuleGroupItem.position, RuleGroupItem.id)
            )
        ).scalars().all()
        if not group_items:
            raise HTTPException(status_code=400, detail="В группе нет правил.")

        presets = await _get_owned_presets(
            session,
            account.owner_id,
            [item.preset_id for item in group_items],
        )
        active_rules = _load_active_rules(account.active_rules)
        attached_ids = {rule.get("preset_id") for rule in active_rules}
        added_presets = [preset for preset in presets if preset.id not in attached_ids]
        active_rules.extend(_preset_snapshot(preset) for preset in added_presets)
        account.active_rules = json.dumps(active_rules)
        account.rules_enabled = bool(active_rules)
        await session.commit()

        skipped_count = len(presets) - len(added_presets)
        message = (
            f"Группа '{group.name}' назначена: добавлено правил — {len(added_presets)}"
            if added_presets
            else f"Все правила группы '{group.name}' уже назначены кабинету"
        )
        return {
            "account_id": account.account_id,
            "group_id": group.id,
            "group_name": group.name,
            "added_count": len(added_presets),
            "skipped_count": skipped_count,
            "active_rules": active_rules,
            "rules_enabled": account.rules_enabled,
            "message": message,
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
                        # Account import never enables automation. Rules are
                        # assigned explicitly after the account is connected.
                        rules_enabled=False,
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
            return _summary_with_cache_metadata(
                cached_data,
                is_cached=True,
                age_seconds=now_ts - cached_ts,
            )

    async with async_session_maker() as session:
        accounts = await get_user_accounts(session, user)
        if not accounts:
            empty_res = {
                "period": period,
                "generated_at": _utc_iso(datetime.now(timezone.utc)),
                "source": "Meta Marketing API",
                "total_spend": 0.0,
                "total_clicks": 0,
                "total_impressions": 0,
                "total_leads": 0,
                "total_regs": 0,
                "total_purchases": 0,
                "avg_cpc": 0.0,
                "avg_ctr": 0.0,
                "cost_per_lead": None,
                "cost_per_registration": None,
                "cost_per_purchase": None,
                "accounts_count": 0,
                "accounts": [],
                "data_quality": {
                    "status": "unavailable",
                    "accounts_total": 0,
                    "accounts_synced": 0,
                    "accounts_failed": 0,
                    "accounts_blocked": 0,
                    "metrics_coverage_percent": 0.0,
                },
                "metric_definitions": SUMMARY_METRIC_DEFINITIONS,
            }
            _summary_cache[cache_key] = (now_ts, empty_res)
            return _summary_with_cache_metadata(empty_res, is_cached=False)

        total_spend = 0.0
        total_clicks = 0
        total_impressions = 0
        total_leads = 0
        total_regs = 0
        total_purchases = 0
        accounts_synced = 0
        accounts_failed = 0
        accounts_blocked = 0

        account_results = []

        for acc in accounts:
            short_name = get_short_account_label(acc.name, acc.account_id)
            if not acc.is_active or acc.account_status in [2, 101]:
                accounts_blocked += 1
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
                    "cost_per_lead": None,
                    "cost_per_registration": None,
                    "cost_per_purchase": None,
                    "cpc": 0.0,
                    "ctr": 0.0,
                    "adsets": [],
                    "has_error": False,
                    "is_banned": True,
                    "data_status": "blocked",
                    "data_status_label": "Meta не отдаёт метрики: кабинет заблокирован",
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
                acc_cpc = (acc_spend / acc_clicks) if acc_clicks > 0 else 0.0
                acc_ctr = ((acc_clicks / acc_impressions) * 100) if acc_impressions > 0 else 0.0

                total_spend += acc_spend
                total_clicks += acc_clicks
                total_impressions += acc_impressions
                total_leads += acc_leads
                total_regs += acc_regs
                total_purchases += acc_purchases
                accounts_synced += 1

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
                    "cost_per_lead": _cost_or_none(acc_spend, acc_leads),
                    "cost_per_registration": _cost_or_none(acc_spend, acc_regs),
                    "cost_per_purchase": _cost_or_none(acc_spend, acc_purchases),
                    "cpc": round(acc_cpc, 2),
                    "ctr": round(acc_ctr, 2),
                    "adsets": adsets,
                    "has_error": False,
                    "is_banned": False,
                    "data_status": "synced",
                    "data_status_label": "Метрики получены из Meta",
                })
            except Exception as e:
                logger.error(f"Error fetching insights for {acc.account_id}: {e}")
                accounts_failed += 1
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
                    "cost_per_lead": None,
                    "cost_per_registration": None,
                    "cost_per_purchase": None,
                    "cpc": 0.0,
                    "ctr": 0.0,
                    "adsets": [],
                    "has_error": True,
                    "is_banned": False,
                    "data_status": "error",
                    "data_status_label": "Meta не вернула метрики",
                })

        avg_cpc = (total_spend / total_clicks) if total_clicks > 0 else 0.0
        avg_ctr = ((total_clicks / total_impressions) * 100) if total_impressions > 0 else 0.0
        metrics_coverage = round((accounts_synced / len(accounts)) * 100, 1) if accounts else 0.0
        quality_status = "complete" if accounts_synced == len(accounts) else ("partial" if accounts_synced else "unavailable")

        res_data = {
            "period": period,
            "generated_at": _utc_iso(datetime.now(timezone.utc)),
            "source": "Meta Marketing API",
            "total_spend": round(total_spend, 2),
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_leads": total_leads,
            "total_regs": total_regs,
            "total_purchases": total_purchases,
            "avg_cpc": round(avg_cpc, 2),
            "avg_ctr": round(avg_ctr, 2),
            "cost_per_lead": _cost_or_none(total_spend, total_leads),
            "cost_per_registration": _cost_or_none(total_spend, total_regs),
            "cost_per_purchase": _cost_or_none(total_spend, total_purchases),
            "accounts_count": len(accounts),
            "accounts": account_results,
            "data_quality": {
                "status": quality_status,
                "accounts_total": len(accounts),
                "accounts_synced": accounts_synced,
                "accounts_failed": accounts_failed,
                "accounts_blocked": accounts_blocked,
                "metrics_coverage_percent": metrics_coverage,
            },
            "metric_definitions": SUMMARY_METRIC_DEFINITIONS,
        }
        _summary_cache[cache_key] = (now_ts, res_data)
        return _summary_with_cache_metadata(res_data, is_cached=False)



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


@router.get("/audit-events")
async def list_audit_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    category: Optional[str] = Query(None, max_length=40),
    event_status: Optional[str] = Query(None, alias="status", max_length=20),
    account_id: Optional[str] = Query(None, max_length=80),
    search: Optional[str] = Query(None, max_length=100),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    user: TelegramUser = Depends(get_current_user),
):
    """Return an owner-isolated, filterable audit history for the web UI."""

    filters = []
    if user.role != "admin":
        filters.append(AuditEvent.owner_id == user.telegram_id)
    if category:
        filters.append(AuditEvent.category == category.upper())
    if account_id:
        filters.append(AuditEvent.account_id == account_id)
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
        filters.append(AuditEvent.status == event_status.upper())

    async with async_session_maker() as session:
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

        rows = (
            await session.execute(
                select(AuditEvent)
                .where(*filters)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()

    items = [
        {
            "id": row.id,
            "owner_id": row.owner_id if user.role == "admin" else None,
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
            "duration_ms": row.duration_ms,
            "created_at": _utc_iso(row.created_at),
        }
        for row in rows
    ]

    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "status_counts": {status_name: count for status_name, count in status_rows},
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

        action_started = time.perf_counter()
        try:
            await meta_client.set_adset_status(adset_id=adset_id, access_token=account.access_token, status="ACTIVE")
        except Exception as e:
            logger.error("Error reactivating adset %s; details stored in audit history", adset_id)
            session.add(
                build_audit_event(
                    account=account,
                    event_type="MANUAL_REACTIVATE",
                    status="ERROR",
                    correlation_id=uuid.uuid4().hex,
                    category="MANUAL_ACTION",
                    action="REACTIVATE_ADSET",
                    message=str(e),
                    before_state={"status": "PAUSED", "is_resolved": False},
                    after_state={"status": "PAUSED", "is_resolved": False},
                    duration_ms=(time.perf_counter() - action_started) * 1000,
                    actor_type="user",
                    actor_id=user.telegram_id,
                    adset_id=adset_id,
                    adset_name=stopped_entry.adset_name,
                )
            )
            try:
                await session.commit()
            except Exception as audit_error:
                await session.rollback()
                logger.error("Failed to persist manual reactivation error: %s", audit_error)
            raise HTTPException(status_code=500, detail="Meta не смогла включить ad set. Подробности сохранены в логах.")

        stopped_entry.is_resolved = True
        session.add(
            build_audit_event(
                account=account,
                event_type="MANUAL_REACTIVATE",
                status="SUCCESS",
                correlation_id=uuid.uuid4().hex,
                category="MANUAL_ACTION",
                action="REACTIVATE_ADSET",
                message="Ad set вручную включён пользователем.",
                before_state={"status": "PAUSED", "is_resolved": False},
                after_state={"status": "ACTIVE", "is_resolved": True},
                duration_ms=(time.perf_counter() - action_started) * 1000,
                actor_type="user",
                actor_id=user.telegram_id,
                adset_id=adset_id,
                adset_name=stopped_entry.adset_name,
            )
        )
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("Meta activated adset %s but local state commit failed: %s", adset_id, e)
            raise HTTPException(
                status_code=500,
                detail="Meta включила ad set, но Buyerly не смог сохранить локальный статус. Обновите страницу.",
            )
        return {"success": True, "message": f"Адсет {adset_id} успешно включен!"}


@router.post("/adsets/{adset_id}/dismiss")
async def dismiss_adset(adset_id: str, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        stopped_res = await session.execute(select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id))
        stopped_entry = stopped_res.scalar_one_or_none()
        if not stopped_entry:
            raise HTTPException(status_code=404, detail="Запись не найдена.")

        account_res = await session.execute(
            select(Account).where(Account.account_id == stopped_entry.account_id)
        )
        account = account_res.scalar_one_or_none()
        if not account or (user.role != "admin" and account.owner_id != user.telegram_id):
            raise HTTPException(status_code=403, detail="Доступ запрещен.")

        stopped_entry.is_resolved = True
        session.add(
            build_audit_event(
                account=account,
                event_type="DISMISS_STOPPED",
                status="SUCCESS",
                correlation_id=uuid.uuid4().hex,
                category="MANUAL_ACTION",
                action="KEEP_PAUSED",
                message="Остановленный ad set оставлен выключенным пользователем.",
                before_state={"status": "PAUSED", "is_resolved": False},
                after_state={"status": "PAUSED", "is_resolved": True},
                actor_type="user",
                actor_id=user.telegram_id,
                adset_id=adset_id,
                adset_name=stopped_entry.adset_name,
            )
        )
        await session.commit()
        return {"success": True, "message": "Оставлен выключенным."}
