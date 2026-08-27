"""Normalize workspace slugs to the deterministic reserved-safe contract.

Revision ID: 0011_workspace_slugs
Revises: 0010_atomic_otp
Create Date: 2026-08-27 23:10:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from core.workspace_slugs import numbered_workspace_slug, reservation_safe_workspace_slug


revision: str = "0011_workspace_slugs"
down_revision: Union[str, None] = "0010_atomic_otp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = list(
        bind.execute(sa.text("SELECT id, slug FROM workspaces ORDER BY id ASC")).mappings()
    )
    occupied = {row["slug"] for row in rows}

    for row in rows:
        old_slug = row["slug"]
        occupied.discard(old_slug)
        base = reservation_safe_workspace_slug(old_slug)
        candidate = base
        number = 2
        while candidate in occupied:
            candidate = numbered_workspace_slug(base, number)
            number += 1
        occupied.add(candidate)
        if candidate != old_slug:
            bind.execute(
                sa.text("UPDATE workspaces SET slug = :slug WHERE id = :workspace_id"),
                {"slug": candidate, "workspace_id": row["id"]},
            )


def downgrade() -> None:
    # The previous arbitrary values cannot be reconstructed safely.
    pass
