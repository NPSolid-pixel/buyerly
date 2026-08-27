"""Add reconnect_connection_id to meta_oauth_states.

Revision ID: 0015_meta_oauth_reconnect_id
Revises: 0014_legacy_columns_nullable
Create Date: 2026-08-28 03:50:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_meta_oauth_reconnect_id"
down_revision: Union[str, None] = "0014_legacy_columns_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("meta_oauth_states"):
        cols = {c["name"]: c for c in inspector.get_columns("meta_oauth_states")}
        if "reconnect_connection_id" not in cols:
            op.add_column(
                "meta_oauth_states",
                sa.Column(
                    "reconnect_connection_id",
                    sa.Integer(),
                    sa.ForeignKey("meta_connections.id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )
            op.create_index(
                "ix_meta_oauth_states_reconnect_connection_id",
                "meta_oauth_states",
                ["reconnect_connection_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("meta_oauth_states"):
        cols = {c["name"]: c for c in inspector.get_columns("meta_oauth_states")}
        if "reconnect_connection_id" in cols:
            op.drop_index("ix_meta_oauth_states_reconnect_connection_id", table_name="meta_oauth_states")
            op.drop_column("meta_oauth_states", "reconnect_connection_id")
