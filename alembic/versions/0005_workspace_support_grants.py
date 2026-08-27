"""Add temporary workspace support grants.

Revision ID: 0005_workspace_support_grants
Revises: 0004_ws_summary_isolation
Create Date: 2026-08-27 12:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_workspace_support_grants"
down_revision: Union[str, None] = "0004_ws_summary_isolation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_support_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="admin"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_support_grants_workspace_id",
        "workspace_support_grants",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_support_grants_user_id",
        "workspace_support_grants",
        ["user_id"],
    )
    op.create_index(
        "ix_workspace_support_grants_expires_at",
        "workspace_support_grants",
        ["expires_at"],
    )
    op.create_index(
        "ix_workspace_support_grants_revoked_at",
        "workspace_support_grants",
        ["revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_support_grants_revoked_at", table_name="workspace_support_grants")
    op.drop_index("ix_workspace_support_grants_expires_at", table_name="workspace_support_grants")
    op.drop_index("ix_workspace_support_grants_user_id", table_name="workspace_support_grants")
    op.drop_index("ix_workspace_support_grants_workspace_id", table_name="workspace_support_grants")
    op.drop_table("workspace_support_grants")
