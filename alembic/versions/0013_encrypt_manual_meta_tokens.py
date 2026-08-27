"""Encrypt legacy manual Meta access tokens at rest.

Revision ID: 0013_manual_tokens
Revises: 0012_group_ws_unique
Create Date: 2026-08-27 19:30:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from core.meta_tokens import MetaTokenError, decrypt_meta_token, encrypt_meta_token


revision: str = "0013_manual_tokens"
down_revision: Union[str, None] = "0012_group_ws_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _encrypted_value(raw_token: str) -> str:
    token = str(raw_token or "").strip()
    if token.startswith("gAAAAA"):
        try:
            decrypt_meta_token(token)
            return token
        except MetaTokenError:
            pass
    return encrypt_meta_token(token)


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "access_token_encrypted",
            sa.Text(),
            nullable=True,
            server_default="",
        ),
    )

    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.text(
                "SELECT id, access_token FROM accounts "
                "WHERE access_token IS NOT NULL AND btrim(access_token) <> '' "
                "ORDER BY id ASC"
            )
        ).mappings()
    )
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE accounts "
                "SET access_token_encrypted = :encrypted_token, access_token = '' "
                "WHERE id = :account_id"
            ),
            {
                "encrypted_token": _encrypted_value(row["access_token"]),
                "account_id": row["id"],
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.text(
                "SELECT id, access_token_encrypted FROM accounts "
                "WHERE access_token_encrypted IS NOT NULL "
                "AND btrim(access_token_encrypted) <> '' "
                "ORDER BY id ASC"
            )
        ).mappings()
    )
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE accounts SET access_token = :access_token "
                "WHERE id = :account_id"
            ),
            {
                "access_token": decrypt_meta_token(row["access_token_encrypted"]),
                "account_id": row["id"],
            },
        )
    op.drop_column("accounts", "access_token_encrypted")
