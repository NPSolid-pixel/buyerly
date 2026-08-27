"""Make legacy owner_id and access_token columns nullable with default empty string.

Revision ID: 0014_legacy_columns_nullable
Revises: 0013_manual_tokens
Create Date: 2026-08-28 02:50:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_legacy_columns_nullable"
down_revision: Union[str, None] = "0013_manual_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES_WITH_OWNER_ID = [
    "accounts",
    "rule_presets",
    "rule_groups",
    "analytics_view_preferences",
    "automation_schedule_states",
    "rule_execution_states",
    "summary_snapshots",
    "action_undo_states",
    "rule_examples_bootstrap",
    "account_groups",
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name in TABLES_WITH_OWNER_ID:
        if inspector.has_table(table_name):
            cols = {c["name"]: c for c in inspector.get_columns(table_name)}
            if "owner_id" in cols:
                op.alter_column(
                    table_name,
                    "owner_id",
                    nullable=True,
                    server_default="",
                )

    if inspector.has_table("accounts"):
        cols = {c["name"]: c for c in inspector.get_columns("accounts")}
        if "access_token" in cols:
            op.alter_column(
                "accounts",
                "access_token",
                nullable=True,
                server_default="",
            )


def downgrade() -> None:
    pass
