import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import and_, delete, func, or_, select

from api.schemas import (
    AccountGroupItem,
    ConditionItem,
    RuleGroupResponse,
    RulePresetItem,
    WorkspaceItem,
)
from bot.handlers import get_short_account_label
from core.metrics import (
    cost_per_event,
    normalize_rule_conditions,
    normalize_runtime_rule,
    validate_public_rule_conditions,
    validate_rule_set_compatibility,
    validate_runtime_rule,
)
from core.ownership import owned_by, owned_by_ids
from database.db import verify_password
from database.models import (
    Account,
    AccountGroup,
    AccountGroupMember,
    AnalyticsViewPreference,
    AuditEvent,
    RuleGroup,
    RuleGroupItem,
    RulePreset,
    SummarySnapshot,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceSupportGrant,
)

logger = logging.getLogger(__name__)

# ----------------------------------------------------
# In-memory summary cache & constants
# ----------------------------------------------------
_summary_cache: Dict[str, Any] = {}
SUMMARY_CACHE_TTL = 120  # 2 minutes cache


def invalidate_summary_cache(
    workspace_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
) -> None:
    """Clear memory summary cache for specific workspace, user, or all."""
    if workspace_id is None and owner_user_id is None:
        _summary_cache.clear()
        return
    stale_keys = []
    for k in list(_summary_cache.keys()):
        workspace_match = workspace_id is not None and (
            k == f"ws:{workspace_id}" or k.startswith(f"ws:{workspace_id}:")
        )
        owner_match = owner_user_id is not None and (
            k.startswith(f"user:{owner_user_id}:") or k.startswith(f"{owner_user_id}:")
        )
        if workspace_match or owner_match:
            stale_keys.append(k)
    for k in stale_keys:
        _summary_cache.pop(k, None)


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
# Workspace Helpers & Scoping
# ----------------------------------------------------
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


def slugify(text: str) -> str:
    """Generate a clean URL slug from name."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text or "workspace"


async def _active_support_grant(
    session,
    user_id: int,
    workspace_id: int,
) -> Optional[WorkspaceSupportGrant]:
    now = datetime.now(timezone.utc)
    return (
        await session.execute(
            select(WorkspaceSupportGrant).where(
                WorkspaceSupportGrant.workspace_id == workspace_id,
                WorkspaceSupportGrant.user_id == user_id,
                WorkspaceSupportGrant.expires_at > now,
                WorkspaceSupportGrant.revoked_at.is_(None),
            )
        )
    ).scalars().first()


async def record_security_event_and_raise(
    session,
    *,
    status_code: int = 403,
    detail: str,
    user: User,
    workspace_id: Optional[int] = None,
    action: str = "UNAUTHORIZED_ACCESS",
    resource_type: str = "workspace",
    resource_id: str = "",
) -> None:
    """Atomically commit a security audit event before raising HTTPException."""
    try:
        audit_event = AuditEvent(
            workspace_id=workspace_id,
            owner_user_id=user.id,
            actor_type="user",
            actor_id=str(user.telegram_id or user.id),
            category="SECURITY",
            event_type="UNAUTHORIZED_ACCESS_ATTEMPT",
            status="BLOCKED",
            action=action,
            message=detail,
            details={"resource_type": resource_type, "resource_id": str(resource_id)},
        )
        session.add(audit_event)
        await session.commit()
    except Exception as e:
        logger.error("Failed to commit security audit event: %s", e)
        try:
            await session.rollback()
        except Exception:
            pass
    raise HTTPException(status_code=status_code, detail=detail)


async def get_user_workspace(
    session,
    user: User,
    workspace_id: Optional[int] = None,
    slug: Optional[str] = None,
) -> Optional[Workspace]:
    """Resolve the active workspace for the user, strictly verifying membership or active support grant."""
    if slug:
        ws = (await session.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one_or_none()
        if ws:
            member = (
                await session.execute(
                    select(WorkspaceMember).where(
                        WorkspaceMember.workspace_id == ws.id,
                        WorkspaceMember.user_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if member:
                return ws
            if user.role == "admin":
                grant = await _active_support_grant(session, user.id, ws.id)
                if grant:
                    return ws
            return None
        return None

    if workspace_id:
        member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if member:
            return (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        if user.role == "admin":
            grant = await _active_support_grant(session, user.id, workspace_id)
            if grant:
                return (await session.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
        return None

    # Check user.active_workspace_id with strict membership or support grant
    if user.active_workspace_id:
        member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == user.active_workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if member:
            ws = (await session.execute(select(Workspace).where(Workspace.id == user.active_workspace_id))).scalar_one_or_none()
            if ws:
                return ws
        elif user.role == "admin":
            grant = await _active_support_grant(session, user.id, user.active_workspace_id)
            if grant:
                ws = (await session.execute(select(Workspace).where(Workspace.id == user.active_workspace_id))).scalar_one_or_none()
                if ws:
                    return ws

    # Fallback to first legitimate membership
    first_member = (
        await session.execute(
            select(WorkspaceMember).where(WorkspaceMember.user_id == user.id).limit(1)
        )
    ).scalar_one_or_none()
    if first_member:
        ws = (await session.execute(select(Workspace).where(Workspace.id == first_member.workspace_id))).scalar_one_or_none()
        if ws:
            db_user = (
                await session.execute(select(User).where(User.id == user.id))
            ).scalar_one_or_none()
            if db_user is not None:
                db_user.active_workspace_id = ws.id
                await session.commit()
            user.active_workspace_id = ws.id
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
        owner_user_id=user.id,
    )
    session.add(ws)
    await session.flush()

    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    session.add(member)
    db_user = (
        await session.execute(select(User).where(User.id == user.id))
    ).scalar_one_or_none()
    if db_user is not None:
        db_user.active_workspace_id = ws.id
    user.active_workspace_id = ws.id
    await session.commit()
    return ws


async def get_user_workspaces_list(session, user: User) -> List[WorkspaceItem]:
    """Return all workspaces accessible to the user with live stats, including active support grants."""
    active_ws = await get_user_workspace(session, user)
    active_id = active_ws.id if active_ws else None

    # 1. Direct memberships
    rows = (
        await session.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user.id)
            .order_by(Workspace.id.asc())
        )
    ).all()

    workspaces_dict = {}
    for ws, role in rows:
        workspaces_dict[ws.id] = (ws, role)

    # 2. Active support grants for platform admins
    if user.role == "admin":
        now = datetime.now(timezone.utc)
        grants = (
            await session.execute(
                select(Workspace, WorkspaceSupportGrant.role)
                .join(WorkspaceSupportGrant, WorkspaceSupportGrant.workspace_id == Workspace.id)
                .where(
                    WorkspaceSupportGrant.user_id == user.id,
                    WorkspaceSupportGrant.expires_at > now,
                    WorkspaceSupportGrant.revoked_at.is_(None),
                )
            )
        ).all()
        for ws, role in grants:
            if ws.id not in workspaces_dict:
                workspaces_dict[ws.id] = (ws, role or "admin")

    items = []
    for ws_id, (ws, role) in sorted(workspaces_dict.items(), key=lambda x: x[0]):
        acc_count = (
            await session.execute(
                select(func.count()).select_from(Account).where(Account.workspace_id == ws.id)
            )
        ).scalar_one() or 0
        mem_count = (
            await session.execute(
                select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id)
            )
        ).scalar_one() or 1

        items.append(
            WorkspaceItem(
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
            )
        )
    return items


async def get_user_workspace_member(
    session,
    user: User,
    workspace_id: Optional[int] = None,
) -> tuple[Optional[Workspace], Optional[WorkspaceMember]]:
    """Resolve active workspace and user membership with role, supporting active support grants."""
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
    if not member and user.role == "admin":
        grant = await _active_support_grant(session, user.id, ws.id)
        if grant:
            member = WorkspaceMember(
                workspace_id=ws.id,
                user_id=user.id,
                role=grant.role or "admin",
            )
    return ws, member


def ensure_workspace_write_access(
    user: User,
    member: Optional[WorkspaceMember],
    action_description: str = "изменения данных",
) -> None:
    """Ensure user has write access to workspace (owner, admin, buyer). Viewers and non-members are blocked."""
    if not member:
        raise HTTPException(status_code=403, detail=f"Нет доступа к воркспейсу для {action_description}")
    if member.role == "viewer":
        raise HTTPException(
            status_code=403,
            detail=f"Роль Наблюдатель (Viewer) имеет доступ только для чтения и не может выполнять {action_description}",
        )


# ----------------------------------------------------
# Account & AccountGroup Helpers
# ----------------------------------------------------
async def get_user_accounts(session, user: User, workspace_id: Optional[int] = None) -> List[Account]:
    ws = await get_user_workspace(session, user, workspace_id=workspace_id)
    if not ws:
        return []

    stmt = (
        select(Account)
        .where(
            or_(
                Account.workspace_id == ws.id,
                and_(Account.workspace_id.is_(None), owned_by(Account, user)),
            )
        )
        .order_by(Account.id.desc())
    )
    res = await session.execute(stmt)
    accounts = res.scalars().all()

    # Auto-backfill workspace_id for legacy unassigned accounts owned by this user in their active workspace
    needs_flush = False
    for acc in accounts:
        if acc.workspace_id is None and acc.owner_user_id == user.id:
            acc.workspace_id = ws.id
            needs_flush = True
    if needs_flush:
        try:
            await session.flush()
        except Exception:
            pass

    return accounts


async def _account_group_ids_by_account(
    session,
    user: User,
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
    user: User,
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
    user: User,
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


async def _ensure_stable_account_owner(session, account: Account) -> None:
    pass


# ----------------------------------------------------
# Rule & Preset Helpers
# ----------------------------------------------------
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
        raw_conditions = (
            json.loads(preset.conditions)
            if isinstance(preset.conditions, str)
            else preset.conditions or []
        )
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
    user: User,
    preset_ids: List[int],
    *,
    owner_user_id: Optional[int] = None,
    owner_id: str = "",
) -> List[RulePreset]:
    ordered_ids = _unique_preset_ids(preset_ids)
    target_user_id = owner_user_id if owner_user_id is not None else getattr(user, "id", None)
    owner_clause = (
        RulePreset.owner_user_id == target_user_id
        if target_user_id is not None
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
# Summary & Analytics Helpers
# ----------------------------------------------------
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
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        **payload,
        "cache": {
            "is_cached": is_cached,
            "age_seconds": round(max(0.0, age_seconds), 1),
            "ttl_seconds": SUMMARY_CACHE_TTL,
            "origin": origin,
            "persisted_at": persisted_at,
            "workspace_id": workspace_id,
        },
    }


def _summary_owner_key(user: User, workspace_id: Optional[int] = None) -> str:
    ws_id = workspace_id if workspace_id is not None else getattr(user, "active_workspace_id", None)
    return f"ws:{ws_id}" if ws_id is not None else f"user:{user.id}"


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
    user: User,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Overlay live Buyerly labels and groups on cached Meta metric rows."""
    accounts = await get_user_accounts(session, user, workspace_id=workspace_id)
    accounts_by_id = {account.account_id: account for account in accounts}
    group_ids_by_account = await _account_group_ids_by_account(session, user, workspace_id=workspace_id)
    rows = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
    enriched_rows = []
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        account_id = str(row.get("account_id") or "")
        account = accounts_by_id.get(account_id)
        if account is None:
            # Strictly drop accounts not present in the current workspace
            continue
        row["name"] = account.name
        row["short_name"] = get_short_account_label(account.name, account.account_id)
        row["custom_name"] = account.custom_name or ""
        row["note"] = account.note or ""
        row["group_ids"] = group_ids_by_account.get(account_id, [])
        enriched_rows.append(row)
    return {**payload, "accounts": enriched_rows, "accounts_count": len(enriched_rows)}


async def _load_persisted_summary(
    session,
    *,
    period: str,
    workspace_id: Optional[int] = None,
    current_account_ids: Optional[Any] = None,
    owner_user_id: Optional[int] = None,
    owner_id: str = "",
) -> Optional[Dict[str, Any]]:
    where_clause = (
        SummarySnapshot.workspace_id == workspace_id
        if workspace_id is not None
        else and_(SummarySnapshot.owner_user_id == owner_user_id, SummarySnapshot.workspace_id.is_(None))
    )
    rows = (
        await session.execute(
            select(SummarySnapshot)
            .where(
                where_clause,
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
    if current_account_ids is not None:
        snapshot_accounts = latest_payload.get("accounts")
        if isinstance(snapshot_accounts, list):
            snapshot_account_ids = {
                str(a.get("account_id") or "").strip()
                for a in snapshot_accounts
                if isinstance(a, dict) and str(a.get("account_id") or "").strip()
            }
            target_ids = set(current_account_ids)
            if snapshot_account_ids != target_ids:
                # Account membership has changed, snapshot is stale!
                return None

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
        workspace_id=latest_row.workspace_id,
    )


async def _persist_summary(
    session,
    *,
    period: str,
    payload: Dict[str, Any],
    workspace_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    owner_id: str = "",
) -> Dict[str, Any]:
    where_clause = (
        SummarySnapshot.workspace_id == workspace_id
        if workspace_id is not None
        else and_(SummarySnapshot.owner_user_id == owner_user_id, SummarySnapshot.workspace_id.is_(None))
    )
    previous_rows = (
        await session.execute(
            select(SummarySnapshot)
            .where(
                where_clause,
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
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        period=period,
        payload=stored_payload,
        generated_at=generated_at,
    )
    session.add(snapshot)
    await session.flush()

    stale_ids = (
        await session.execute(
            select(SummarySnapshot.id)
            .where(
                where_clause,
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


# ----------------------------------------------------
# Settings Helpers
# ----------------------------------------------------
async def _confirm_admin_password(session, user: User, password: SecretStr) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может изменять автоматику.")
    db_user = (
        await session.execute(select(User).where(User.id == user.id))
    ).scalar_one_or_none()
    if not db_user or not verify_password(password.get_secret_value(), db_user.password_hash):
        raise HTTPException(status_code=403, detail="Неверный пароль учётной записи.")
    return db_user
