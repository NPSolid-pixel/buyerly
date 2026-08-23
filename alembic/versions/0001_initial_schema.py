"""Initial PostgreSQL schema for Buyerly

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-08-23 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_id", sa.String(), unique=True, nullable=True, index=True),
        sa.Column("username", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("full_name", sa.String(), default="", nullable=False),
        sa.Column("first_name", sa.String(), default="", nullable=False),
        sa.Column("last_name", sa.String(), default="", nullable=False),
        sa.Column("email", sa.String(), nullable=True, index=True),
        sa.Column("avatar_url", sa.String(), default="", nullable=False),
        sa.Column("onboarding_step", sa.String(), default="personal_details", nullable=False),
        sa.Column("onboarding_completed", sa.Boolean(), default=False, nullable=False),
        sa.Column("password_hash", sa.String(), default="", nullable=False),
        sa.Column("auth_token", sa.String(), unique=True, nullable=True, index=True),
        sa.Column("role", sa.String(), default="admin", nullable=False),
        sa.Column("is_approved", sa.Boolean(), default=True, nullable=False),
        sa.Column("active_workspace_id", sa.Integer(), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. workspaces
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("badge_text", sa.String(), default="B", nullable=False),
        sa.Column("badge_color", sa.String(), default="#F5A300", nullable=False),
        sa.Column("logo_url", sa.String(), default="", nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Add foreign key from users.active_workspace_id to workspaces.id
    op.create_foreign_key(
        "fk_users_active_workspace",
        "users",
        "workspaces",
        ["active_workspace_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )

    # 3. workspace_members
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", sa.String(), default="owner", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member_user"),
    )

    # 4. workspace_invites
    op.create_table(
        "workspace_invites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(), nullable=True, index=True),
        sa.Column("role", sa.String(), default="buyer", nullable=False),
        sa.Column("inviter_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("status", sa.String(), default="pending", nullable=False, index=True),
        sa.Column("max_uses", sa.Integer(), default=1, nullable=False),
        sa.Column("used_count", sa.Integer(), default=0, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 5. email_verification_codes
    op.create_table(
        "email_verification_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(), nullable=False, index=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("is_used", sa.Boolean(), default=False, nullable=False),
        sa.Column("failed_attempts", sa.Integer(), default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 6. app_settings
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("poll_interval_minutes", sa.Integer(), default=10, nullable=False),
        sa.Column("critical_rule_interval_minutes", sa.Integer(), default=2, nullable=False),
        sa.Column("stop_confirmation_minutes", sa.Integer(), default=10, nullable=False),
        sa.Column("inventory_cache_minutes", sa.Integer(), default=5, nullable=False),
        sa.Column("account_health_interval_minutes", sa.Integer(), default=15, nullable=False),
        sa.Column("max_concurrent_accounts", sa.Integer(), default=3, nullable=False),
        sa.Column("max_concurrent_actions", sa.Integer(), default=3, nullable=False),
        sa.Column("usage_soft_limit_percent", sa.Integer(), default=60, nullable=False),
        sa.Column("usage_hard_limit_percent", sa.Integer(), default=80, nullable=False),
        sa.Column("adaptive_polling_enabled", sa.Boolean(), default=True, nullable=False),
        sa.Column("admin_chat_id", sa.String(), default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 7. automation_runtime_states
    op.create_table(
        "automation_runtime_states",
        sa.Column("state_key", sa.String(), primary_key=True, default="monitoring"),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 8. meta_connections
    op.create_table(
        "meta_connections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("provider_user_id", sa.String(), nullable=False, index=True),
        sa.Column("provider_user_name", sa.String(), default="", nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("granted_scopes", JSONB, nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), default="active", nullable=False, index=True),
        sa.Column("last_error", sa.Text(), default="", nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_user_id", "provider_user_id", name="uq_meta_connection_owner_provider_user"),
    )

    # 9. meta_oauth_states
    op.create_table(
        "meta_oauth_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("state_hash", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("return_path", sa.String(), default="/add-accounts", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 10. meta_connection_assets
    op.create_table(
        "meta_connection_assets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("meta_connections.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("meta_account_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), default="", nullable=False),
        sa.Column("business_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("business_name", sa.String(), default="Без Business Manager", nullable=False),
        sa.Column("account_status", sa.Integer(), default=1, nullable=False),
        sa.Column("currency", sa.String(), default="UNKNOWN", nullable=False),
        sa.Column("timezone_name", sa.String(), default="UTC", nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connection_id", "meta_account_id", name="uq_meta_connection_asset_account"),
    )

    # 11. accounts
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("account_id", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("custom_name", sa.String(), default="", nullable=False),
        sa.Column("note", sa.Text(), default="", nullable=False),
        sa.Column("access_token", sa.String(), nullable=True, default=""),
        sa.Column("meta_connection_id", sa.Integer(), sa.ForeignKey("meta_connections.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("batch_name", sa.String(), default="", nullable=False),
        sa.Column("currency", sa.String(), default="UNKNOWN", nullable=False),
        sa.Column("timezone_name", sa.String(), default="UTC", nullable=False),
        sa.Column("last_started_date", sa.String(), default="", nullable=False),
        sa.Column("last_day_start_date", sa.String(), default="", nullable=False),
        sa.Column("active_rules", sa.Text(), default="[]", nullable=False),
        sa.Column("account_status", sa.Integer(), default=1, nullable=False),
        sa.Column("status_label", sa.String(), default="Активен (ACTIVE)", nullable=False),
        sa.Column("rules_enabled", sa.Boolean(), default=False, nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 12. rule_presets
    op.create_table(
        "rule_presets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("action", sa.String(), default="turn_off", nullable=False),
        sa.Column("conditions", JSONB, nullable=False),
        sa.Column("condition_logic", sa.String(), default="and", nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), default=0, nullable=False),
        sa.Column("check_interval_minutes", sa.Integer(), default=5, nullable=False),
        sa.Column("notify_tg", sa.Boolean(), default=True, nullable=False),
        sa.Column("budget_change_percent", sa.Float(), default=0.0, nullable=False),
        sa.Column("budget_max_daily", sa.Float(), default=0.0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 13. rule_groups
    op.create_table(
        "rule_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), default="", nullable=False),
        sa.Column("position", sa.Integer(), default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 14. rule_group_items
    op.create_table(
        "rule_group_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("rule_groups.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("preset_id", sa.Integer(), sa.ForeignKey("rule_presets.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("position", sa.Integer(), default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "preset_id", name="uq_rule_group_preset"),
    )

    # 15. rule_examples_bootstrap
    op.create_table(
        "rule_examples_bootstrap",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True),
        sa.Column("version", sa.Integer(), default=1, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 16. account_groups
    op.create_table(
        "account_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_account_group_owner_name"),
    )

    # 17. account_group_members
    op.create_table(
        "account_group_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("account_groups.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("position", sa.Integer(), default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "account_id", name="uq_account_group_member"),
    )

    # 18. summary_snapshots
    op.create_table(
        "summary_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("period", sa.String(), nullable=False, index=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("schema_version", sa.Integer(), default=1, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )
    op.create_index(
        "ix_summary_snapshots_user_period_created",
        "summary_snapshots",
        ["owner_user_id", "period", "created_at"],
    )

    # 19. analytics_view_preferences
    op.create_table(
        "analytics_view_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("scope", sa.String(), default="summary", nullable=False, index=True),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_user_id", "scope", name="uq_analytics_view_owner_scope"),
    )

    # 20. stopped_adsets
    op.create_table(
        "stopped_adsets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.String(), nullable=False, index=True),
        sa.Column("adset_id", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("adset_name", sa.String(), nullable=False),
        sa.Column("stop_spend", sa.Float(), nullable=False),
        sa.Column("stop_leads", sa.Integer(), default=0, nullable=False),
        sa.Column("stop_registrations", sa.Integer(), default=0, nullable=False),
        sa.Column("is_resolved", sa.Boolean(), default=False, nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 21. event_logs
    op.create_table(
        "event_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(), nullable=False, index=True),
        sa.Column("target_chat_id", sa.String(), default="", index=True),
        sa.Column("account_id", sa.String(), default="", index=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), default="SUCCESS", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )

    # 22. audit_events
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("actor_type", sa.String(), default="system", nullable=False, index=True),
        sa.Column("actor_id", sa.String(), default="monitoring_worker", nullable=False),
        sa.Column("category", sa.String(), default="RULE_ACTION", nullable=False, index=True),
        sa.Column("event_type", sa.String(), nullable=False, index=True),
        sa.Column("status", sa.String(), default="SUCCESS", nullable=False, index=True),
        sa.Column("account_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("account_name", sa.String(), default="", nullable=False),
        sa.Column("adset_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("adset_name", sa.String(), default="", nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True, index=True),
        sa.Column("rule_name", sa.String(), default="", nullable=False),
        sa.Column("action", sa.String(), default="", nullable=False, index=True),
        sa.Column("message", sa.Text(), default="", nullable=False),
        sa.Column("before_state", JSONB, nullable=False),
        sa.Column("after_state", JSONB, nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("correlation_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("reverts_event_id", sa.Integer(), sa.ForeignKey("audit_events.id"), nullable=True, unique=True, index=True),
        sa.Column("duration_ms", sa.Integer(), default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )

    # 23. automation_schedule_states
    op.create_table(
        "automation_schedule_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("state_key", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("account_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("rule_key", sa.String(), default="", nullable=False, index=True),
        sa.Column("last_checked_at", sa.Float(), default=0.0, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 24. rule_execution_states
    op.create_table(
        "rule_execution_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("execution_key", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("account_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("adset_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("rule_key", sa.String(), default="", nullable=False, index=True),
        sa.Column("action", sa.String(), default="", nullable=False, index=True),
        sa.Column("status", sa.String(), default="IDLE", nullable=False, index=True),
        sa.Column("correlation_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("last_attempt_at", sa.Float(), default=0.0, nullable=False),
        sa.Column("last_success_at", sa.Float(), nullable=True),
        sa.Column("before_state", JSONB, nullable=False),
        sa.Column("after_state", JSONB, nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 25. action_undo_states
    op.create_table(
        "action_undo_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("original_event_id", sa.Integer(), sa.ForeignKey("audit_events.id"), unique=True, nullable=False, index=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("status", sa.String(), default="PENDING", nullable=False, index=True),
        sa.Column("correlation_id", sa.String(), default="", nullable=False, index=True),
        sa.Column("attempt_count", sa.Integer(), default=1, nullable=False),
        sa.Column("expected_state", JSONB, nullable=False),
        sa.Column("desired_state", JSONB, nullable=False),
        sa.Column("last_error", sa.Text(), default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table_name in [
        "action_undo_states",
        "rule_execution_states",
        "automation_schedule_states",
        "audit_events",
        "event_logs",
        "stopped_adsets",
        "analytics_view_preferences",
        "summary_snapshots",
        "account_group_members",
        "account_groups",
        "rule_examples_bootstrap",
        "rule_group_items",
        "rule_groups",
        "rule_presets",
        "accounts",
        "meta_connection_assets",
        "meta_oauth_states",
        "meta_connections",
        "automation_runtime_states",
        "app_settings",
        "email_verification_codes",
        "workspace_invites",
        "workspace_members",
        "workspaces",
        "users",
    ]:
        op.drop_table(table_name)
