"""Add workspace-scoped account health snapshots.

Revision ID: 0018_account_health
Revises: 0017_meta_connection_invites
Create Date: 2026-08-28 10:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0018_account_health"
down_revision: Union[str, None] = "0017_meta_connection_invites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "account_health" in set(inspector.get_table_names()):
        return
    op.create_table(
        "account_health",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("account_pk", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("cause", sa.String(16), nullable=False, server_default="none"),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_pk"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_pk", name="uq_account_health_account_pk"),
    )
    op.create_index("ix_account_health_workspace_id", "account_health", ["workspace_id"])
    op.create_index("ix_account_health_account_pk", "account_health", ["account_pk"])
    op.create_index("ix_account_health_status", "account_health", ["status"])
    op.create_index("ix_account_health_cause", "account_health", ["cause"])
    op.create_index("ix_account_health_last_success_at", "account_health", ["last_success_at"])
    op.create_index("ix_account_health_last_error_at", "account_health", ["last_error_at"])
    op.create_index("ix_account_health_last_checked_at", "account_health", ["last_checked_at"])
    op.create_index("ix_account_health_workspace_status", "account_health", ["workspace_id", "status"])


def downgrade() -> None:
    if "account_health" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("account_health")
