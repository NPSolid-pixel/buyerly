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
from bot.handlers import parse_fb_raw_accounts, scheduler_ref, get_short_account_label
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

class ConditionItem(BaseModel):
    metric: str = "spend"  # spend, cpl, cpr
    operator: str = "gt"   # gt, lt, eq
    value: float = 0.0

class RulePresetItem(BaseModel):
    id: int
    name: str
    action: str
    conditions: List[ConditionItem]
    created_at: str

class CreatePresetRequest(BaseModel):
    name: str
    action: Optional[str] = "turn_off"
    conditions: List[ConditionItem] = Field(default_factory=list)

class ApplyPresetRequest(BaseModel):
    preset_id: Optional[int] = None
    name: Optional[str] = ""
    action: Optional[str] = "turn_off"
    conditions: List[ConditionItem] = Field(default_factory=list)

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
    preset_id: Optional[int] = None
    preset_name: Optional[str] = ""
    rule_action: Optional[str] = "turn_off"
    rule_conditions: Optional[List[ConditionItem]] = Field(default_factory=list)
    max_spend_0_leads: float
    max_spend_1_lead: float
    max_cpa_multiple_leads: float
    conversion_event: str
    auto_reactivate: bool
    created_at: str

class UpdateLimitsRequest(BaseModel):
    max_spend_0_leads: float = Field(ge=0.0)
    max_spend_1_lead: float = Field(ge=0.0)
    max_cpa_multiple_leads: float = Field(ge=0.0)
    conversion_event: Optional[str] = "all"
    auto_reactivate: Optional[bool] = False

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
    max_spend_0_leads: Optional[float] = 2.0
    max_spend_1_lead: Optional[float] = 6.0
    max_cpa_multiple_leads: Optional[float] = 6.0

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


# ----------------------------------------------------
# Endpoints
# ----------------------------------------------------

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
        res_list = []
        for a in accounts:
            conds = []
            if a.rule_conditions:
                try:
                    parsed = json.loads(a.rule_conditions) if isinstance(a.rule_conditions, str) else a.rule_conditions
                    conds = [ConditionItem(**c) for c in parsed if isinstance(c, dict)]
                except Exception:
                    conds = []

            res_list.append(AccountItem(
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
                preset_id=a.preset_id,
                preset_name=a.preset_name or "",
                rule_action=a.rule_action or "turn_off",
                rule_conditions=conds,
                max_spend_0_leads=a.max_spend_0_leads,
                max_spend_1_lead=a.max_spend_1_lead,
                max_cpa_multiple_leads=a.max_cpa_multiple_leads,
                conversion_event=a.conversion_event or "all",
                auto_reactivate=a.auto_reactivate,
                created_at=a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else ""
            ))
        return res_list


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
            conditions=conds_json
        )
        session.add(preset)
        await session.commit()
        await session.refresh(preset)
        return RulePresetItem(
            id=preset.id,
            name=preset.name,
            action=preset.action,
            conditions=payload.conditions,
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
        await session.commit()
        await session.refresh(preset)
        return RulePresetItem(
            id=preset.id,
            name=preset.name,
            action=preset.action,
            conditions=payload.conditions,
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
        
        await session.execute(delete(RulePreset).where(RulePreset.id == preset_id))
        await session.commit()
        return {"success": True, "message": "Пресет удален"}


@router.post("/accounts/{account_id}/apply-preset")
async def apply_preset_to_account(
    account_id: str,
    payload: ApplyPresetRequest,
    user: TelegramUser = Depends(get_current_user)
):
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
            if user.role != "admin":
                p_stmt = p_stmt.where(RulePreset.owner_id == user.telegram_id)
            p_res = await session.execute(p_stmt)
            preset = p_res.scalar_one_or_none()
            if preset:
                acc.preset_id = preset.id
                acc.preset_name = preset.name
                acc.rule_action = preset.action
                acc.rule_conditions = preset.conditions
        else:
            # Custom rule conditions from payload
            conds_json = json.dumps([c.model_dump() for c in payload.conditions])
            preset_name = payload.name.strip() or "Мое правило"
            preset = RulePreset(
                owner_id=user.telegram_id,
                name=preset_name,
                action=payload.action or "turn_off",
                conditions=conds_json
            )
            session.add(preset)
            await session.flush()
            
            acc.preset_id = preset.id
            acc.preset_name = preset.name
            acc.rule_action = preset.action
            acc.rule_conditions = conds_json

        acc.rules_enabled = True
        await session.commit()
        return {
            "account_id": acc.account_id,
            "preset_id": acc.preset_id,
            "preset_name": acc.preset_name,
            "rule_action": acc.rule_action,
            "rule_conditions": acc.rule_conditions,
            "rules_enabled": acc.rules_enabled,
            "message": f"Правило '{acc.preset_name}' успешно применено к кабинету"
        }


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

        acc.rules_enabled = not acc.rules_enabled
        await session.commit()
        return {
            "account_id": acc.account_id,
            "rules_enabled": acc.rules_enabled,
            "message": f"Авто-правила {'включены' if acc.rules_enabled else 'выключены'}"
        }


@router.post("/accounts/{account_id}/limits")
async def update_account_limits(
    account_id: str, 
    payload: UpdateLimitsRequest,
    user: TelegramUser = Depends(get_current_user)
):
    async with async_session_maker() as session:
        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(Account.owner_id == user.telegram_id)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        acc.max_spend_0_leads = payload.max_spend_0_leads
        acc.max_spend_1_lead = payload.max_spend_1_lead
        acc.max_cpa_multiple_leads = payload.max_cpa_multiple_leads
        if payload.conversion_event:
            acc.conversion_event = payload.conversion_event
        if payload.auto_reactivate is not None:
            acc.auto_reactivate = payload.auto_reactivate

        await session.commit()
        return {
            "account_id": acc.account_id,
            "name": acc.name,
            "max_spend_0_leads": acc.max_spend_0_leads,
            "max_spend_1_lead": acc.max_spend_1_lead,
            "max_cpa_multiple_leads": acc.max_cpa_multiple_leads,
            "conversion_event": acc.conversion_event,
            "auto_reactivate": acc.auto_reactivate,
            "message": "Лимиты успешно обновлены"
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
                        max_spend_0_leads=payload.max_spend_0_leads or 2.0,
                        max_spend_1_lead=payload.max_spend_1_lead or 6.0,
                        max_cpa_multiple_leads=payload.max_cpa_multiple_leads or 6.0,
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

    if scheduler_ref:
        scheduler_ref.reschedule_job(
            "monitoring_job",
            trigger="interval",
            minutes=payload.minutes
        )
        logger.info(f"Rescheduled monitoring job to {payload.minutes} minutes via Web App.")

    return {
        "success": True,
        "poll_interval_minutes": payload.minutes,
        "message": f"Интервал опроса изменен на {payload.minutes} минут"
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
