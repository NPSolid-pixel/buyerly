"""API router aggregation and backward compatibility exports."""

import logging
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

import database.db
from api.deps import (
    RESERVED_WORKSPACE_SLUGS,
    SUMMARY_CACHE_TTL,
    SUMMARY_COLUMN_MAX_WIDTH,
    SUMMARY_COLUMN_MIN_WIDTH,
    SUMMARY_DEFAULT_COLUMN_WIDTHS,
    SUMMARY_FILTER_KEYS,
    SUMMARY_FILTER_STATUSES,
    SUMMARY_REQUIRED_COLUMNS,
    SUMMARY_SNAPSHOT_RETENTION,
    SUMMARY_TABLE_COLUMNS,
    SUMMARY_VIEW_MODES,
    SUMMARY_VIEW_PERIODS,
    SUMMARY_VIEW_SCOPE,
    _account_group_ids_by_account,
    _account_group_items,
    _analytics_view_response,
    _clean_rule_group_name,
    _confirm_admin_password,
    _cost_or_none,
    _currency_total_payload,
    _enrich_summary_account_metadata,
    _ensure_compatible_presets,
    _ensure_compatible_rule_set,
    _ensure_stable_account_owner,
    _get_owned_presets,
    _latest_account_metrics_by_id,
    _load_active_rules,
    _load_group_presets,
    _load_json_object,
    _load_persisted_summary,
    _normalize_summary_view_config,
    _persist_summary,
    _preset_response,
    _preset_snapshot,
    _rule_group_response,
    _summary_cache,
    _summary_owner_key,
    _summary_snapshot_reference,
    _summary_with_cache_metadata,
    _unique_preset_ids,
    _utc_iso,
    _validate_account_group_members,
    _validated_condition_payloads,
    ensure_workspace_write_access,
    get_user_accounts,
    get_user_workspace,
    get_user_workspace_member,
    get_user_workspaces_list,
    invalidate_summary_cache,
    slugify,
)
from api.routers import (
    accounts_router,
    admin_support_router,
    adsets_router,
    audit_router,
    auth_router,
    members_router,
    onboarding_router,
    rules_router,
    settings_router,
    summary_router,
    workspaces_router,
)
from api.schemas import (
    AccountGroupItem,
    AccountGroupRequest,
    AccountItem,
    AccountLatestMetrics,
    AccountProfileUpdateRequest,
    AnalyticsViewPreferenceRequest,
    ApplyPresetRequest,
    AutomationSettingsUpdateRequest,
    BatchAddAccountEntry,
    BatchAddRequest,
    BulkInviteItem,
    ChangePasswordRequest,
    CheckSlugResponse,
    ConditionItem,
    CreatePresetRequest,
    CreateWorkspaceInviteRequest,
    CreateWorkspaceRequest,
    LoginRequest,
    LoginResponse,
    OnboardingBulkInvitesRequest,
    OnboardingBulkInvitesResponse,
    OnboardingStatusResponse,
    OnboardingWorkspaceRequest,
    ParsedAccountItem,
    ParseRawRequest,
    PersonalDetailsRequest,
    PublicInviteInfoResponse,
    RequestTemporaryPasswordRequest,
    RuleGroupResponse,
    RuleGroupsReorderRequest,
    RuleGroupWriteRequest,
    RulePresetItem,
    SetIntervalRequest,
    SwitchWorkspaceRequest,
    TransferOwnershipRequest,
    UpdateMemberRoleRequest,
    UpdateProfileRequest,
    UpdateWorkspaceRequest,
    UserProfileResponse,
    WorkspaceInviteItem,
    WorkspaceMemberItem,
    WorkspaceItem,
)
from database.db import async_session_maker
from meta_api.client import MetaClient

logger = logging.getLogger(__name__)

# Master API Router (prefix="/api")
router = APIRouter(prefix="/api")
meta_client = MetaClient()

# Mount all modular domain routers
router.include_router(auth_router)
router.include_router(admin_support_router)
router.include_router(workspaces_router)
router.include_router(members_router)
router.include_router(onboarding_router)
router.include_router(accounts_router)
router.include_router(rules_router)
router.include_router(summary_router)
router.include_router(settings_router)
router.include_router(audit_router)
router.include_router(adsets_router)

# Sync meta_client reference to routers using it
import api.routers.accounts
import api.routers.adsets
import api.routers.audit
import api.routers.summary

api.routers.accounts.meta_client = meta_client
api.routers.adsets.meta_client = meta_client
api.routers.audit.meta_client = meta_client
api.routers.summary.meta_client = meta_client


class _RoutesModule(sys.modules[__name__].__class__):
    """Custom module class to propagate dynamically patched attributes (e.g. in tests) to submodules."""

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name == "async_session_maker":
            import api.deps
            import api.routers.accounts
            import api.routers.adsets
            import api.routers.audit
            import api.routers.auth
            import api.routers.members
            import api.routers.onboarding
            import api.routers.rules
            import api.routers.settings
            import api.routers.summary
            import api.routers.workspaces
            import database.db

            database.db.async_session_maker = value
            api.deps.async_session_maker = value
            api.routers.accounts.async_session_maker = value
            api.routers.adsets.async_session_maker = value
            api.routers.audit.async_session_maker = value
            api.routers.auth.async_session_maker = value
            api.routers.members.async_session_maker = value
            api.routers.onboarding.async_session_maker = value
            api.routers.rules.async_session_maker = value
            api.routers.settings.async_session_maker = value
            api.routers.summary.async_session_maker = value
            api.routers.workspaces.async_session_maker = value
        elif name == "meta_client":
            import api.routers.accounts
            import api.routers.adsets
            import api.routers.audit
            import api.routers.summary

            api.routers.accounts.meta_client = value
            api.routers.adsets.meta_client = value
            api.routers.audit.meta_client = value
            api.routers.summary.meta_client = value


sys.modules[__name__].__class__ = _RoutesModule

__all__ = [
    "router",
    "meta_client",
    "async_session_maker",
    "_summary_cache",
    "invalidate_summary_cache",
    "SUMMARY_CACHE_TTL",
    "SUMMARY_SNAPSHOT_RETENTION",
    "SUMMARY_VIEW_SCOPE",
    "SUMMARY_TABLE_COLUMNS",
    "SUMMARY_REQUIRED_COLUMNS",
    "SUMMARY_VIEW_MODES",
    "SUMMARY_VIEW_PERIODS",
    "SUMMARY_FILTER_KEYS",
    "SUMMARY_FILTER_STATUSES",
    "SUMMARY_COLUMN_MIN_WIDTH",
    "SUMMARY_COLUMN_MAX_WIDTH",
    "SUMMARY_DEFAULT_COLUMN_WIDTHS",
    "slugify",
    "RESERVED_WORKSPACE_SLUGS",
    "get_user_workspace",
    "get_user_workspaces_list",
    "get_user_workspace_member",
    "ensure_workspace_write_access",
    "get_user_accounts",
    "_account_group_ids_by_account",
    "_account_group_items",
    "_validate_account_group_members",
    "_ensure_stable_account_owner",
    "_load_active_rules",
    "_preset_snapshot",
    "_preset_response",
    "_validated_condition_payloads",
    "_ensure_compatible_rule_set",
    "_ensure_compatible_presets",
    "_unique_preset_ids",
    "_clean_rule_group_name",
    "_get_owned_presets",
    "_rule_group_response",
    "_load_group_presets",
    "_load_json_object",
    "_utc_iso",
    "_cost_or_none",
    "_currency_total_payload",
    "_summary_with_cache_metadata",
    "_summary_owner_key",
    "_normalize_summary_view_config",
    "_analytics_view_response",
    "_summary_snapshot_reference",
    "_latest_account_metrics_by_id",
    "_enrich_summary_account_metadata",
    "_load_persisted_summary",
    "_persist_summary",
    "_confirm_admin_password",
    "WorkspaceItem",
    "CreateWorkspaceRequest",
    "UpdateWorkspaceRequest",
    "SwitchWorkspaceRequest",
    "WorkspaceMemberItem",
    "UpdateMemberRoleRequest",
    "TransferOwnershipRequest",
    "CreateWorkspaceInviteRequest",
    "WorkspaceInviteItem",
    "PublicInviteInfoResponse",
    "UserProfileResponse",
    "RequestTemporaryPasswordRequest",
    "LoginRequest",
    "LoginResponse",
    "ChangePasswordRequest",
    "UpdateProfileRequest",
    "OnboardingStatusResponse",
    "PersonalDetailsRequest",
    "CheckSlugResponse",
    "OnboardingWorkspaceRequest",
    "BulkInviteItem",
    "OnboardingBulkInvitesRequest",
    "OnboardingBulkInvitesResponse",
    "ConditionItem",
    "RulePresetItem",
    "CreatePresetRequest",
    "RuleGroupWriteRequest",
    "RuleGroupResponse",
    "RuleGroupsReorderRequest",
    "ApplyPresetRequest",
    "AccountLatestMetrics",
    "AccountItem",
    "AccountProfileUpdateRequest",
    "AccountGroupRequest",
    "AccountGroupItem",
    "ParseRawRequest",
    "ParsedAccountItem",
    "BatchAddAccountEntry",
    "BatchAddRequest",
    "AnalyticsViewPreferenceRequest",
    "SetIntervalRequest",
    "AutomationSettingsUpdateRequest",
]
