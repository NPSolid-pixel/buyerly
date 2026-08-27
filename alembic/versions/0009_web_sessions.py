"""Replace persistent browser tokens with expiring hashed web sessions.

Revision ID: 0009_web_sessions
Revises: 0008_rule_workspace_scope
Create Date: 2026-08-27 20:30:00.000000+00:00
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from core.config import settings


revision: str = "0009_web_sessions"
down_revision: Union[str, None] = "0008_rule_workspace_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_web_sessions_user_id", "web_sessions", ["user_id"], unique=False)
    op.create_index("ix_web_sessions_token_hash", "web_sessions", ["token_hash"], unique=True)
    op.create_index("ix_web_sessions_expires_at", "web_sessions", ["expires_at"], unique=False)
    op.create_index("ix_web_sessions_revoked_at", "web_sessions", ["revoked_at"], unique=False)

    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.WEB_SESSION_TTL_HOURS)
    rows = bind.execute(
        sa.text("SELECT id, auth_token FROM users WHERE auth_token IS NOT NULL")
    ).mappings()
    for row in rows:
        raw_token = row["auth_token"]
        if not raw_token:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO web_sessions (
                    id, user_id, token_hash, csrf_hash, user_agent, ip_address,
                    created_at, expires_at, last_seen_at, rotated_at, revoked_at
                ) VALUES (
                    :id, :user_id, :token_hash, :csrf_hash, 'Legacy browser', '',
                    :created_at, :expires_at, :last_seen_at, :rotated_at, NULL
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": row["id"],
                "token_hash": hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
                "csrf_hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                "created_at": now,
                "expires_at": expires_at,
                "last_seen_at": now,
                "rotated_at": now,
            },
        )
    bind.execute(sa.text("UPDATE users SET auth_token = NULL WHERE auth_token IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_web_sessions_revoked_at", table_name="web_sessions")
    op.drop_index("ix_web_sessions_expires_at", table_name="web_sessions")
    op.drop_index("ix_web_sessions_token_hash", table_name="web_sessions")
    op.drop_index("ix_web_sessions_user_id", table_name="web_sessions")
    op.drop_table("web_sessions")

