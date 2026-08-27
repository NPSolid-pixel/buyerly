"""Add workspace index and isolate summary snapshots

Revision ID: 0004_ws_summary_isolation
Revises: 0003_user_email_verified
Create Date: 2026-08-26 18:50:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_ws_summary_isolation"
down_revision: Union[str, None] = "0003_user_email_verified"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Clean up legacy / orphan snapshots without workspace_id
    op.execute("DELETE FROM summary_snapshots WHERE workspace_id IS NULL")

    # 2. Create composite index on (workspace_id, period, created_at)
    op.create_index(
        "ix_summary_snapshots_workspace_period_created",
        "summary_snapshots",
        ["workspace_id", "period", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_summary_snapshots_workspace_period_created",
        table_name="summary_snapshots",
    )
