import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Literal
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body, UploadFile, File
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from sqlalchemy import select, delete, func, or_, and_, update, case

from core.audit import build_audit_event
from core.action_undo import (
    MUTATING_EVENT_TYPES,
    REVERSIBLE_EVENT_TYPES,
    UndoError,
    event_is_within_undo_window,
    undo_audit_action,
)
from core.config import settings
from core.currency import UNKNOWN_CURRENCY, normalize_currency
from core.metrics import (
    SUMMARY_METRIC_DEFINITIONS,
    cost_per_event,
    normalize_rule_conditions,
    normalize_runtime_rule,
    validate_public_rule_conditions,
    validate_runtime_rule,
    validate_rule_set_compatibility,
)
from core.meta_tokens import resolve_account_access_token
from core.ownership import assign_owner, entity_is_owned_by, owned_by, owned_by_ids
from core.rule_examples import ensure_rule_examples
from core.timezones import resolve_account_clock
from database.db import async_session_maker, hash_password, password_needs_rehash, verify_password
import json
from database.models import (
    Account,
    AuditEvent,
    StoppedAdSet,
    AppSettings,
    TelegramUser,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceInvite,
    EventLog,
    RulePreset,
    RuleGroup,
    RuleGroupItem,
    AccountGroup,
    AccountGroupMember,
    SummarySnapshot,
    AnalyticsViewPreference,
    AutomationScheduleState,
    RuleExecutionState,
    ActionUndoState,
    AutomationRuntimeState,
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
SUMMARY_SNAPSHOT_RETENTION = 100
SUMMARY_VIEW_SCOPE = "summary"
SUMMARY_TABLE_COLUMNS = (
    "account",
    "custom_name",
    "note",
    "data",
    "spend",
    "impressions",
    "reach",
    "frequency",
    "cpm",
    "clicks",
    "link_clicks",
    "unique_clicks",
    "outbound_clicks",
    "landing_page_views",
    "ctr",
    "ctr_link",
    "cpc",
    "cpc_link",
    "leads",
    "registrations",
    "purchases",
    "cpl",
    "cpreg",
    "cpp",
)
SUMMARY_REQUIRED_COLUMNS = ("account", "data")
SUMMARY_VIEW_MODES = {"all", "overview", "delivery", "traffic", "funnel", "custom"}
SUMMARY_VIEW_PERIODS = {"today", "yesterday", "last_3d", "last_7d"}
SUMMARY_FILTER_KEYS = {"query", "status", "group_id"}
SUMMARY_FILTER_STATUSES = {"all", "synced", "blocked", "error"}
SUMMARY_COLUMN_MIN_WIDTH = 72
SUMMARY_COLUMN_MAX_WIDTH = 420
SUMMARY_DEFAULT_COLUMN_WIDTHS = {
    "account": 260,
    "custom_name": 180,
    "note": 280,
    "data": 120,
    "spend": 112,
    "impressions": 104,
    "reach": 104,
    "frequency": 96,
    "cpm": 96,
    "clicks": 104,
    "link_clicks": 104,
    "unique_clicks": 104,
    "outbound_clicks": 112,
    "landing_page_views": 120,
    "ctr": 96,
    "ctr_link": 96,
    "cpc": 96,
    "cpc_link": 96,
    "leads": 88,
    "registrations": 96,
    "purchases": 96,
    "cpl": 96,
    "cpreg": 96,
    "cpp": 96,
}

# ----------------------------------------------------
# Pydantic Schemas
# ----------------------------------------------------
class WorkspaceItem(BaseModel):
    id: int
    name: str
    slug: str
    badge_text: str
    badge_color: str
    logo_url: str = ""
    role: str
    is_active: bool
    accounts_count: int = 0
    members_count: int = 1

class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    slug: Optional[str] = Field(None, max_length=60)
    badge_color: Optional[str] = Field("#F5A300", max_length=30)
    badge_text: Optional[str] = Field(None, max_length=5)
    logo_url: Optional[str] = Field(None, max_length=500)

class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    badge_color: Optional[str] = Field(None, max_length=30)
    badge_text: Optional[str] = Field(None, max_length=5)
    logo_url: Optional[str] = Field(None, max_length=500)

class SwitchWorkspaceRequest(BaseModel):
    workspace_id: Optional[int] = None
    slug: Optional[str] = None

class WorkspaceMemberItem(BaseModel):
    id: int
    user_id: int
    username: str
    full_name: str
    first_name: str = ""
    last_name: str = ""
    email: Optional[str] = None
    avatar_url: str = ""
    telegram_id: Optional[str] = None
    role: str
    joined_at: str
    is_current_user: bool = False

class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|buyer|viewer)$")

class TransferOwnershipRequest(BaseModel):
    new_owner_user_id: int

class CreateWorkspaceInviteRequest(BaseModel):
    email: Optional[str] = Field(None, max_length=255)
    role: str = Field(default="buyer", pattern="^(admin|buyer|viewer)$")
    expires_in_days: int = Field(default=7, ge=1, le=365)
    max_uses: int = Field(default=1, ge=0, le=1000)

class WorkspaceInviteItem(BaseModel):
    id: int
    workspace_id: int
    workspace_name: str
    token: str
    invite_url: str
    email: Optional[str] = None
    role: str
    status: str
    max_uses: int
    used_count: int
    inviter_name: str
    expires_at: Optional[str] = None
    created_at: str

class PublicInviteInfoResponse(BaseModel):
    valid: bool
    status: str
    workspace_name: Optional[str] = None
    workspace_slug: Optional[str] = None
    workspace_badge_text: Optional[str] = None
    workspace_badge_color: Optional[str] = None
    inviter_name: Optional[str] = None
    role: Optional[str] = None
    target_email: Optional[str] = None
    expires_at: Optional[str] = None
    message: Optional[str] = None

class UserProfileResponse(BaseModel):
    telegram_id: Optional[str] = None
    username: str
    full_name: str
    first_name: str = ""
    last_name: str = ""
    email: Optional[str] = None
    avatar_url: str = ""
    role: str
    is_approved: bool
    onboarding_step: str = "completed"
    onboarding_completed: bool = False
    active_workspace: Optional[WorkspaceItem] = None
    workspaces: List[WorkspaceItem] = Field(default_factory=list)

# ----------------------------------------------------
# Onboarding Schemas
# ----------------------------------------------------
class OnboardingStatusResponse(BaseModel):
    onboarding_step: str
    onboarding_completed: bool
    user: UserProfileResponse
    active_workspace: Optional[WorkspaceItem] = None

class PersonalDetailsRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=60)
    last_name: str = Field(..., min_length=1, max_length=60)
    email: Optional[str] = Field(None, max_length=255)

class CheckSlugResponse(BaseModel):
    slug: str
    available: bool
    message: str

class OnboardingWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    slug: Optional[str] = Field(None, max_length=60)
    badge_color: Optional[str] = Field("#F5A300", max_length=30)
    badge_text: Optional[str] = Field(None, max_length=5)
    logo_url: Optional[str] = Field(None, max_length=500)

class BulkInviteItem(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: str = Field(default="buyer", pattern="^(admin|buyer|viewer)$")

class OnboardingBulkInvitesRequest(BaseModel):
    invites: List[BulkInviteItem] = Field(default_factory=list)

class OnboardingBulkInvitesResponse(BaseModel):
    sent_count: int
    invites: List[WorkspaceInviteItem]
    onboarding_completed: bool
    redirect_url: str = "/"

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
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    telegram_id: Optional[str] = None


class ConditionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["spend", "cpl", "cpreg", "cpp", "leads", "registrations", "purchases", "ctr", "cpc"] = "spend"
    operator: Literal["gte", "gt", "lte", "lt", "eq"] = "gte"
    value: float = Field(default=0.0, ge=0, le=1_000_000_000, allow_inf_nan=False)
    time_window: Literal["today", "yesterday", "last_3d", "last_7d"] = "today"

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
    currency_mode: Literal["account"] = "account"
    created_at: str

class CreatePresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    action: Literal["turn_off", "notify_only", "turn_on", "increase_budget", "decrease_budget"] = "turn_off"
    conditions: List[ConditionItem] = Field(min_length=1, max_length=20)
    condition_logic: Literal["and", "or"] = "and"
    cooldown_minutes: int = Field(default=0, ge=0, le=10_080)
    check_interval_minutes: int = Field(default=5, ge=1, le=1_440)
    notify_tg: bool = True
    budget_change_percent: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)
    budget_max_daily: float = Field(default=0.0, ge=0, le=10_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_safe_action_parameters(self):
        validate_runtime_rule(
            {
                "action": self.action,
                "conditions": [condition.model_dump() for condition in self.conditions],
                "logic": self.condition_logic,
                "cooldown_minutes": self.cooldown_minutes,
                "check_interval": self.check_interval_minutes,
                "budget_change_percent": self.budget_change_percent,
                "budget_max_daily": self.budget_max_daily,
            }
        )
        return self

class RuleGroupWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    position: Optional[int] = None
    preset_ids: List[int] = Field(default_factory=list, min_length=0, max_length=50)

class RuleGroupResponse(BaseModel):
    id: int
    name: str
    description: str
    position: int = 0
    preset_ids: List[int]
    rules: List[RulePresetItem]
    created_at: str

class RuleGroupsReorderRequest(BaseModel):
    group_ids: List[int] = Field(min_length=0, max_length=100)

class ApplyPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: int = Field(gt=0)


class AccountLatestMetrics(BaseModel):
    period: Literal["today"] = "today"
    generated_at: str = ""
    saved_at: str = ""
    data_status: Literal["synced", "blocked", "error"]
    data_status_label: str = ""
    spend: Optional[float] = None
    impressions: int = 0
    reach: int = 0
    frequency: Optional[float] = None
    cpm: Optional[float] = None
    clicks: int = 0
    unique_clicks: int = 0
    link_clicks: int = 0
    outbound_clicks: int = 0
    landing_page_views: int = 0
    leads: int = 0
    registrations: int = 0
    purchases: int = 0
    cpl: Optional[float] = None
    cpreg: Optional[float] = None
    cpp: Optional[float] = None
    cpc: Optional[float] = None
    ctr: Optional[float] = None
    ctr_link: Optional[float] = None
    cpc_link: Optional[float] = None
    cost_per_lpv: Optional[float] = None
    roas: Optional[float] = None


class AccountItem(BaseModel):
    id: int
    account_id: str
    name: str
    custom_name: str
    note: str
    connection_type: Literal["facebook_login", "system_user"]
    owner_id: str
    batch_name: str
    timezone_name: str
    currency: str
    account_status: int
    status_label: str
    rules_enabled: bool
    is_active: bool
    active_rules: List[Dict[str, Any]] = Field(default_factory=list)
    group_ids: List[int] = Field(default_factory=list)
    latest_metrics: Optional[AccountLatestMetrics] = None
    created_at: str


class AccountProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    custom_name: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)


class AccountGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    account_ids: List[str] = Field(default_factory=list, max_length=250)


class AccountGroupItem(BaseModel):
    id: int
    name: str
    description: str
    account_ids: List[str] = Field(default_factory=list)
    accounts_count: int = 0
    created_at: str = ""
    updated_at: str = ""


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
    current_password: SecretStr


class AutomationSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr
    poll_interval_minutes: int = Field(ge=5, le=120)
    critical_rule_interval_minutes: int = Field(ge=1, le=15)
    stop_confirmation_minutes: int = Field(default=10, ge=0, le=60)
    inventory_cache_minutes: int = Field(ge=1, le=30)
    account_health_interval_minutes: int = Field(ge=5, le=120)
    max_concurrent_accounts: int = Field(ge=1, le=5)
    max_concurrent_actions: int = Field(ge=1, le=10)
    usage_soft_limit_percent: int = Field(ge=40, le=85)
    usage_hard_limit_percent: int = Field(ge=60, le=95)
    adaptive_polling_enabled: bool = True

    @model_validator(mode="after")
    def validate_usage_thresholds(self):
        if self.usage_soft_limit_percent >= self.usage_hard_limit_percent:
            raise ValueError("Мягкий порог квоты должен быть ниже жёсткого")
        return self


class AnalyticsViewPreferenceRequest(BaseModel):
    view_mode: str = Field(default="all", pattern="^(all|overview|delivery|traffic|funnel|custom)$")
    visible_columns: List[str] = Field(default_factory=lambda: list(SUMMARY_TABLE_COLUMNS), max_length=len(SUMMARY_TABLE_COLUMNS))
    column_order: List[str] = Field(default_factory=lambda: list(SUMMARY_TABLE_COLUMNS), max_length=len(SUMMARY_TABLE_COLUMNS))
    column_widths: Dict[str, int] = Field(default_factory=dict, max_length=len(SUMMARY_TABLE_COLUMNS))
    sort_column: str = Field(default="", max_length=64)
    sort_direction: str = Field(default="desc", pattern="^(asc|desc)$")
    filters: Dict[str, str] = Field(default_factory=dict, max_length=len(SUMMARY_FILTER_KEYS))
    period: str = Field(default="today", pattern="^(today|yesterday|last_3d|last_7d)$")


# ----------------------------------------------------
# Workspaces Helpers & Scoping
# ----------------------------------------------------
import re

def slugify(text: str) -> str:
    """Generate a clean URL slug from name."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    text = re.sub(r'^-+|-+$', '', text)
    return text or "workspace"


RESERVED_WORKSPACE_SLUGS = {
    "api",
    "admin",
    "app",
    "auth",
    "static",
    "uploads",
    "settings",
    "terms",
    "privacy",
    "data-deletion",
    "onboarding",
    "login",
    "sign-in",
    "dashboard",
    "accounts",
    "rules",
    "chats",
    "summary",
    "logs",
    "invite",
    "invites",
    "null",
    "undefined",
}


async def get_user_workspace(
    session,
    user: TelegramUser,
    workspace_id: Optional[int] = None,
    slug: Optional[str] = None
) -> Optional[Workspace]:
    """Resolve the active workspace for the user, verifying membership."""
    if slug:
        ws = (await session.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one_or_none()
        if ws:
            member = (await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == ws.id,
                    WorkspaceMember.user_id == user.id
                )
            )).scalar_one_or_none()
            if member or user.role == "admin":
                return ws

    if workspace_id:
        member = (await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id
            )
        )).scalar_one_or_none()
        if member or user.role == "admin":
            return (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()

    if user.active_workspace_id:
        ws = (await session.execute(select(Workspace).where(Workspace.id == user.active_workspace_id))).scalar_one_or_none()
        if ws:
            return ws

    # Fallback to first membership
    first_member = (await session.execute(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id).limit(1)
    )).scalar_one_or_none()
    if first_member:
        ws = (await session.execute(select(Workspace).where(Workspace.id == first_member.workspace_id))).scalar_one_or_none()
        if ws:
            user.active_workspace_id = ws.id
            await session.commit()
            return ws

    # Create default workspace if user has none
    ws_slug = "buyerly"
    existing = (await session.execute(select(Workspace).where(Workspace.slug == ws_slug))).scalar_one_or_none()
    if existing:
        ws_slug = f"buyerly-{user.id}"

    ws = Workspace(
        name="Buyerly",
        slug=ws_slug,
        badge_text="B",
        badge_color="#F5A300",
        logo_url="",
        owner_user_id=user.id
    )
    session.add(ws)
    await session.flush()

    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    session.add(member)
    user.active_workspace_id = ws.id
    await session.commit()
    return ws


async def get_user_workspaces_list(session, user: TelegramUser) -> List[WorkspaceItem]:
    """Return all workspaces accessible to the user with live stats."""
    active_ws = await get_user_workspace(session, user)
    active_id = active_ws.id if active_ws else None

    rows = (await session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.id.asc())
    )).all()

    items = []
    for ws, role in rows:
        acc_count = (await session.execute(
            select(func.count()).select_from(Account).where(Account.workspace_id == ws.id)
        )).scalar_one() or 0
        mem_count = (await session.execute(
            select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id)
        )).scalar_one() or 1

        items.append(WorkspaceItem(
            id=ws.id,
            name=ws.name,
            slug=ws.slug,
            badge_text=ws.badge_text or ws.name[:1].upper(),
            badge_color=ws.badge_color or "#F5A300",
            logo_url=ws.logo_url or "",
            role=role or "owner",
            is_active=(ws.id == active_id),
            accounts_count=int(acc_count),
            members_count=int(mem_count),
        ))
    return items


async def get_user_workspace_member(
    session,
    user: TelegramUser,
    workspace_id: Optional[int] = None,
) -> tuple[Optional[Workspace], Optional[WorkspaceMember]]:
    """Resolve active workspace and user membership with role."""
    ws = await get_user_workspace(session, user, workspace_id=workspace_id)
    if not ws:
        return None, None
    member = (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws.id,
                WorkspaceMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    return ws, member


def ensure_workspace_write_access(
    user: TelegramUser,
    member: Optional[WorkspaceMember],
    action_description: str = "изменения данных",
) -> None:
    """Ensure user has write access to workspace (owner, admin, buyer). Viewers are blocked."""
    if user.role == "admin":
        return
    if not member:
        raise HTTPException(status_code=403, detail=f"Нет доступа к воркспейсу для {action_description}")
    if member.role == "viewer":
        raise HTTPException(
            status_code=403,
            detail=f"Роль Наблюдатель (Viewer) имеет доступ только для чтения и не может выполнять {action_description}",
        )


# ----------------------------------------------------
# Helper to filter user accounts
# ----------------------------------------------------
async def get_user_accounts(session, user: TelegramUser, workspace_id: Optional[int] = None) -> List[Account]:
    if user.role == "admin" and not workspace_id:
        stmt = select(Account).order_by(Account.id.desc())
        res = await session.execute(stmt)
        return res.scalars().all()

    ws = await get_user_workspace(session, user, workspace_id=workspace_id)
    if ws:
        stmt = select(Account).where(
            or_(
                Account.workspace_id == ws.id,
                and_(Account.workspace_id.is_(None), owned_by(Account, user))
            )
        ).order_by(Account.id.desc())
    else:
        stmt = select(Account).where(owned_by(Account, user)).order_by(Account.id.desc())
    res = await session.execute(stmt)
    return res.scalars().all()


async def _account_group_ids_by_account(
    session,
    user: TelegramUser,
    workspace_id: Optional[int] = None,
) -> Dict[str, List[int]]:
    """Return live workspace/owner-scoped group membership keyed by Meta account ID."""
    ws = await get_user_workspace(session, user, workspace_id=workspace_id)
    scope_clause = (
        or_(AccountGroup.workspace_id == ws.id, and_(AccountGroup.workspace_id.is_(None), owned_by(AccountGroup, user)))
        if ws
        else owned_by(AccountGroup, user)
    )

    rows = (
        await session.execute(
            select(Account.account_id, AccountGroupMember.group_id)
            .join(AccountGroupMember, AccountGroupMember.account_id == Account.id)
            .join(AccountGroup, AccountGroup.id == AccountGroupMember.group_id)
            .where(scope_clause)
            .order_by(AccountGroupMember.position, AccountGroupMember.id)
        )
    ).all()
    result: Dict[str, List[int]] = {}
    for account_id, group_id in rows:
        result.setdefault(str(account_id), []).append(int(group_id))
    return result


async def _account_group_items(
    session,
    user: TelegramUser,
    workspace_id: Optional[int] = None,
) -> List[AccountGroupItem]:
    ws = await get_user_workspace(session, user, workspace_id=workspace_id)
    scope_clause = (
        or_(AccountGroup.workspace_id == ws.id, and_(AccountGroup.workspace_id.is_(None), owned_by(AccountGroup, user)))
        if ws
        else owned_by(AccountGroup, user)
    )

    groups = (
        await session.execute(
            select(AccountGroup)
            .where(scope_clause)
            .order_by(AccountGroup.name.asc(), AccountGroup.id.asc())
        )
    ).scalars().all()
    if not groups:
        return []

    group_ids = [group.id for group in groups]
    member_rows = (
        await session.execute(
            select(AccountGroupMember.group_id, Account.account_id)
            .join(Account, Account.id == AccountGroupMember.account_id)
            .where(AccountGroupMember.group_id.in_(group_ids))
            .order_by(AccountGroupMember.group_id, AccountGroupMember.position, AccountGroupMember.id)
        )
    ).all()
    members: Dict[int, List[str]] = {group_id: [] for group_id in group_ids}
    for group_id, account_id in member_rows:
        members.setdefault(int(group_id), []).append(str(account_id))

    return [
        AccountGroupItem(
            id=group.id,
            name=group.name,
            description=group.description or "",
            account_ids=members.get(group.id, []),
            accounts_count=len(members.get(group.id, [])),
            created_at=_utc_iso(group.created_at),
            updated_at=_utc_iso(group.updated_at),
        )
        for group in groups
    ]


async def _validate_account_group_members(
    session,
    user: TelegramUser,
    requested_account_ids: List[str],
) -> List[Account]:
    unique_ids = list(dict.fromkeys(str(value).strip() for value in requested_account_ids if str(value).strip()))
    visible_accounts = await get_user_accounts(session, user)
    visible_by_id = {account.account_id: account for account in visible_accounts}
    invalid = [account_id for account_id in unique_ids if account_id not in visible_by_id]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Кабинеты недоступны текущему пользователю: {', '.join(invalid[:5])}",
        )
    return [visible_by_id[account_id] for account_id in unique_ids]


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


def _currency_total_payload(currency: str, values: Dict[str, Any]) -> Dict[str, Any]:
    spend = float(values.get("spend", 0.0))
    impressions = int(values.get("impressions", 0))
    clicks = int(values.get("clicks", 0))
    link_clicks = int(values.get("link_clicks", 0))
    landing_page_views = int(values.get("landing_page_views", 0))
    leads = int(values.get("leads", 0))
    registrations = int(values.get("registrations", 0))
    purchases = int(values.get("purchases", 0))
    return {
        "currency": currency,
        "accounts_count": int(values.get("accounts_count", 0)),
        "spend": round(spend, 2),
        "impressions": impressions,
        "clicks": clicks,
        "link_clicks": link_clicks,
        "landing_page_views": landing_page_views,
        "leads": leads,
        "registrations": registrations,
        "purchases": purchases,
        "cpm": _cost_or_none(spend * 1000, impressions),
        "cpc": _cost_or_none(spend, clicks),
        "cpc_link": _cost_or_none(spend, link_clicks),
        "cost_per_landing_page_view": _cost_or_none(spend, landing_page_views),
        "cost_per_lead": _cost_or_none(spend, leads),
        "cost_per_registration": _cost_or_none(spend, registrations),
        "cost_per_purchase": _cost_or_none(spend, purchases),
    }


def _summary_with_cache_metadata(
    payload: Dict[str, Any],
    *,
    is_cached: bool,
    age_seconds: float = 0.0,
    origin: str = "live",
    persisted_at: str = "",
) -> Dict[str, Any]:
    return {
        **payload,
        "cache": {
            "is_cached": is_cached,
            "age_seconds": round(max(0.0, age_seconds), 1),
            "ttl_seconds": SUMMARY_CACHE_TTL,
            "origin": origin,
            "persisted_at": persisted_at,
        },
    }


def _summary_owner_key(user: TelegramUser) -> str:
    return f"user:{user.id}"


def _normalize_summary_view_config(config: Any, *, strict: bool = True) -> Dict[str, Any]:
    if not isinstance(config, dict):
        config = {}
    view_mode = str(config.get("view_mode") or "all")
    if view_mode not in SUMMARY_VIEW_MODES:
        view_mode = "all"

    requested = config.get("visible_columns")
    if not isinstance(requested, list):
        requested = list(SUMMARY_TABLE_COLUMNS)
    invalid = [key for key in requested if key not in SUMMARY_TABLE_COLUMNS]
    if invalid and strict:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестные колонки аналитики: {', '.join(map(str, invalid))}",
        )
    requested = [key for key in requested if key in SUMMARY_TABLE_COLUMNS]
    requested_set = set(requested) | set(SUMMARY_REQUIRED_COLUMNS)
    visible_columns = [key for key in SUMMARY_TABLE_COLUMNS if key in requested_set]

    requested_order = config.get("column_order")
    if not isinstance(requested_order, list):
        requested_order = list(SUMMARY_TABLE_COLUMNS)
    invalid_order = [key for key in requested_order if key not in SUMMARY_TABLE_COLUMNS]
    if invalid_order and strict:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестные колонки в порядке аналитики: {', '.join(map(str, invalid_order))}",
        )
    column_order = []
    for key in requested_order:
        if key in SUMMARY_TABLE_COLUMNS and key not in column_order:
            column_order.append(key)
    column_order.extend(key for key in SUMMARY_TABLE_COLUMNS if key not in column_order)

    requested_widths = config.get("column_widths")
    if not isinstance(requested_widths, dict):
        requested_widths = {}
    invalid_width_keys = [key for key in requested_widths if key not in SUMMARY_TABLE_COLUMNS]
    if invalid_width_keys and strict:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестные колонки в ширине аналитики: {', '.join(map(str, invalid_width_keys))}",
        )
    column_widths = {}
    for key in SUMMARY_TABLE_COLUMNS:
        default_width = SUMMARY_DEFAULT_COLUMN_WIDTHS[key]
        raw_width = requested_widths.get(key, default_width)
        try:
            width = int(raw_width)
        except (TypeError, ValueError):
            if strict:
                raise HTTPException(status_code=422, detail=f"Некорректная ширина колонки: {key}")
            width = default_width
        if strict and not SUMMARY_COLUMN_MIN_WIDTH <= width <= SUMMARY_COLUMN_MAX_WIDTH:
            raise HTTPException(
                status_code=422,
                detail=f"Ширина колонки {key} должна быть от {SUMMARY_COLUMN_MIN_WIDTH} до {SUMMARY_COLUMN_MAX_WIDTH}px",
            )
        column_widths[key] = max(SUMMARY_COLUMN_MIN_WIDTH, min(SUMMARY_COLUMN_MAX_WIDTH, width))

    sort_column = str(config.get("sort_column") or "")
    if sort_column and sort_column not in SUMMARY_TABLE_COLUMNS:
        if strict:
            raise HTTPException(status_code=422, detail=f"Неизвестная колонка сортировки: {sort_column}")
        sort_column = ""
    sort_direction = str(config.get("sort_direction") or "desc")
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "desc"

    raw_filters = config.get("filters")
    if not isinstance(raw_filters, dict):
        raw_filters = {}
    invalid_filter_keys = [key for key in raw_filters if key not in SUMMARY_FILTER_KEYS]
    if invalid_filter_keys and strict:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестные фильтры аналитики: {', '.join(map(str, invalid_filter_keys))}",
        )
    query_filter = str(raw_filters.get("query") or "").strip()
    if strict and len(query_filter) > 120:
        raise HTTPException(status_code=422, detail="Поиск по кабинетам не должен превышать 120 символов")
    query_filter = query_filter[:120]
    status_filter = str(raw_filters.get("status") or "all")
    if status_filter not in SUMMARY_FILTER_STATUSES:
        if strict:
            raise HTTPException(status_code=422, detail=f"Неизвестный фильтр статуса: {status_filter}")
        status_filter = "all"
    group_filter = str(raw_filters.get("group_id") or "all").strip()
    if group_filter != "all" and (not group_filter.isdigit() or int(group_filter) <= 0):
        if strict:
            raise HTTPException(status_code=422, detail="Фильтр группы должен быть 'all' или положительным ID")
        group_filter = "all"

    period = str(config.get("period") or "today")
    if period not in SUMMARY_VIEW_PERIODS:
        period = "today"
    return {
        "scope": SUMMARY_VIEW_SCOPE,
        "view_mode": view_mode,
        "visible_columns": visible_columns,
        "column_order": column_order,
        "column_widths": column_widths,
        "sort_column": sort_column,
        "sort_direction": sort_direction,
        "filters": {
            "query": query_filter,
            "status": status_filter,
            "group_id": group_filter,
        },
        "period": period,
    }


def _analytics_view_response(
    row: Optional[AnalyticsViewPreference],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        **config,
        "is_saved": row is not None,
        "updated_at": _utc_iso(row.updated_at) if row else "",
    }


def _summary_snapshot_reference(
    row: SummarySnapshot,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "id": row.id,
        "generated_at": payload.get("generated_at") or _utc_iso(row.generated_at),
        "saved_at": _utc_iso(row.created_at),
        "total_spend": payload.get("total_spend", 0.0),
        "display_currency": payload.get("display_currency", ""),
        "mixed_currencies": bool(payload.get("mixed_currencies", False)),
        "currency_totals": payload.get("currency_totals", []),
        "total_impressions": payload.get("total_impressions", 0),
        "total_reach": payload.get("total_reach", 0),
        "total_clicks": payload.get("total_clicks", 0),
        "total_link_clicks": payload.get("total_link_clicks", 0),
        "total_outbound_clicks": payload.get("total_outbound_clicks", 0),
        "total_landing_page_views": payload.get("total_landing_page_views", 0),
        "total_leads": payload.get("total_leads", 0),
        "total_regs": payload.get("total_regs", 0),
        "total_purchases": payload.get("total_purchases", 0),
    }


def _latest_account_metrics_by_id(summary: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Extract a compact, backwards-compatible account view from a saved summary."""

    if not isinstance(summary, dict):
        return {}
    generated_at = str(summary.get("generated_at") or "")
    snapshot = summary.get("snapshot") if isinstance(summary.get("snapshot"), dict) else {}
    saved_at = str(snapshot.get("saved_at") or "")
    rows = summary.get("accounts") if isinstance(summary.get("accounts"), list) else []
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_id = str(row.get("account_id") or "").strip()
        if not account_id:
            continue
        raw_status = str(row.get("data_status") or "synced")
        data_status = raw_status if raw_status in {"synced", "blocked", "error"} else "error"

        def safe_int(key: str) -> int:
            try:
                return max(0, int(float(row.get(key) or 0)))
            except (TypeError, ValueError):
                return 0

        def safe_float(key: str) -> Optional[float]:
            val = row.get(key)
            if val is None:
                return None
            try:
                return round(float(val), 2)
            except (TypeError, ValueError):
                return None

        spend: Optional[float]
        try:
            spend = round(float(row.get("spend")), 2) if row.get("spend") is not None else None
        except (TypeError, ValueError):
            spend = None
        result[account_id] = {
            "period": "today",
            "generated_at": generated_at,
            "saved_at": saved_at,
            "data_status": data_status,
            "data_status_label": str(row.get("data_status_label") or ""),
            "spend": spend,
            "impressions": safe_int("impressions"),
            "reach": safe_int("reach"),
            "frequency": safe_float("frequency"),
            "cpm": safe_float("cpm"),
            "clicks": safe_int("clicks"),
            "unique_clicks": safe_int("unique_clicks"),
            "link_clicks": safe_int("link_clicks"),
            "outbound_clicks": safe_int("outbound_clicks"),
            "landing_page_views": safe_int("landing_page_views"),
            "leads": safe_int("leads"),
            "registrations": safe_int("registrations"),
            "purchases": safe_int("purchases"),
            "cpl": safe_float("cost_per_lead"),
            "cpreg": safe_float("cost_per_registration"),
            "cpp": safe_float("cost_per_purchase"),
            "cpc": safe_float("cpc"),
            "ctr": safe_float("ctr"),
            "ctr_link": safe_float("ctr_link"),
            "cpc_link": safe_float("cpc_link"),
            "cost_per_lpv": safe_float("cost_per_landing_page_view"),
            "roas": safe_float("purchase_roas"),
        }
    return result


async def _enrich_summary_account_metadata(
    session,
    payload: Dict[str, Any],
    user: TelegramUser,
) -> Dict[str, Any]:
    """Overlay live Buyerly labels and groups on cached Meta metric rows."""

    accounts = await get_user_accounts(session, user)
    accounts_by_id = {account.account_id: account for account in accounts}
    group_ids_by_account = await _account_group_ids_by_account(session, user)
    rows = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
    enriched_rows = []
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        account_id = str(row.get("account_id") or "")
        account = accounts_by_id.get(account_id)
        if account is not None:
            row["name"] = account.name
            row["short_name"] = get_short_account_label(account.name, account.account_id)
            row["custom_name"] = account.custom_name or ""
            row["note"] = account.note or ""
        else:
            row.setdefault("custom_name", "")
            row.setdefault("note", "")
        row["group_ids"] = group_ids_by_account.get(account_id, [])
        enriched_rows.append(row)
    return {**payload, "accounts": enriched_rows}


async def _load_persisted_summary(
    session,
    *,
    owner_id: str,
    owner_user_id: int,
    period: str,
) -> Optional[Dict[str, Any]]:
    rows = (
        await session.execute(
            select(SummarySnapshot)
            .where(
                owned_by_ids(SummarySnapshot, owner_user_id, owner_id),
                SummarySnapshot.period == period,
            )
            .order_by(SummarySnapshot.created_at.desc(), SummarySnapshot.id.desc())
            .limit(5)
        )
    ).scalars().all()

    valid_rows = []
    for row in rows:
        payload = _load_json_object(row.payload)
        if payload:
            valid_rows.append((row, payload))
        if len(valid_rows) == 2:
            break
    if not valid_rows:
        return None

    latest_row, latest_payload = valid_rows[0]
    previous = (
        _summary_snapshot_reference(*valid_rows[1])
        if len(valid_rows) > 1
        else None
    )
    latest_payload["snapshot"] = {
        "persisted": True,
        "saved_at": _utc_iso(latest_row.created_at),
        "previous": previous,
    }

    generated_at = latest_row.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - generated_at).total_seconds()
    return _summary_with_cache_metadata(
        latest_payload,
        is_cached=True,
        age_seconds=age_seconds,
        origin="database",
        persisted_at=_utc_iso(latest_row.created_at),
    )


async def _persist_summary(
    session,
    *,
    owner_id: str,
    owner_user_id: int,
    period: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    previous_rows = (
        await session.execute(
            select(SummarySnapshot)
            .where(
                owned_by_ids(SummarySnapshot, owner_user_id, owner_id),
                SummarySnapshot.period == period,
            )
            .order_by(SummarySnapshot.created_at.desc(), SummarySnapshot.id.desc())
            .limit(5)
        )
    ).scalars().all()
    previous = None
    for previous_row in previous_rows:
        previous_payload = _load_json_object(previous_row.payload)
        if previous_payload:
            previous = _summary_snapshot_reference(previous_row, previous_payload)
            break

    stored_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"cache", "snapshot"}
    }
    generated_at = datetime.now(timezone.utc)
    generated_raw = stored_payload.get("generated_at")
    if isinstance(generated_raw, str):
        try:
            generated_at = datetime.fromisoformat(generated_raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    snapshot = SummarySnapshot(
        owner_id=owner_id,
        owner_user_id=owner_user_id,
        period=period,
        payload=json.dumps(stored_payload, ensure_ascii=False, separators=(",", ":")),
        generated_at=generated_at,
    )
    session.add(snapshot)
    await session.flush()

    stale_ids = (
        await session.execute(
            select(SummarySnapshot.id)
            .where(
                owned_by_ids(SummarySnapshot, owner_user_id, owner_id),
                SummarySnapshot.period == period,
            )
            .order_by(SummarySnapshot.created_at.desc(), SummarySnapshot.id.desc())
            .offset(SUMMARY_SNAPSHOT_RETENTION)
        )
    ).scalars().all()
    if stale_ids:
        await session.execute(
            delete(SummarySnapshot).where(SummarySnapshot.id.in_(stale_ids))
        )
    await session.commit()

    return {
        "persisted": True,
        "saved_at": _utc_iso(snapshot.created_at),
        "previous": previous,
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
        "currency_mode": "account",
    }
    validation_error = ""
    try:
        validate_runtime_rule(snapshot)
    except (TypeError, ValueError) as error:
        validation_error = str(error)
    if has_legacy_cpa or validation_error:
        snapshot.update(
            {
                "enabled": False,
                "needs_review": True,
                "review_reason": (
                    "Замените старый общий CPA на CPL, CPReg или CPP."
                    if has_legacy_cpa
                    else "Правило сохранено в старом или небезопасном формате и требует пересохранения."
                ),
            }
        )
    return snapshot


def _preset_response(preset: RulePreset) -> RulePresetItem:
    try:
        raw_conditions = json.loads(preset.conditions) if preset.conditions else []
    except (TypeError, ValueError):
        raw_conditions = []
    normalized_conditions, _, _ = normalize_rule_conditions(raw_conditions)
    conditions = []
    for condition in normalized_conditions:
        if not isinstance(condition, dict):
            continue
        try:
            conditions.append(ConditionItem(**condition))
        except ValidationError:
            continue
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


def _ensure_compatible_rule_set(rules: List[Dict[str, Any]]) -> None:
    try:
        validate_rule_set_compatibility(rules)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _ensure_compatible_presets(presets: List[RulePreset]) -> None:
    snapshots = [_preset_snapshot(preset) for preset in presets]
    if any(snapshot.get("needs_review") for snapshot in snapshots):
        raise HTTPException(
            status_code=400,
            detail="В наборе есть небезопасное или устаревшее правило. Пересохраните его.",
        )
    _ensure_compatible_rule_set(snapshots)


def _unique_preset_ids(preset_ids: List[int]) -> List[int]:
    return list(dict.fromkeys(preset_ids))


def _clean_rule_group_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Введите название группы.")
    return name


async def _get_owned_presets(
    session,
    user: TelegramUser,
    preset_ids: List[int],
    *,
    owner_user_id: Optional[int] = None,
    owner_id: str = "",
) -> List[RulePreset]:
    ordered_ids = _unique_preset_ids(preset_ids)
    owner_clause = (
        owned_by_ids(RulePreset, owner_user_id, owner_id)
        if owner_user_id is not None or owner_id
        else owned_by(RulePreset, user)
    )
    result = await session.execute(
        select(RulePreset).where(
            owner_clause,
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


async def _ensure_stable_account_owner(session, account: Account) -> None:
    if account.owner_user_id is not None or not account.owner_id:
        return
    owner_user_id = (
        await session.execute(
            select(TelegramUser.id).where(TelegramUser.telegram_id == account.owner_id)
        )
    ).scalar_one_or_none()
    if owner_user_id is not None:
        account.owner_user_id = owner_user_id


def _rule_group_response(group: RuleGroup, presets: List[RulePreset]) -> RuleGroupResponse:
    return RuleGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description or "",
        position=getattr(group, "position", 0) or 0,
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
                    select(TelegramUser.id).where(
                        TelegramUser.telegram_id == new_telegram_id,
                        TelegramUser.id != db_user.id,
                    )
                )
            ).scalar_one_or_none()
            if collision is not None:
                raise HTTPException(status_code=409, detail="Этот Telegram ID уже используется")

            legacy_owner_id = str(db_user.telegram_id or "")
            if legacy_owner_id:
                for model in (
                    Account,
                    RulePreset,
                    RuleGroup,
                    SummarySnapshot,
                    AnalyticsViewPreference,
                    AuditEvent,
                    AutomationScheduleState,
                    RuleExecutionState,
                    ActionUndoState,
                ):
                    await session.execute(
                        update(model)
                        .where(
                            model.owner_user_id.is_(None),
                            model.owner_id == legacy_owner_id,
                        )
                        .values(owner_user_id=db_user.id)
                    )
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
    async with async_session_maker() as session:
        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        workspaces = await get_user_workspaces_list(session, db_user)
        active_ws = next((w for w in workspaces if w.is_active), workspaces[0] if workspaces else None)
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
            onboarding_completed=bool(getattr(db_user, "onboarding_completed", False)),
            active_workspace=active_ws,
            workspaces=workspaces
        )


# ----------------------------------------------------
# Workspace Endpoints
# ----------------------------------------------------
@router.get("/workspaces", response_model=List[WorkspaceItem])
async def list_workspaces(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        return await get_user_workspaces_list(session, user)


@router.post("/workspaces", response_model=WorkspaceItem)
async def create_workspace(req: CreateWorkspaceRequest, user: TelegramUser = Depends(get_current_user)):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название воркспейса обязательно")
    
    slug = slugify(req.slug.strip()) if req.slug else slugify(name)
    badge_color = req.badge_color or "#F5A300"
    badge_text = req.badge_text.strip() if req.badge_text else name[:1].upper()
    logo_url = req.logo_url.strip() if req.logo_url else ""

    async with async_session_maker() as session:
        existing = (await session.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one_or_none()
        if existing:
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        ws = Workspace(
            name=name,
            slug=slug,
            badge_text=badge_text,
            badge_color=badge_color,
            logo_url=logo_url,
            owner_user_id=user.id
        )
        session.add(ws)
        await session.flush()

        member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
        session.add(member)

        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        db_user.active_workspace_id = ws.id
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
            members_count=1
        )


@router.get("/workspaces/current", response_model=WorkspaceItem)
async def get_current_workspace_info(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        workspaces = await get_user_workspaces_list(session, user)
        active_ws = next((w for w in workspaces if w.is_active), workspaces[0] if workspaces else None)
        if not active_ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")
        return active_ws


@router.post("/workspaces/switch")
async def switch_workspace(req: SwitchWorkspaceRequest, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        target_ws = None
        if req.workspace_id:
            target_ws = (await session.execute(select(Workspace).where(Workspace.id == req.workspace_id))).scalar_one_or_none()
        elif req.slug:
            target_ws = (await session.execute(select(Workspace).where(Workspace.slug == req.slug))).scalar_one_or_none()

        if not target_ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        member = (await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == target_ws.id,
                WorkspaceMember.user_id == user.id
            )
        )).scalar_one_or_none()
        if not member and user.role != "admin":
            raise HTTPException(status_code=403, detail="Нет доступа к данному воркспейсу")

        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        db_user.active_workspace_id = target_ws.id
        await session.commit()

        workspaces = await get_user_workspaces_list(session, user)
        active_ws = next((w for w in workspaces if w.id == target_ws.id), None)
        return {"status": "ok", "active_workspace": active_ws}


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceItem)
async def update_workspace(workspace_id: int, req: UpdateWorkspaceRequest, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        member = (await session.execute(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
        )).scalar_one_or_none()
        if not member or member.role not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Недостаточно прав для редактирования воркспейса")

        if req.name and req.name.strip():
            ws.name = req.name.strip()
        if req.badge_color and req.badge_color.strip():
            ws.badge_color = req.badge_color.strip()
        if req.badge_text and req.badge_text.strip():
            ws.badge_text = req.badge_text.strip()
        if req.logo_url is not None:
            ws.logo_url = req.logo_url.strip()

        await session.commit()
        workspaces = await get_user_workspaces_list(session, user)
        return next((w for w in workspaces if w.id == ws.id), None)


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: int, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")
        if ws.owner_user_id != user.id and user.role != "admin":
            raise HTTPException(status_code=403, detail="Только владелец может удалить воркспейс")

        other_member = (await session.execute(
            select(WorkspaceMember)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id != workspace_id)
            .limit(1)
        )).scalar_one_or_none()
        if not other_member:
            raise HTTPException(status_code=400, detail="Нельзя удалить единственный воркспейс")

        await session.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id))
        await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        db_user.active_workspace_id = other_member.workspace_id
        await session.commit()
        return {"status": "ok", "message": "Воркспейс удалён", "next_workspace_id": other_member.workspace_id}


# ----------------------------------------------------
# Workspace Members Management Endpoints
# ----------------------------------------------------

@router.get("/workspaces/{workspace_id}/members", response_model=List[WorkspaceMemberItem])
async def list_workspace_members(
    workspace_id: int,
    user: TelegramUser = Depends(get_current_user),
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
                select(WorkspaceMember, TelegramUser)
                .join(TelegramUser, TelegramUser.id == WorkspaceMember.user_id)
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
    user: TelegramUser = Depends(get_current_user),
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

        target_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == member_user_id))).scalar_one()
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
    user: TelegramUser = Depends(get_current_user),
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

        target_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == member_user_id))).scalar_one_or_none()
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
        return {"status": "ok", "message": "Участник успешно исключён из воркспейса"}


@router.post("/workspaces/{workspace_id}/leave")
async def leave_workspace(
    workspace_id: int,
    user: TelegramUser = Depends(get_current_user),
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

        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
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
        return {"status": "ok", "message": "Вы вышли из воркспейса", "next_workspace_id": next_ws_id}


@router.post("/workspaces/{workspace_id}/transfer-ownership")
async def transfer_workspace_ownership(
    workspace_id: int,
    req: TransferOwnershipRequest,
    user: TelegramUser = Depends(get_current_user),
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
        return {
            "status": "ok",
            "message": "Права владения успешно переданы",
            "new_owner_user_id": req.new_owner_user_id,
        }


# ----------------------------------------------------
# Workspace Invites Endpoints
# ----------------------------------------------------

@router.post("/workspaces/{workspace_id}/invites", response_model=WorkspaceInviteItem)
async def create_workspace_invite(
    workspace_id: int,
    req: CreateWorkspaceInviteRequest,
    user: TelegramUser = Depends(get_current_user),
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
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
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
        await session.commit()
        await session.refresh(invite)

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
    user: TelegramUser = Depends(get_current_user),
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
                select(WorkspaceInvite, TelegramUser)
                .outerjoin(TelegramUser, TelegramUser.id == WorkspaceInvite.inviter_user_id)
                .where(WorkspaceInvite.workspace_id == workspace_id)
                .order_by(WorkspaceInvite.id.desc())
            )
        ).all()

        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
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
    user: TelegramUser = Depends(get_current_user),
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
        await session.commit()
        return {"status": "ok", "message": "Приглашение успешно отозвано"}


@router.get("/invites/{token}", response_model=PublicInviteInfoResponse)
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

        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
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
                    select(TelegramUser).where(TelegramUser.id == invite.inviter_user_id)
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


@router.post("/invites/{token}/accept")
async def accept_workspace_invite(
    token: str,
    user: TelegramUser = Depends(get_current_user),
):
    """Accept a workspace invite and join the workspace."""
    async with async_session_maker() as session:
        invite = (
            await session.execute(
                select(WorkspaceInvite).where(WorkspaceInvite.token == token)
            )
        ).scalar_one_or_none()
        if not invite:
            raise HTTPException(status_code=404, detail="Приглашение не найдено")

        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        if invite.status == "revoked":
            raise HTTPException(status_code=400, detail="Это приглашение было отозвано")

        if invite.expires_at and now_dt > invite.expires_at:
            invite.status = "expired"
            await session.commit()
            raise HTTPException(status_code=400, detail="Срок действия приглашения истёк")

        if invite.max_uses > 0 and invite.used_count >= invite.max_uses:
            raise HTTPException(status_code=400, detail="Лимит использований приглашения исчерпан")

        ws = (await session.execute(select(Workspace).where(Workspace.id == invite.workspace_id))).scalar_one_or_none()
        if not ws:
            raise HTTPException(status_code=404, detail="Воркспейс не найден")

        existing_m = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == invite.workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()

        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
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

        await session.commit()
        return {
            "status": "ok",
            "message": f"Вы успешно присоединились к воркспейсу {ws.name}",
            "workspace_id": ws.id,
            "workspace_slug": ws.slug,
            "role": existing_m.role if existing_m else invite.role,
        }


# ----------------------------------------------------
# Onboarding & Profile Flow Endpoints
# ----------------------------------------------------

@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(user: TelegramUser = Depends(get_current_user)):
    """Return the current onboarding status and progress step for the user."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
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
    user: TelegramUser = Depends(get_current_user),
):
    """Save user first name, last name, and optional email during onboarding."""
    first_name = req.first_name.strip()
    last_name = req.last_name.strip()
    if not first_name:
        raise HTTPException(status_code=400, detail="Имя обязательно для заполнения")

    clean_email = req.email.strip().lower() if req.email and req.email.strip() else None

    async with async_session_maker() as session:
        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        db_user.first_name = first_name
        db_user.last_name = last_name
        db_user.full_name = f"{first_name} {last_name}".strip()
        if clean_email:
            db_user.email = clean_email

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
    user: TelegramUser = Depends(get_current_user),
):
    """Upload custom avatar image for the user during or after onboarding."""
    filename = file.filename or "avatar.png"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        raise HTTPException(status_code=400, detail="Поддерживаются только форматы PNG, JPG, WEBP, SVG")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Размер файла не должен превышать 5 МБ")

    upload_dir = os.path.join("webapp", "uploads", "avatars")
    os.makedirs(upload_dir, exist_ok=True)

    unique_filename = f"avatar_{user.id}_{int(time.time())}_{secrets.token_hex(4)}{ext}"
    target_path = os.path.join(upload_dir, unique_filename)

    with open(target_path, "wb") as f:
        f.write(content)

    avatar_url = f"/uploads/avatars/{unique_filename}"

    async with async_session_maker() as session:
        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        db_user.avatar_url = avatar_url
        await session.commit()

    return {"status": "ok", "avatar_url": avatar_url}


@router.delete("/onboarding/avatar")
async def delete_onboarding_avatar(user: TelegramUser = Depends(get_current_user)):
    """Reset user avatar to default initial badge."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        db_user.avatar_url = ""
        await session.commit()
    return {"status": "ok", "avatar_url": ""}


@router.get("/onboarding/check-slug", response_model=CheckSlugResponse)
async def check_workspace_slug(
    slug: str = Query(..., min_length=2, max_length=60),
    user: TelegramUser = Depends(get_current_user),
):
    """Check availability and validity of workspace slug in real-time."""
    raw_slug = slug.strip().lower()
    cleaned_slug = slugify(raw_slug)

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
    user: TelegramUser = Depends(get_current_user),
):
    """Create a new workspace during onboarding and set it as active."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название воркспейса обязательно")

    raw_slug = req.slug.strip().lower() if req.slug else slugify(name)
    slug = slugify(raw_slug) if raw_slug else "workspace"
    if not slug or len(slug) < 2:
        slug = f"ws-{secrets.token_hex(3)}"

    if slug in RESERVED_WORKSPACE_SLUGS:
        slug = f"{slug}-{secrets.token_hex(3)}"

    badge_color = req.badge_color or "#F5A300"
    badge_text = req.badge_text.strip() if req.badge_text else name[:1].upper()
    logo_url = req.logo_url.strip() if req.logo_url else ""

    async with async_session_maker() as session:
        existing = (await session.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one_or_none()
        if existing:
            slug = f"{slug}-{secrets.token_hex(3)}"

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

        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
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
    user: TelegramUser = Depends(get_current_user),
):
    """Upload workspace logo image."""
    filename = file.filename or "logo.png"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        raise HTTPException(status_code=400, detail="Поддерживаются только форматы PNG, JPG, WEBP, SVG")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Размер файла не должен превышать 5 МБ")

    upload_dir = os.path.join("webapp", "uploads", "workspaces")
    os.makedirs(upload_dir, exist_ok=True)

    unique_filename = f"logo_{user.id}_{int(time.time())}_{secrets.token_hex(4)}{ext}"
    target_path = os.path.join(upload_dir, unique_filename)

    with open(target_path, "wb") as f:
        f.write(content)

    logo_url = f"/uploads/workspaces/{unique_filename}"
    return {"status": "ok", "logo_url": logo_url}


@router.post("/onboarding/invites", response_model=OnboardingBulkInvitesResponse)
async def submit_onboarding_invites(
    req: OnboardingBulkInvitesRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """Create bulk team member invitations and finalize onboarding."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        active_ws = await get_user_workspace(session, db_user)
        if not active_ws:
            raise HTTPException(status_code=400, detail="Не найден активный воркспейс для создания приглашений")

        created_invites: List[WorkspaceInviteItem] = []
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
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
async def skip_onboarding(user: TelegramUser = Depends(get_current_user)):
    """Skip remaining onboarding steps and mark onboarding completed."""
    async with async_session_maker() as session:
        db_user = (await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))).scalar_one()
        db_user.onboarding_step = "completed"
        db_user.onboarding_completed = True
        await session.commit()
    return {"status": "ok", "onboarding_completed": True, "redirect_url": "/"}


@router.get("/accounts", response_model=List[AccountItem])
async def list_accounts(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        accounts = await get_user_accounts(session, user)
        group_ids_by_account = await _account_group_ids_by_account(session, user)
        latest_summary = await _load_persisted_summary(
            session,
            owner_id=str(user.telegram_id or ""),
            owner_user_id=user.id,
            period="today",
        )
        latest_metrics = _latest_account_metrics_by_id(latest_summary)
        
        items = []
        for a in accounts:
            active_rules_list = _load_active_rules(a.active_rules)
            
            items.append(AccountItem(
                id=a.id,
                account_id=a.account_id,
                name=a.name,
                custom_name=a.custom_name or "",
                note=a.note or "",
                connection_type=(
                    "facebook_login" if a.meta_connection_id else "system_user"
                ),
                owner_id=a.owner_id,
                batch_name=a.batch_name or "",
                timezone_name=a.timezone_name or "UTC",
                currency=normalize_currency(a.currency),
                account_status=a.account_status,
                status_label=a.status_label or "Активен (ACTIVE)",
                rules_enabled=a.rules_enabled,
                is_active=a.is_active,
                active_rules=active_rules_list,
                group_ids=group_ids_by_account.get(a.account_id, []),
                latest_metrics=latest_metrics.get(a.account_id),
                created_at=a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else ""
            ))
        return items


@router.get("/account-groups", response_model=List[AccountGroupItem])
async def list_account_groups(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        return await _account_group_items(session, user)


@router.post("/account-groups", response_model=AccountGroupItem, status_code=status.HTTP_201_CREATED)
async def create_account_group(
    payload: AccountGroupRequest,
    user: TelegramUser = Depends(get_current_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Название группы не может быть пустым")
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "создания групп кабинетов")

        scope_clause = (
            or_(AccountGroup.workspace_id == ws.id, and_(AccountGroup.workspace_id.is_(None), owned_by(AccountGroup, user)))
            if ws
            else owned_by(AccountGroup, user)
        )
        duplicate = (
            await session.execute(
                select(AccountGroup.id).where(
                    scope_clause,
                    func.lower(AccountGroup.name) == name.lower(),
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Группа с таким названием уже существует")

        accounts = await _validate_account_group_members(session, user, payload.account_ids)
        group = AccountGroup(
            workspace_id=ws.id if ws else None,
            owner_id=str(user.telegram_id or ""),
            owner_user_id=user.id,
            name=name,
            description=payload.description.strip(),
        )
        session.add(group)
        await session.flush()
        for position, account in enumerate(accounts):
            session.add(AccountGroupMember(group_id=group.id, account_id=account.id, position=position))
        await session.commit()
        items = await _account_group_items(session, user)
        return next(item for item in items if item.id == group.id)


@router.put("/account-groups/{group_id}", response_model=AccountGroupItem)
async def update_account_group(
    group_id: int,
    payload: AccountGroupRequest,
    user: TelegramUser = Depends(get_current_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Название группы не может быть пустым")
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "редактирования групп кабинетов")

        scope_clause = (
            or_(AccountGroup.workspace_id == ws.id, and_(AccountGroup.workspace_id.is_(None), owned_by(AccountGroup, user)))
            if ws
            else owned_by(AccountGroup, user)
        )
        group = (
            await session.execute(
                select(AccountGroup).where(
                    AccountGroup.id == group_id,
                    scope_clause,
                )
            )
        ).scalar_one_or_none()
        if group is None:
            raise HTTPException(status_code=404, detail="Группа кабинетов не найдена")
        duplicate = (
            await session.execute(
                select(AccountGroup.id).where(
                    scope_clause,
                    func.lower(AccountGroup.name) == name.lower(),
                    AccountGroup.id != group_id,
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Группа с таким названием уже существует")

        accounts = await _validate_account_group_members(session, user, payload.account_ids)
        group.name = name
        group.description = payload.description.strip()
        group.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.execute(delete(AccountGroupMember).where(AccountGroupMember.group_id == group.id))
        for position, account in enumerate(accounts):
            session.add(AccountGroupMember(group_id=group.id, account_id=account.id, position=position))
        await session.commit()
        items = await _account_group_items(session, user)
        return next(item for item in items if item.id == group.id)


@router.delete("/account-groups/{group_id}")
async def delete_account_group(
    group_id: int,
    user: TelegramUser = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "удаления групп кабинетов")

        scope_clause = (
            or_(AccountGroup.workspace_id == ws.id, and_(AccountGroup.workspace_id.is_(None), owned_by(AccountGroup, user)))
            if ws
            else owned_by(AccountGroup, user)
        )
        group = (
            await session.execute(
                select(AccountGroup).where(
                    AccountGroup.id == group_id,
                    scope_clause,
                )
            )
        ).scalar_one_or_none()
        if group is None:
            raise HTTPException(status_code=404, detail="Группа кабинетов не найдена")
        await session.execute(delete(AccountGroupMember).where(AccountGroupMember.group_id == group.id))
        await session.delete(group)
        await session.commit()
    return {"message": "Группа кабинетов удалена", "group_id": group_id}



# ----------------------------------------------------
# RULE PRESETS ENDPOINTS
# ----------------------------------------------------

@router.get("/presets", response_model=List[RulePresetItem])
async def list_presets(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        await ensure_rule_examples(session, user)
        ws = await get_user_workspace(session, user)
        scope_clause = (
            or_(RulePreset.workspace_id == ws.id, and_(RulePreset.workspace_id.is_(None), owned_by(RulePreset, user)))
            if ws
            else owned_by(RulePreset, user)
        )
        stmt = select(RulePreset).where(scope_clause).order_by(RulePreset.id.desc())
        res = await session.execute(stmt)
        presets = res.scalars().all()
        return [_preset_response(preset) for preset in presets]


@router.post("/presets", response_model=RulePresetItem)
async def create_preset(payload: CreatePresetRequest, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "создания правил")

        condition_payloads = _validated_condition_payloads(payload.conditions)
        conds_json = json.dumps(condition_payloads)
        preset = RulePreset(
            workspace_id=ws.id if ws else None,
            owner_id=user.telegram_id,
            owner_user_id=user.id,
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
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "редактирования правил")

        condition_payloads = _validated_condition_payloads(payload.conditions)
        stmt = select(RulePreset).where(RulePreset.id == preset_id)
        if user.role != "admin":
            scope_clause = (
                or_(RulePreset.workspace_id == ws.id, and_(RulePreset.workspace_id.is_(None), owned_by(RulePreset, user)))
                if ws
                else owned_by(RulePreset, user)
            )
            stmt = stmt.where(scope_clause)
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
                _ensure_compatible_rule_set(active_rules)
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
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "удаления правил")

        stmt = select(RulePreset).where(RulePreset.id == preset_id)
        if user.role != "admin":
            scope_clause = (
                or_(RulePreset.workspace_id == ws.id, and_(RulePreset.workspace_id.is_(None), owned_by(RulePreset, user)))
                if ws
                else owned_by(RulePreset, user)
            )
            stmt = stmt.where(scope_clause)
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
        await ensure_rule_examples(session, user)
        ws = await get_user_workspace(session, user)
        scope_clause = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        groups = (
            await session.execute(
                select(RuleGroup)
                .where(scope_clause)
                .order_by(RuleGroup.position.asc(), RuleGroup.id.asc())
            )
        ).scalars().all()
        presets_by_group = await _load_group_presets(session, [group.id for group in groups])
        return [
            _rule_group_response(group, presets_by_group.get(group.id, []))
            for group in groups
        ]


@router.put("/rule-groups/reorder", response_model=List[RuleGroupResponse])
async def reorder_rule_groups(
    payload: RuleGroupsReorderRequest,
    user: TelegramUser = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws = await get_user_workspace(session, user)
        scope_clause = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        groups = (
            await session.execute(
                select(RuleGroup).where(scope_clause)
            )
        ).scalars().all()
        group_map = {g.id: g for g in groups}

        for idx, gid in enumerate(payload.group_ids):
            if gid in group_map:
                group_map[gid].position = idx

        await session.commit()

        ordered_groups = (
            await session.execute(
                select(RuleGroup)
                .where(scope_clause)
                .order_by(RuleGroup.position.asc(), RuleGroup.id.asc())
            )
        ).scalars().all()
        presets_by_group = await _load_group_presets(session, [g.id for g in ordered_groups])
        return [
            _rule_group_response(g, presets_by_group.get(g.id, []))
            for g in ordered_groups
        ]


@router.post("/rule-groups", response_model=RuleGroupResponse)
async def create_rule_group(
    payload: RuleGroupWriteRequest,
    user: TelegramUser = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "создания групп правил")

        presets = await _get_owned_presets(session, user, payload.preset_ids)
        _ensure_compatible_presets(presets)
        scope_clause = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        if payload.position is not None:
            position = payload.position
        else:
            max_pos = (
                await session.execute(
                    select(func.max(RuleGroup.position)).where(owned_by(RuleGroup, user))
                )
            ).scalar()
            position = (max_pos + 1) if max_pos is not None else 0

        group = RuleGroup(
            workspace_id=ws.id if ws else None,
            owner_id=user.telegram_id,
            owner_user_id=user.id,
            name=_clean_rule_group_name(payload.name),
            description=payload.description.strip(),
            position=position,
        )
        session.add(group)
        await session.flush()
        session.add_all(
            RuleGroupItem(group_id=group.id, preset_id=preset.id, position=pos)
            for pos, preset in enumerate(presets)
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
                    owned_by(RuleGroup, user),
                )
            )
        ).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="Группа правил не найдена.")

        presets = await _get_owned_presets(session, user, payload.preset_ids)
        _ensure_compatible_presets(presets)
        group.name = _clean_rule_group_name(payload.name)
        group.description = payload.description.strip()
        if payload.position is not None:
            group.position = payload.position
        await session.execute(delete(RuleGroupItem).where(RuleGroupItem.group_id == group.id))
        session.add_all(
            RuleGroupItem(group_id=group.id, preset_id=preset.id, position=pos)
            for pos, preset in enumerate(presets)
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
                    owned_by(RuleGroup, user),
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
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "привязки правил к кабинету")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(scope_clause)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")
        await _ensure_stable_account_owner(session, acc)

        # If preset_id provided, load preset
        if payload.preset_id:
            p_stmt = select(RulePreset).where(RulePreset.id == payload.preset_id)
            if user.role != "admin":
                p_scope = (
                    or_(RulePreset.workspace_id == ws.id, and_(RulePreset.workspace_id.is_(None), owned_by(RulePreset, user)))
                    if ws
                    else owned_by(RulePreset, user)
                )
                p_stmt = p_stmt.where(p_scope)
            p_res = await session.execute(p_stmt)
            preset = p_res.scalar_one_or_none()
            if not preset:
                raise HTTPException(status_code=404, detail="Пресет не найден.")
            
            new_rule = _preset_snapshot(preset)
            if new_rule.get("needs_review"):
                raise HTTPException(
                    status_code=400,
                    detail="Правило имеет небезопасные или устаревшие параметры. Откройте и пересохраните его.",
                )
        else:
            raise HTTPException(status_code=400, detail="Custom rules without preset are no longer supported.")

        active_rules = _load_active_rules(acc.active_rules)
            
        # Check if preset already attached
        if any(r.get("preset_id") == new_rule["preset_id"] for r in active_rules):
            raise HTTPException(status_code=400, detail="Это правило уже привязано к кабинету.")
            
        active_rules.append(new_rule)
        _ensure_compatible_rule_set(active_rules)
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
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "назначения группы правил")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        account_stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            account_stmt = account_stmt.where(scope_clause)
        account = (await session.execute(account_stmt)).scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")
        await _ensure_stable_account_owner(session, account)

        group_scope = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        group_stmt = select(RuleGroup).where(RuleGroup.id == group_id)
        if user.role != "admin":
            group_stmt = group_stmt.where(group_scope)
        group = (await session.execute(group_stmt)).scalar_one_or_none()
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
            user,
            [item.preset_id for item in group_items],
            owner_user_id=account.owner_user_id,
            owner_id=account.owner_id,
        )
        active_rules = _load_active_rules(account.active_rules)
        attached_ids = {rule.get("preset_id") for rule in active_rules}
        added_presets = [preset for preset in presets if preset.id not in attached_ids]
        new_snapshots = [_preset_snapshot(preset) for preset in added_presets]
        if any(snapshot.get("needs_review") for snapshot in new_snapshots):
            raise HTTPException(
                status_code=400,
                detail="В группе есть небезопасное или устаревшее правило. Пересохраните его перед назначением.",
            )
        active_rules.extend(new_snapshots)
        _ensure_compatible_rule_set(active_rules)
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
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "отвязки правил от кабинета")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(scope_clause)

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
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "включения/выключения правил")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(scope_clause)
        
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

        if not acc.rules_enabled:
            _ensure_compatible_rule_set(active_rules)

        acc.rules_enabled = not acc.rules_enabled
        await session.commit()
        return {
            "account_id": acc.account_id,
            "rules_enabled": acc.rules_enabled,
            "message": f"Авто-правила {'включены' if acc.rules_enabled else 'выключены'}"
        }


@router.patch("/accounts/{account_id}/profile")
async def update_account_profile(
    account_id: str,
    payload: AccountProfileUpdateRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """Update owner-only Buyerly labels without changing the Meta account name."""

    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "редактирования кабинета")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(scope_clause)
        account = (await session.execute(stmt)).scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        account.custom_name = payload.custom_name.strip()
        account.note = payload.note.strip()
        await session.commit()
        return {
            "account_id": account.account_id,
            "custom_name": account.custom_name,
            "note": account.note,
            "message": "Название и заметка сохранены",
        }


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "удаления кабинета")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(scope_clause)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        await session.execute(delete(AccountGroupMember).where(AccountGroupMember.account_id == acc.id))
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
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "добавления рекламных кабинетов")
        for idx, item in enumerate(payload.accounts, start=1):
            acc_id = item.account_id if item.account_id.startswith("act_") else f"act_{item.account_id}"
            custom_name = item.name.strip() if item.name else ""

            try:
                acc_info = await meta_client.get_account_info(acc_id, token)
                timezone_name = str(acc_info.get("timezone_name") or "").strip()
                if resolve_account_clock(timezone_name) is None:
                    raise RuntimeError(
                        "Meta не вернула поддерживаемый часовой пояс рекламного кабинета."
                    )
                fb_name = acc_info.get("name", acc_id)
                status_code = acc_info.get("account_status", 1)
                status_label = acc_info.get("status_label", "Активен (ACTIVE)")
                currency = normalize_currency(acc_info.get("currency"))

                if batch_name != "-" and len(batch_name) > 0:
                    display_name = f"{batch_name} {idx}" if len(payload.accounts) > 1 else batch_name
                elif custom_name:
                    display_name = custom_name
                else:
                    display_name = fb_name

                res = await session.execute(select(Account).where(Account.account_id == acc_id))
                existing = res.scalar_one_or_none()

                ws = await get_user_workspace(session, user)
                if existing:
                    if existing.timezone_name != timezone_name:
                        existing.last_day_start_date = ""
                    existing.name = display_name
                    existing.access_token = token
                    existing.meta_connection_id = None
                    existing.timezone_name = timezone_name
                    existing.currency = currency
                    existing.owner_id = owner_id
                    existing.owner_user_id = user.id
                    existing.workspace_id = ws.id if ws else existing.workspace_id
                    existing.batch_name = batch_name if batch_name != "-" else ""
                    existing.account_status = status_code
                    existing.status_label = status_label
                    existing.is_active = True
                else:
                    new_acc = Account(
                        workspace_id=ws.id if ws else None,
                        account_id=acc_id,
                        name=display_name,
                        access_token=token,
                        owner_id=owner_id,
                        owner_user_id=user.id,
                        batch_name=batch_name if batch_name != "-" else "",
                        timezone_name=timezone_name,
                        currency=currency,
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
                    "currency": currency,
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


@router.get("/analytics-view")
async def get_analytics_view(user: TelegramUser = Depends(get_current_user)):
    owner_key = _summary_owner_key(user)
    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(AnalyticsViewPreference).where(
                    owned_by(AnalyticsViewPreference, user),
                    AnalyticsViewPreference.scope == SUMMARY_VIEW_SCOPE,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            config = _normalize_summary_view_config({})
        else:
            try:
                stored_config = json.loads(row.config or "{}")
            except (TypeError, json.JSONDecodeError):
                stored_config = {}
            stored_order = stored_config.get("column_order")
            if (
                isinstance(stored_order, list)
                and "custom_name" not in stored_order
                and "note" not in stored_order
            ):
                # Views saved before account annotations existed receive the
                # two new columns once. The next normal save persists the new
                # order, after which either column can be hidden normally.
                insert_at = stored_order.index("account") + 1 if "account" in stored_order else 0
                stored_order[insert_at:insert_at] = ["custom_name", "note"]
                stored_visible = stored_config.get("visible_columns")
                if isinstance(stored_visible, list):
                    visible_at = stored_visible.index("account") + 1 if "account" in stored_visible else 0
                    stored_visible[visible_at:visible_at] = ["custom_name", "note"]
            config = _normalize_summary_view_config(stored_config, strict=False)
        return _analytics_view_response(row, config)


@router.put("/analytics-view")
async def save_analytics_view(
    payload: AnalyticsViewPreferenceRequest,
    user: TelegramUser = Depends(get_current_user),
):
    owner_key = _summary_owner_key(user)
    config = _normalize_summary_view_config(payload.model_dump())
    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(AnalyticsViewPreference).where(
                    owned_by(AnalyticsViewPreference, user),
                    AnalyticsViewPreference.scope == SUMMARY_VIEW_SCOPE,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = AnalyticsViewPreference(
                owner_id=str(user.telegram_id or ""),
                owner_user_id=user.id,
                scope=SUMMARY_VIEW_SCOPE,
                config=json.dumps(config, ensure_ascii=False),
            )
            session.add(row)
        else:
            row.config = json.dumps(config, ensure_ascii=False)
            row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
        return _analytics_view_response(row, config)


@router.get("/summary")
async def get_summary_report(
    period: str = Query("today", pattern="^(today|yesterday|last_3d|last_7d)$"),
    force: bool = Query(False),
    user: TelegramUser = Depends(get_current_user)
):
    owner_key = _summary_owner_key(user)
    cache_key = f"{owner_key}:{period}"
    now_ts = time.time()

    # Return cached data if valid and force is False
    if not force and cache_key in _summary_cache:
        cached_ts, cached_data = _summary_cache[cache_key]
        if now_ts - cached_ts < SUMMARY_CACHE_TTL:
            async with async_session_maker() as session:
                enriched_cached_data = await _enrich_summary_account_metadata(
                    session,
                    cached_data,
                    user,
                )
            return _summary_with_cache_metadata(
                enriched_cached_data,
                is_cached=True,
                age_seconds=now_ts - cached_ts,
                origin="memory",
                persisted_at=(cached_data.get("snapshot") or {}).get("saved_at", ""),
            )

    async with async_session_maker() as session:
        if not force:
            persisted = await _load_persisted_summary(
                session,
                owner_id=str(user.telegram_id or ""),
                owner_user_id=user.id,
                period=period,
            )
            if persisted:
                persisted = await _enrich_summary_account_metadata(session, persisted, user)
                cached_payload = {
                    key: value
                    for key, value in persisted.items()
                    if key != "cache"
                }
                _summary_cache[cache_key] = (now_ts, cached_payload)
                return persisted

        accounts = await get_user_accounts(session, user)
        group_ids_by_account = await _account_group_ids_by_account(session, user)
        if not accounts:
            empty_res = {
                "period": period,
                "generated_at": _utc_iso(datetime.now(timezone.utc)),
                "source": "Meta Marketing API",
                "total_spend": 0.0,
                "display_currency": "",
                "mixed_currencies": False,
                "currency_totals": [],
                "total_clicks": 0,
                "total_impressions": 0,
                "total_reach": 0,
                "total_unique_clicks": 0,
                "total_link_clicks": 0,
                "total_outbound_clicks": 0,
                "total_landing_page_views": 0,
                "avg_frequency": None,
                "avg_cpm": None,
                "total_leads": 0,
                "total_regs": 0,
                "total_purchases": 0,
                "avg_cpc": 0.0,
                "avg_ctr": 0.0,
                "avg_cpc_link": None,
                "avg_ctr_link": None,
                "avg_ctr_outbound": None,
                "cost_per_landing_page_view": None,
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
            empty_res["snapshot"] = await _persist_summary(
                session,
                owner_id=str(user.telegram_id or ""),
                owner_user_id=user.id,
                period=period,
                payload=empty_res,
            )
            _summary_cache[cache_key] = (now_ts, empty_res)
            return _summary_with_cache_metadata(
                empty_res,
                is_cached=False,
                origin="live",
                persisted_at=empty_res["snapshot"]["saved_at"],
            )

        total_spend = 0.0
        total_clicks = 0
        total_impressions = 0
        total_reach = 0
        total_unique_clicks = 0
        total_link_clicks = 0
        total_outbound_clicks = 0
        total_landing_page_views = 0
        total_leads = 0
        total_regs = 0
        total_purchases = 0
        accounts_synced = 0
        accounts_failed = 0
        accounts_blocked = 0
        currency_buckets: Dict[str, Dict[str, Any]] = {}

        account_results = []

        for acc in accounts:
            short_name = get_short_account_label(acc.name, acc.account_id)
            account_currency = normalize_currency(acc.currency)
            try:
                access_token = await resolve_account_access_token(session, acc)
                if account_currency == UNKNOWN_CURRENCY:
                    account_info = await meta_client.get_account_info(
                        acc.account_id,
                        access_token,
                    )
                    account_currency = normalize_currency(account_info.get("currency"))
                    if account_currency == UNKNOWN_CURRENCY:
                        raise RuntimeError("Meta не вернула валюту рекламного кабинета")
                    acc.currency = account_currency
                    await session.commit()
                account_insights = await meta_client.get_account_insights_summary(
                    account_id=acc.account_id,
                    access_token=access_token,
                    date_preset=period
                )
                acc_spend = account_insights.get("spend", 0.0)
                acc_clicks = account_insights.get("clicks", 0)
                acc_impressions = account_insights.get("impressions", 0)
                acc_reach = account_insights.get("reach", 0)
                acc_unique_clicks = account_insights.get("unique_clicks", 0)
                acc_link_clicks = account_insights.get("link_clicks", 0)
                acc_outbound_clicks = account_insights.get("outbound_clicks", 0)
                acc_landing_page_views = account_insights.get("landing_page_views", 0)
                acc_leads = account_insights.get("leads", 0)
                acc_regs = account_insights.get("registrations", 0)
                acc_purchases = account_insights.get("purchases", 0)
                acc_cpc = (acc_spend / acc_clicks) if acc_clicks > 0 else 0.0
                acc_ctr = ((acc_clicks / acc_impressions) * 100) if acc_impressions > 0 else 0.0
                acc_frequency = (
                    account_insights.get("frequency")
                    or ((acc_impressions / acc_reach) if acc_reach > 0 else 0.0)
                )
                acc_cpm = (
                    account_insights.get("cpm")
                    or ((acc_spend / acc_impressions) * 1000 if acc_impressions > 0 else 0.0)
                )
                acc_ctr_link = (
                    (acc_link_clicks / acc_impressions) * 100
                    if acc_impressions > 0 else 0.0
                )
                acc_ctr_outbound = (
                    (acc_outbound_clicks / acc_impressions) * 100
                    if acc_impressions > 0 else 0.0
                )

                total_spend += acc_spend
                total_clicks += acc_clicks
                total_impressions += acc_impressions
                total_reach += acc_reach
                total_unique_clicks += acc_unique_clicks
                total_link_clicks += acc_link_clicks
                total_outbound_clicks += acc_outbound_clicks
                total_landing_page_views += acc_landing_page_views
                total_leads += acc_leads
                total_regs += acc_regs
                total_purchases += acc_purchases
                accounts_synced += 1

                bucket = currency_buckets.setdefault(
                    account_currency,
                    {
                        "accounts_count": 0,
                        "spend": 0.0,
                        "impressions": 0,
                        "clicks": 0,
                        "link_clicks": 0,
                        "landing_page_views": 0,
                        "leads": 0,
                        "registrations": 0,
                        "purchases": 0,
                    },
                )
                bucket["accounts_count"] += 1
                bucket["spend"] += acc_spend
                bucket["impressions"] += acc_impressions
                bucket["clicks"] += acc_clicks
                bucket["link_clicks"] += acc_link_clicks
                bucket["landing_page_views"] += acc_landing_page_views
                bucket["leads"] += acc_leads
                bucket["registrations"] += acc_regs
                bucket["purchases"] += acc_purchases

                account_results.append({
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "short_name": short_name,
                    "custom_name": acc.custom_name or "",
                    "note": acc.note or "",
                    "group_ids": group_ids_by_account.get(acc.account_id, []),
                    "timezone_name": acc.timezone_name,
                    "currency": account_currency,
                    "account_status": acc.account_status,
                    "status_label": acc.status_label,
                    "rules_enabled": acc.rules_enabled,
                    "spend": round(acc_spend, 2),
                    "clicks": acc_clicks,
                    "impressions": acc_impressions,
                    "reach": acc_reach,
                    "frequency": round(acc_frequency, 2),
                    "cpm": round(acc_cpm, 2),
                    "unique_clicks": acc_unique_clicks,
                    "link_clicks": acc_link_clicks,
                    "outbound_clicks": acc_outbound_clicks,
                    "landing_page_views": acc_landing_page_views,
                    "leads": acc_leads,
                    "registrations": acc_regs,
                    "purchases": acc_purchases,
                    "cost_per_lead": _cost_or_none(acc_spend, acc_leads),
                    "cost_per_registration": _cost_or_none(acc_spend, acc_regs),
                    "cost_per_purchase": _cost_or_none(acc_spend, acc_purchases),
                    "cpc": round(acc_cpc, 2),
                    "ctr": round(acc_ctr, 2),
                    "cpc_link": _cost_or_none(acc_spend, acc_link_clicks),
                    "ctr_link": round(acc_ctr_link, 2),
                    "ctr_outbound": round(acc_ctr_outbound, 2),
                    "cost_per_landing_page_view": _cost_or_none(
                        acc_spend,
                        acc_landing_page_views,
                    ),
                    "adsets": [],
                    "has_error": False,
                    "is_banned": not acc.is_active or acc.account_status in [2, 101],
                    "data_status": "synced",
                    "data_status_label": "Account-level метрики получены из Meta независимо от текущего статуса",
                })
            except Exception as e:
                logger.error(f"Error fetching insights for {acc.account_id}: {e}")
                is_blocked = not acc.is_active or acc.account_status in [2, 101]
                if is_blocked:
                    accounts_blocked += 1
                else:
                    accounts_failed += 1
                account_results.append({
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "short_name": short_name,
                    "custom_name": acc.custom_name or "",
                    "note": acc.note or "",
                    "group_ids": group_ids_by_account.get(acc.account_id, []),
                    "timezone_name": acc.timezone_name,
                    "currency": account_currency,
                    "account_status": acc.account_status,
                    "status_label": "Ошибка синхронизации",
                    "rules_enabled": acc.rules_enabled,
                    "spend": 0.0,
                    "clicks": 0,
                    "impressions": 0,
                    "reach": 0,
                    "frequency": None,
                    "cpm": None,
                    "unique_clicks": 0,
                    "link_clicks": 0,
                    "outbound_clicks": 0,
                    "landing_page_views": 0,
                    "leads": 0,
                    "registrations": 0,
                    "purchases": 0,
                    "cost_per_lead": None,
                    "cost_per_registration": None,
                    "cost_per_purchase": None,
                    "cpc": 0.0,
                    "ctr": 0.0,
                    "cpc_link": None,
                    "ctr_link": None,
                    "ctr_outbound": None,
                    "cost_per_landing_page_view": None,
                    "adsets": [],
                    "has_error": not is_blocked,
                    "is_banned": is_blocked,
                    "data_status": "blocked" if is_blocked else "error",
                    "data_status_label": (
                        "Исторические метрики недоступны для текущего статуса кабинета"
                        if is_blocked
                        else "Meta не вернула метрики"
                    ),
                })

        currency_totals = [
            _currency_total_payload(currency, currency_buckets[currency])
            for currency in sorted(currency_buckets)
        ]
        mixed_currencies = len(currency_totals) > 1
        display_currency = (
            currency_totals[0]["currency"]
            if len(currency_totals) == 1
            and currency_totals[0]["currency"] != UNKNOWN_CURRENCY
            else ""
        )
        monetary_totals_available = bool(display_currency)
        avg_cpc = (
            (total_spend / total_clicks) if total_clicks > 0 else 0.0
        ) if monetary_totals_available else None
        avg_ctr = ((total_clicks / total_impressions) * 100) if total_impressions > 0 else 0.0
        avg_frequency = (total_impressions / total_reach) if total_reach > 0 else None
        avg_cpm = (
            ((total_spend / total_impressions) * 1000)
            if total_impressions > 0 else None
        ) if monetary_totals_available else None
        avg_cpc_link = (
            _cost_or_none(total_spend, total_link_clicks)
            if monetary_totals_available else None
        )
        avg_ctr_link = (
            (total_link_clicks / total_impressions) * 100
            if total_impressions > 0 else None
        )
        avg_ctr_outbound = (
            (total_outbound_clicks / total_impressions) * 100
            if total_impressions > 0 else None
        )
        metrics_coverage = round((accounts_synced / len(accounts)) * 100, 1) if accounts else 0.0
        quality_status = "complete" if accounts_synced == len(accounts) else ("partial" if accounts_synced else "unavailable")

        if accounts_synced == 0:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Meta не вернула данные ни по одному кабинету. "
                    "Последний сохранённый снимок не изменён."
                ),
            )

        res_data = {
            "period": period,
            "generated_at": _utc_iso(datetime.now(timezone.utc)),
            "source": "Meta Marketing API",
            "total_spend": round(total_spend, 2) if monetary_totals_available else None,
            "display_currency": display_currency,
            "mixed_currencies": mixed_currencies,
            "currency_totals": currency_totals,
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_reach": total_reach,
            "total_unique_clicks": total_unique_clicks,
            "total_link_clicks": total_link_clicks,
            "total_outbound_clicks": total_outbound_clicks,
            "total_landing_page_views": total_landing_page_views,
            "avg_frequency": round(avg_frequency, 2) if avg_frequency is not None else None,
            "avg_cpm": round(avg_cpm, 2) if avg_cpm is not None else None,
            "total_leads": total_leads,
            "total_regs": total_regs,
            "total_purchases": total_purchases,
            "avg_cpc": round(avg_cpc, 2) if avg_cpc is not None else None,
            "avg_ctr": round(avg_ctr, 2),
            "avg_cpc_link": avg_cpc_link,
            "avg_ctr_link": round(avg_ctr_link, 2) if avg_ctr_link is not None else None,
            "avg_ctr_outbound": round(avg_ctr_outbound, 2) if avg_ctr_outbound is not None else None,
            "cost_per_landing_page_view": (
                _cost_or_none(total_spend, total_landing_page_views)
                if monetary_totals_available else None
            ),
            "cost_per_lead": (
                _cost_or_none(total_spend, total_leads)
                if monetary_totals_available else None
            ),
            "cost_per_registration": (
                _cost_or_none(total_spend, total_regs)
                if monetary_totals_available else None
            ),
            "cost_per_purchase": (
                _cost_or_none(total_spend, total_purchases)
                if monetary_totals_available else None
            ),
            "accounts_count": len(accounts),
            "accounts": account_results,
            "data_quality": {
                "status": quality_status,
                "accounts_total": len(accounts),
                "accounts_synced": accounts_synced,
                "accounts_failed": accounts_failed,
                "accounts_blocked": accounts_blocked,
                "metrics_coverage_percent": metrics_coverage,
                "monetary_totals_available": monetary_totals_available,
                "currency_issue": (
                    "mixed"
                    if mixed_currencies
                    else "unknown"
                    if not monetary_totals_available
                    else ""
                ),
            },
            "metric_definitions": SUMMARY_METRIC_DEFINITIONS,
        }
        res_data["snapshot"] = await _persist_summary(
            session,
            owner_id=str(user.telegram_id or ""),
            owner_user_id=user.id,
            period=period,
            payload=res_data,
        )
        _summary_cache[cache_key] = (now_ts, res_data)
        return _summary_with_cache_metadata(
            res_data,
            is_cached=False,
            origin="live",
            persisted_at=res_data["snapshot"]["saved_at"],
        )



@router.get("/settings")
async def get_settings(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        runtime_row = (
            await session.execute(
                select(AutomationRuntimeState).where(
                    AutomationRuntimeState.state_key == "monitoring"
                )
            )
        ).scalar_one_or_none()
        runtime = _load_json_object(runtime_row.payload) if runtime_row else {}
        if runtime_row and "updated_at" not in runtime:
            runtime["updated_at"] = _utc_iso(runtime_row.updated_at)
        return {
            "poll_interval_minutes": app_settings.poll_interval_minutes if app_settings else 10,
            "critical_rule_interval_minutes": (
                app_settings.critical_rule_interval_minutes if app_settings else 2
            ),
            "stop_confirmation_minutes": (
                app_settings.stop_confirmation_minutes if app_settings else 10
            ),
            "inventory_cache_minutes": app_settings.inventory_cache_minutes if app_settings else 5,
            "account_health_interval_minutes": (
                app_settings.account_health_interval_minutes if app_settings else 15
            ),
            "max_concurrent_accounts": app_settings.max_concurrent_accounts if app_settings else 3,
            "max_concurrent_actions": app_settings.max_concurrent_actions if app_settings else 3,
            "usage_soft_limit_percent": app_settings.usage_soft_limit_percent if app_settings else 60,
            "usage_hard_limit_percent": app_settings.usage_hard_limit_percent if app_settings else 80,
            "adaptive_polling_enabled": (
                app_settings.adaptive_polling_enabled if app_settings else True
            ),
            "admin_chat_id": settings.ADMIN_CHAT_ID,
            "user_role": user.role,
            "runtime": runtime,
        }


async def _confirm_admin_password(session, user: TelegramUser, password: SecretStr) -> TelegramUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может изменять автоматику.")
    db_user = (
        await session.execute(select(TelegramUser).where(TelegramUser.id == user.id))
    ).scalar_one_or_none()
    if not db_user or not verify_password(password.get_secret_value(), db_user.password_hash):
        raise HTTPException(status_code=403, detail="Неверный пароль учётной записи.")
    return db_user


@router.post("/settings/automation")
async def update_automation_settings(
    payload: AutomationSettingsUpdateRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """Update global polling controls after explicit account-password confirmation."""

    async with async_session_maker() as session:
        await _confirm_admin_password(session, user, payload.current_password)
        app_settings = (
            await session.execute(select(AppSettings).limit(1))
        ).scalar_one_or_none()
        if app_settings is None:
            app_settings = AppSettings()
            session.add(app_settings)

        for field_name in (
            "poll_interval_minutes",
            "critical_rule_interval_minutes",
            "stop_confirmation_minutes",
            "inventory_cache_minutes",
            "account_health_interval_minutes",
            "max_concurrent_accounts",
            "max_concurrent_actions",
            "usage_soft_limit_percent",
            "usage_hard_limit_percent",
            "adaptive_polling_enabled",
        ):
            setattr(app_settings, field_name, getattr(payload, field_name))
        await session.commit()

    return {
        "success": True,
        "message": "Настройки автоматики сохранены",
    }


@router.post("/settings/interval")
async def set_poll_interval(payload: SetIntervalRequest, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        await _confirm_admin_password(session, user, payload.current_password)
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
    rule_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    user: TelegramUser = Depends(get_current_user),
):
    """Return an owner-isolated, filterable audit history for the web UI."""

    filters = []
    if user.role != "admin":
        filters.append(owned_by(AuditEvent, user))
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
                        AuditEvent.reverts_event_id.is_not(None)
                    )
                )
            )
        else:
            filters.append(AuditEvent.status == normalized_status)

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
                        AuditEvent.reverts_event_id.in_(row_ids)
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
            is_reversible
            and reversal_id is None
            and latest_id == row.id
            and event_is_within_undo_window(row)
        )
        if reversal_id is not None:
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
    user: TelegramUser = Depends(get_current_user),
):
    async with async_session_maker() as session:
        try:
            return await undo_audit_action(
                session,
                meta_client=meta_client,
                event_id=event_id,
                actor_type="user",
                actor_id=str(user.telegram_id or user.id),
                owner_id=user.telegram_id,
                owner_user_id=user.id,
                is_admin=user.role == "admin",
            )
        except UndoError as error:
            raise HTTPException(status_code=error.status_code, detail=error.message) from error


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
        currencies = {
            account.account_id: normalize_currency(account.currency)
            for account in user_accounts
        }

        return [
            {
                "id": r.id,
                "account_id": r.account_id,
                "adset_id": r.adset_id,
                "adset_name": r.adset_name,
                "stop_spend": r.stop_spend,
                "currency": currencies.get(r.account_id, UNKNOWN_CURRENCY),
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
        if not account or (user.role != "admin" and not entity_is_owned_by(account, user)):
            raise HTTPException(status_code=403, detail="Доступ запрещен.")

        action_started = time.perf_counter()
        try:
            access_token = await resolve_account_access_token(session, account)
            await meta_client.set_adset_status(adset_id=adset_id, access_token=access_token, status="ACTIVE")
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
        if not account or (user.role != "admin" and not entity_is_owned_by(account, user)):
            raise HTTPException(status_code=403, detail="Доступ запрещен.")

        stopped_entry.is_resolved = True
        session.add(
            build_audit_event(
                account=account,
                event_type="HIDE_STOPPED_NOTIFICATION",
                status="SUCCESS",
                correlation_id=uuid.uuid4().hex,
                category="MANUAL_ACTION",
                action="HIDE_NOTIFICATION",
                message="Карточка выполненной остановки скрыта пользователем. Ad set остался выключенным.",
                before_state={"status": "PAUSED", "is_resolved": False},
                after_state={"status": "PAUSED", "is_resolved": True},
                actor_type="user",
                actor_id=user.telegram_id,
                adset_id=adset_id,
                adset_name=stopped_entry.adset_name,
            )
        )
        await session.commit()
        return {"success": True, "message": "Карточка скрыта. Ad set остался выключенным."}
