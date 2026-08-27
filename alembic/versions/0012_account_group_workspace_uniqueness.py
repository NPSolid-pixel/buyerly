"""Scope case-insensitive account group uniqueness to workspace.

Revision ID: 0012_group_ws_unique
Revises: 0011_workspace_slugs
Create Date: 2026-08-27 23:55:00.000000+00:00
"""

from collections import defaultdict
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_group_ws_unique"
down_revision: Union[str, None] = "0011_workspace_slugs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAX_GROUP_NAME_LENGTH = 80


def _numbered_name(base: str, number: int) -> str:
    suffix = f" ({number})"
    return f"{base[: MAX_GROUP_NAME_LENGTH - len(suffix)].rstrip()}{suffix}"


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(
        sa.text(
            "ALTER TABLE account_groups "
            "DROP CONSTRAINT IF EXISTS uq_account_group_owner_name"
        )
    )

    legacy_rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, owner_user_id
                FROM account_groups
                WHERE workspace_id IS NULL
                ORDER BY id ASC
                """
            )
        ).mappings()
    )
    for row in legacy_rows:
        member_workspace_ids = list(
            bind.execute(
                sa.text(
                    """
                    SELECT DISTINCT account.workspace_id
                    FROM account_group_members AS member
                    JOIN accounts AS account ON account.id = member.account_id
                    WHERE member.group_id = :group_id
                      AND account.workspace_id IS NOT NULL
                    ORDER BY account.workspace_id ASC
                    """
                ),
                {"group_id": row["id"]},
            ).scalars()
        )
        workspace_id = member_workspace_ids[0] if len(member_workspace_ids) == 1 else None
        if workspace_id is None and row["owner_user_id"] is not None:
            workspace_id = bind.execute(
                sa.text(
                    """
                    SELECT user_row.active_workspace_id
                    FROM users AS user_row
                    JOIN workspace_members AS member
                      ON member.user_id = user_row.id
                     AND member.workspace_id = user_row.active_workspace_id
                    WHERE user_row.id = :owner_user_id
                    """
                ),
                {"owner_user_id": row["owner_user_id"]},
            ).scalar_one_or_none()
        if workspace_id is not None:
            bind.execute(
                sa.text(
                    "UPDATE account_groups SET workspace_id = :workspace_id WHERE id = :group_id"
                ),
                {"workspace_id": workspace_id, "group_id": row["id"]},
            )

    rows = list(
        bind.execute(
            sa.text(
                "SELECT id, workspace_id, name FROM account_groups ORDER BY id ASC"
            )
        ).mappings()
    )
    used_names: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        clean_name = " ".join((row["name"] or "").split()) or f"Group {row['id']}"
        clean_name = clean_name[:MAX_GROUP_NAME_LENGTH].rstrip()
        candidate = clean_name
        workspace_id = row["workspace_id"]
        if workspace_id is not None:
            number = 2
            while candidate.lower() in used_names[workspace_id]:
                candidate = _numbered_name(clean_name, number)
                number += 1
            used_names[workspace_id].add(candidate.lower())
        if candidate != row["name"]:
            bind.execute(
                sa.text("UPDATE account_groups SET name = :name WHERE id = :group_id"),
                {"name": candidate, "group_id": row["id"]},
            )

    op.execute(sa.text("DROP INDEX IF EXISTS uq_account_groups_workspace_name_ci"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_account_groups_workspace_name_ci "
            "ON account_groups (workspace_id, lower(btrim(name))) "
            "WHERE workspace_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(sa.text("DROP INDEX IF EXISTS uq_account_groups_workspace_name_ci"))

    rows = list(
        bind.execute(
            sa.text(
                "SELECT id, owner_user_id, name FROM account_groups ORDER BY id ASC"
            )
        ).mappings()
    )
    used_names: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        owner_user_id = row["owner_user_id"]
        if owner_user_id is None:
            continue
        candidate = row["name"]
        number = 2
        while candidate in used_names[owner_user_id]:
            candidate = _numbered_name(row["name"], number)
            number += 1
        used_names[owner_user_id].add(candidate)
        if candidate != row["name"]:
            bind.execute(
                sa.text("UPDATE account_groups SET name = :name WHERE id = :group_id"),
                {"name": candidate, "group_id": row["id"]},
            )

    op.create_unique_constraint(
        "uq_account_group_owner_name",
        "account_groups",
        ["owner_user_id", "name"],
    )
