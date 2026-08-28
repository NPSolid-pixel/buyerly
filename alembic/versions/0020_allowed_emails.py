"""Add allowed_emails table for access whitelist and backfill existing approved users.

Revision ID: 0020_allowed_emails
Revises: 0019_runtime_payload_jsonb
Create Date: 2026-08-28 22:50:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_allowed_emails"
down_revision: Union[str, None] = "0019_runtime_payload_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "allowed_emails" not in table_names:
        op.create_table(
            "allowed_emails",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("added_by", sa.String(length=128), nullable=True),
            sa.Column("comment", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_allowed_emails_email",
            "allowed_emails",
            ["email"],
            unique=True,
        )

    # Backfill approved users' emails into allowed_emails if users table exists
    if "users" in table_names:
        if bind.dialect.name == "postgresql":
            bind.execute(
                sa.text(
                    """
                    INSERT INTO allowed_emails (email, added_by, comment, created_at)
                    SELECT DISTINCT lower(email), 'migration_backfill', 'Auto-imported active user', NOW()
                    FROM users
                    WHERE email IS NOT NULL
                      AND trim(email) != ''
                      AND is_approved = true
                    ON CONFLICT (email) DO NOTHING
                    """
                )
            )
        else:
            bind.execute(
                sa.text(
                    """
                    INSERT OR IGNORE INTO allowed_emails (email, added_by, comment, created_at)
                    SELECT DISTINCT lower(email), 'migration_backfill', 'Auto-imported active user', CURRENT_TIMESTAMP
                    FROM users
                    WHERE email IS NOT NULL
                      AND trim(email) != ''
                      AND is_approved = 1
                    """
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "allowed_emails" in set(inspector.get_table_names()):
        op.drop_table("allowed_emails")
