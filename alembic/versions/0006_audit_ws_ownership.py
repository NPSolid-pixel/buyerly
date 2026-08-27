"""Normalize audit event ownership for workspace-scoped writes.

Revision ID: 0006_audit_ws_ownership
Revises: 0005_workspace_support_grants
Create Date: 2026-08-27 15:45:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_audit_ws_ownership"
down_revision: Union[str, None] = "0005_workspace_support_grants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    audit_columns = {
        column["name"]: column
        for column in inspector.get_columns("audit_events")
    }

    owner_column = audit_columns.get("owner_id")
    if owner_column and not owner_column.get("nullable", True):
        op.alter_column(
            "audit_events",
            "owner_id",
            existing_type=owner_column["type"],
            nullable=True,
        )

    op.execute(
        "UPDATE audit_events AS event "
        "SET owner_user_id = account.owner_user_id "
        "FROM accounts AS account "
        "WHERE event.owner_user_id IS NULL "
        "AND event.account_id = account.account_id "
        "AND account.owner_user_id IS NOT NULL"
    )
    op.execute(
        "UPDATE audit_events AS event "
        "SET workspace_id = account.workspace_id "
        "FROM accounts AS account "
        "WHERE event.workspace_id IS NULL "
        "AND event.account_id = account.account_id "
        "AND account.workspace_id IS NOT NULL"
    )


def downgrade() -> None:
    # Historical rows created after this migration intentionally have no
    # legacy owner_id. Restoring NOT NULL would make downgrade destructive.
    pass
