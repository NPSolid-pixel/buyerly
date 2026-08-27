"""Add email_verified_at and unconfirmed_email columns to users table and ensure unique index

Revision ID: 0003_user_email_verified
Revises: 0002_adset_inventory_cache
Create Date: 2026-08-26 17:45:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_user_email_verified"
down_revision: Union[str, None] = "0002_adset_inventory_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add columns if not present
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("unconfirmed_email", sa.String(), nullable=True),
    )

    # 2. Clean empty strings and normalize existing emails
    op.execute("UPDATE users SET email = NULL WHERE email IS NOT NULL AND TRIM(email) = ''")
    op.execute("UPDATE users SET email = LOWER(TRIM(email)) WHERE email IS NOT NULL")

    # Existing targeted links were issued before verified-email enforcement.
    # Revoke them so owners can explicitly reissue fresh invitations.
    op.execute(
        "UPDATE workspace_invites SET status = 'revoked' "
        "WHERE status = 'pending' AND email IS NOT NULL"
    )

    # 3. Deduplicate duplicate emails keeping active user
    op.execute(
        """
        WITH ranked_users AS (
            SELECT id, email,
                   ROW_NUMBER() OVER (
                       PARTITION BY email
                       ORDER BY (CASE WHEN active_workspace_id IS NOT NULL THEN 1 ELSE 0 END) DESC, id DESC
                   ) as rn
            FROM users
            WHERE email IS NOT NULL
        )
        UPDATE users
        SET email = NULL
        WHERE id IN (
            SELECT id FROM ranked_users WHERE rn > 1
        )
        """
    )

    # 4. Create indexes
    op.create_index(
        "ix_users_email_verified_at",
        "users",
        ["email_verified_at"],
    )
    op.create_index(
        "ix_users_unconfirmed_email",
        "users",
        ["unconfirmed_email"],
    )
    # Drop previous non-unique index if it exists and replace with unique index
    op.execute("DROP INDEX IF EXISTS ix_users_email")

    op.create_index(
        "ix_users_email",
        "users",
        ["email"],
        unique=True,
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_email")
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.drop_index("ix_users_unconfirmed_email", table_name="users")
    op.drop_index("ix_users_email_verified_at", table_name="users")
    op.drop_column("users", "unconfirmed_email")
    op.drop_column("users", "email_verified_at")
