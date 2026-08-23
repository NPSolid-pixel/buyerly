"""Add adset_inventory_cache table

Revision ID: 0002_adset_inventory_cache
Revises: 0001_initial_schema
Create Date: 2026-08-23 23:30:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0002_adset_inventory_cache"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "adset_inventory_cache",
        sa.Column("account_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("adsets_payload", JSONB, nullable=False),
        sa.Column("version", sa.Integer(), default=1, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_adset_inventory_cache_account_id",
        "adset_inventory_cache",
        ["account_id"],
    )
    op.create_index(
        "ix_adset_inventory_cache_expires_at",
        "adset_inventory_cache",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_adset_inventory_cache_expires_at", table_name="adset_inventory_cache")
    op.drop_index("ix_adset_inventory_cache_account_id", table_name="adset_inventory_cache")
    op.drop_table("adset_inventory_cache")
