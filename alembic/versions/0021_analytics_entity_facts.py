"""Add analytics_entity_daily_facts table for workspace-isolated metric store.

Revision ID: 0021_analytics_entity_facts
Revises: 0020_allowed_emails
Create Date: 2026-08-29 04:30:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0021_analytics_entity_facts"
down_revision: Union[str, None] = "0020_allowed_emails"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    json_type = JSONB if bind.dialect.name == "postgresql" else sa.JSON

    if "analytics_entity_daily_facts" not in table_names:
        op.create_table(
            "analytics_entity_daily_facts",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column(
                "workspace_id",
                sa.Integer(),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("entity_level", sa.String(length=16), nullable=False),
            sa.Column("entity_id", sa.String(length=64), nullable=False),
            sa.Column("entity_name", sa.String(length=255), server_default="", nullable=False),
            sa.Column("parent_entity_id", sa.String(length=64), server_default="", nullable=False),
            sa.Column("date", sa.String(length=10), nullable=False),
            sa.Column("currency", sa.String(length=10), server_default="UNKNOWN", nullable=False),
            sa.Column("spend", sa.Float(), server_default="0.0", nullable=False),
            sa.Column("impressions", sa.Integer(), server_default="0", nullable=False),
            sa.Column("reach", sa.Integer(), server_default="0", nullable=False),
            sa.Column("frequency", sa.Float(), server_default="0.0", nullable=False),
            sa.Column("cpm", sa.Float(), server_default="0.0", nullable=False),
            sa.Column("clicks", sa.Integer(), server_default="0", nullable=False),
            sa.Column("unique_clicks", sa.Integer(), server_default="0", nullable=False),
            sa.Column("link_clicks", sa.Integer(), server_default="0", nullable=False),
            sa.Column("outbound_clicks", sa.Integer(), server_default="0", nullable=False),
            sa.Column("landing_page_views", sa.Integer(), server_default="0", nullable=False),
            sa.Column("cpc", sa.Float(), server_default="0.0", nullable=False),
            sa.Column("cpc_link", sa.Float(), nullable=True),
            sa.Column("ctr", sa.Float(), server_default="0.0", nullable=False),
            sa.Column("ctr_link", sa.Float(), nullable=True),
            sa.Column("ctr_outbound", sa.Float(), nullable=True),
            sa.Column("leads", sa.Integer(), server_default="0", nullable=False),
            sa.Column("registrations", sa.Integer(), server_default="0", nullable=False),
            sa.Column("purchases", sa.Integer(), server_default="0", nullable=False),
            sa.Column("cost_per_lead", sa.Float(), nullable=True),
            sa.Column("cost_per_registration", sa.Float(), nullable=True),
            sa.Column("cost_per_purchase", sa.Float(), nullable=True),
            sa.Column("cost_per_landing_page_view", sa.Float(), nullable=True),
            sa.Column("raw_actions", json_type, server_default="[]", nullable=False),
            sa.Column("status", sa.String(length=32), server_default="UNKNOWN", nullable=False),
            sa.Column("effective_status", sa.String(length=32), server_default="UNKNOWN", nullable=False),
            sa.Column("daily_budget", sa.Float(), server_default="0.0", nullable=False),
            sa.Column(
                "fetched_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "workspace_id",
                "account_id",
                "entity_level",
                "entity_id",
                "date",
                name="uq_analytics_facts_ws_acc_level_entity_date",
            ),
        )
        op.create_index(
            "ix_analytics_facts_ws_date_level",
            "analytics_entity_daily_facts",
            ["workspace_id", "date", "entity_level"],
        )
        op.create_index(
            "ix_analytics_facts_ws_parent_date",
            "analytics_entity_daily_facts",
            ["workspace_id", "parent_entity_id", "date"],
        )
        op.create_index(
            "ix_analytics_facts_acc_date",
            "analytics_entity_daily_facts",
            ["account_id", "date"],
        )
        op.create_index(
            "ix_analytics_facts_workspace_id",
            "analytics_entity_daily_facts",
            ["workspace_id"],
        )
        op.create_index(
            "ix_analytics_facts_account_id",
            "analytics_entity_daily_facts",
            ["account_id"],
        )
        op.create_index(
            "ix_analytics_facts_entity_level",
            "analytics_entity_daily_facts",
            ["entity_level"],
        )
        op.create_index(
            "ix_analytics_facts_entity_id",
            "analytics_entity_daily_facts",
            ["entity_id"],
        )
        op.create_index(
            "ix_analytics_facts_parent_entity_id",
            "analytics_entity_daily_facts",
            ["parent_entity_id"],
        )
        op.create_index(
            "ix_analytics_facts_date",
            "analytics_entity_daily_facts",
            ["date"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "analytics_entity_daily_facts" in set(inspector.get_table_names()):
        op.drop_index("ix_analytics_facts_date", table_name="analytics_entity_daily_facts")
        op.drop_index("ix_analytics_facts_parent_entity_id", table_name="analytics_entity_daily_facts")
        op.drop_index("ix_analytics_facts_entity_id", table_name="analytics_entity_daily_facts")
        op.drop_index("ix_analytics_facts_entity_level", table_name="analytics_entity_daily_facts")
        op.drop_index("ix_analytics_facts_account_id", table_name="analytics_entity_daily_facts")
        op.drop_index("ix_analytics_facts_workspace_id", table_name="analytics_entity_daily_facts")
        op.drop_index("ix_analytics_facts_acc_date", table_name="analytics_entity_daily_facts")
        op.drop_index("ix_analytics_facts_ws_parent_date", table_name="analytics_entity_daily_facts")
        op.drop_index("ix_analytics_facts_ws_date_level", table_name="analytics_entity_daily_facts")
        op.drop_table("analytics_entity_daily_facts")
