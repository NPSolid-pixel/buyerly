"""Make OTP delivery state and consumption explicit and atomic.

Revision ID: 0010_atomic_otp
Revises: 0009_web_sessions
Create Date: 2026-08-27 22:15:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_atomic_otp"
down_revision: Union[str, None] = "0009_web_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        col["name"] for col in sa.inspect(bind).get_columns("email_verification_codes")
    }
    if "code_hash" not in columns:
        op.add_column(
            "email_verification_codes",
            sa.Column("code_hash", sa.String(length=64), nullable=True),
        )
    if "purpose" not in columns:
        op.add_column(
            "email_verification_codes",
            sa.Column("purpose", sa.String(length=32), nullable=True),
        )
    if "scope" not in columns:
        op.add_column(
            "email_verification_codes",
            sa.Column("scope", sa.String(length=320), nullable=True),
        )
    if "delivered_at" not in columns:
        op.add_column(
            "email_verification_codes",
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Codes issued by older application versions are intentionally revoked:
    # their plaintext values cannot be safely promoted to the new HMAC contract.
    op.execute(
        sa.text(
            """
            UPDATE email_verification_codes
            SET is_used = true,
                code_hash = repeat('0', 64),
                purpose = 'legacy',
                scope = 'legacy:' || id::text
            WHERE code_hash IS NULL OR code_hash = ''
            """
        )
    )
    op.alter_column("email_verification_codes", "code_hash", nullable=False)
    op.alter_column("email_verification_codes", "purpose", nullable=False)
    op.alter_column("email_verification_codes", "scope", nullable=False)

    indexes = {
        idx["name"] for idx in sa.inspect(bind).get_indexes("email_verification_codes")
    }
    if "ix_email_verification_codes_scope" not in indexes:
        op.create_index(
            "ix_email_verification_codes_scope",
            "email_verification_codes",
            ["scope"],
            unique=False,
        )
    if "ix_email_verification_codes_delivered_at" not in indexes:
        op.create_index(
            "ix_email_verification_codes_delivered_at",
            "email_verification_codes",
            ["delivered_at"],
            unique=False,
        )
    if "uq_email_verification_codes_active_scope" not in indexes:
        op.create_index(
            "uq_email_verification_codes_active_scope",
            "email_verification_codes",
            ["scope"],
            unique=True,
            postgresql_where=sa.text("is_used = false"),
        )


def downgrade() -> None:
    op.drop_index(
        "uq_email_verification_codes_active_scope",
        table_name="email_verification_codes",
    )
    op.drop_index(
        "ix_email_verification_codes_delivered_at",
        table_name="email_verification_codes",
    )
    op.drop_index(
        "ix_email_verification_codes_scope",
        table_name="email_verification_codes",
    )
    op.drop_column("email_verification_codes", "delivered_at")
    op.drop_column("email_verification_codes", "scope")
    op.drop_column("email_verification_codes", "purpose")
    op.drop_column("email_verification_codes", "code_hash")
