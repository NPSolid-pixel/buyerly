"""Enforce workspace scope for Meta connections and OAuth states.

Revision ID: 0016_meta_connection_ws_scope
Revises: 0015_meta_oauth_reconnect_id
Create Date: 2026-08-28 05:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_meta_connection_ws_scope"
down_revision: Union[str, None] = "0015_meta_oauth_reconnect_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    # 1. Update meta_oauth_states: add workspace_id column with FK and index
    if "meta_oauth_states" in table_names:
        cols = {c["name"]: c for c in inspector.get_columns("meta_oauth_states")}
        if "workspace_id" not in cols:
            op.add_column(
                "meta_oauth_states",
                sa.Column(
                    "workspace_id",
                    sa.Integer(),
                    sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                    nullable=True,
                ),
            )
            # Backfill existing oauth states from user active workspace or membership
            bind.execute(
                sa.text(
                    """
                    UPDATE meta_oauth_states AS s
                    SET workspace_id = COALESCE(
                        u.active_workspace_id,
                        (SELECT wm.workspace_id FROM workspace_members wm WHERE wm.user_id = s.owner_user_id ORDER BY wm.id ASC LIMIT 1),
                        (SELECT w.id FROM workspaces w WHERE w.owner_user_id = s.owner_user_id ORDER BY w.id ASC LIMIT 1)
                    )
                    FROM users u
                    WHERE s.workspace_id IS NULL AND s.owner_user_id = u.id
                    """
                )
            )
            # Delete any unresolvable orphaned states
            bind.execute(sa.text("DELETE FROM meta_oauth_states WHERE workspace_id IS NULL"))
            op.alter_column("meta_oauth_states", "workspace_id", nullable=False)
            op.create_index(
                "ix_meta_oauth_states_workspace_id",
                "meta_oauth_states",
                ["workspace_id"],
            )

    # 2. Update meta_connections: backfill workspace_id, alter to NOT NULL, update unique constraint
    if "meta_connections" in table_names:
        # Backfill any null workspace_id
        bind.execute(
            sa.text(
                """
                UPDATE meta_connections AS conn
                SET workspace_id = COALESCE(
                    u.active_workspace_id,
                    (SELECT wm.workspace_id FROM workspace_members wm WHERE wm.user_id = conn.owner_user_id ORDER BY wm.id ASC LIMIT 1),
                    (SELECT w.id FROM workspaces w WHERE w.owner_user_id = conn.owner_user_id ORDER BY w.id ASC LIMIT 1)
                )
                FROM users u
                WHERE conn.workspace_id IS NULL AND conn.owner_user_id = u.id
                """
            )
        )
        # Delete unresolvable orphaned connections
        bind.execute(sa.text("DELETE FROM meta_connections WHERE workspace_id IS NULL"))

        # Deduplicate any (workspace_id, provider_user_id) keeping latest
        bind.execute(
            sa.text(
                """
                DELETE FROM meta_connections
                WHERE id NOT IN (
                    SELECT DISTINCT ON (workspace_id, provider_user_id) id
                    FROM meta_connections
                    ORDER BY workspace_id, provider_user_id, updated_at DESC, id DESC
                )
                """
            )
        )

        op.alter_column("meta_connections", "workspace_id", nullable=False)

        # Drop old constraint if present
        op.execute(
            sa.text(
                "ALTER TABLE meta_connections "
                "DROP CONSTRAINT IF EXISTS uq_meta_connection_owner_provider_user"
            )
        )
        # Drop old index if present
        op.execute(
            sa.text(
                "DROP INDEX IF EXISTS uq_meta_connection_owner_provider_user"
            )
        )

        # Create new unique constraint on (workspace_id, provider_user_id)
        op.create_unique_constraint(
            "uq_meta_connections_workspace_provider_user",
            "meta_connections",
            ["workspace_id", "provider_user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "meta_connections" in table_names:
        op.execute(
            sa.text(
                "ALTER TABLE meta_connections "
                "DROP CONSTRAINT IF EXISTS uq_meta_connections_workspace_provider_user"
            )
        )
        op.alter_column("meta_connections", "workspace_id", nullable=True)
        op.create_unique_constraint(
            "uq_meta_connection_owner_provider_user",
            "meta_connections",
            ["owner_user_id", "provider_user_id"],
        )

    if "meta_oauth_states" in table_names:
        cols = {c["name"]: c for c in inspector.get_columns("meta_oauth_states")}
        if "workspace_id" in cols:
            op.drop_index("ix_meta_oauth_states_workspace_id", table_name="meta_oauth_states")
            op.drop_column("meta_oauth_states", "workspace_id")
