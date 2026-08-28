"""Add meta_connection_invites table and invite_id to meta_oauth_states.

Revision ID: 0017_meta_connection_invites
Revises: 0016_meta_connection_ws_scope
Create Date: 2026-08-28 08:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_meta_connection_invites"
down_revision: Union[str, None] = "0016_meta_connection_ws_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    # 1. Create meta_connection_invites table (idempotent)
    if "meta_connection_invites" not in table_names:
        op.create_table(
            "meta_connection_invites",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("workspace_id", sa.Integer(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("token_prefix", sa.String(20), nullable=False),
            sa.Column("label", sa.String(255), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("connected_meta_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.ForeignKeyConstraint(
                ["workspace_id"],
                ["workspaces.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["created_by_user_id"],
                ["users.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["connected_meta_id"],
                ["meta_connections.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash", name="uq_meta_connection_invites_token_hash"),
        )
        op.create_index(
            "ix_meta_connection_invites_workspace_id",
            "meta_connection_invites",
            ["workspace_id"],
        )
        op.create_index(
            "ix_meta_connection_invites_created_by_user_id",
            "meta_connection_invites",
            ["created_by_user_id"],
        )
        op.create_index(
            "ix_meta_connection_invites_token_hash",
            "meta_connection_invites",
            ["token_hash"],
            unique=True,
        )
        op.create_index(
            "ix_meta_connection_invites_status",
            "meta_connection_invites",
            ["status"],
        )
        op.create_index(
            "ix_meta_connection_invites_expires_at",
            "meta_connection_invites",
            ["expires_at"],
        )
        op.create_index(
            "ix_meta_connection_invites_connected_meta_id",
            "meta_connection_invites",
            ["connected_meta_id"],
        )

    # 2. Add invite_id FK column to meta_oauth_states (idempotent)
    if "meta_oauth_states" in table_names:
        cols = {c["name"] for c in inspector.get_columns("meta_oauth_states")}
        if "invite_id" not in cols:
            op.add_column(
                "meta_oauth_states",
                sa.Column(
                    "invite_id",
                    sa.Integer(),
                    nullable=True,
                ),
            )
            op.create_foreign_key(
                "fk_meta_oauth_states_invite_id",
                "meta_oauth_states",
                "meta_connection_invites",
                ["invite_id"],
                ["id"],
                ondelete="SET NULL",
            )
            op.create_index(
                "ix_meta_oauth_states_invite_id",
                "meta_oauth_states",
                ["invite_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    # Remove invite_id from meta_oauth_states
    if "meta_oauth_states" in table_names:
        cols = {c["name"] for c in inspector.get_columns("meta_oauth_states")}
        if "invite_id" in cols:
            op.drop_index("ix_meta_oauth_states_invite_id", table_name="meta_oauth_states")
            op.drop_constraint(
                "fk_meta_oauth_states_invite_id",
                "meta_oauth_states",
                type_="foreignkey",
            )
            op.drop_column("meta_oauth_states", "invite_id")

    # Drop meta_connection_invites table
    if "meta_connection_invites" in table_names:
        op.drop_table("meta_connection_invites")
