"""Transactional allocation of globally unique workspace slugs."""

from sqlalchemy import select, text

from core.workspace_slugs import (
    numbered_workspace_slug,
    reservation_safe_workspace_slug,
)
from database.models import Workspace


async def allocate_workspace_slug(session, value: str) -> str:
    """Allocate the lowest available deterministic suffix for a workspace slug."""
    base = reservation_safe_workspace_slug(value)
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"buyerly-workspace-slug:{base}"},
        )

    base_exists = (
        await session.execute(select(Workspace.id).where(Workspace.slug == base))
    ).scalar_one_or_none()
    if base_exists is None:
        return base

    number = 2
    while True:
        candidate = numbered_workspace_slug(base, number)
        candidate_exists = (
            await session.execute(select(Workspace.id).where(Workspace.slug == candidate))
        ).scalar_one_or_none()
        if candidate_exists is None:
            return candidate
        number += 1
