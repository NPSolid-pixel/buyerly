"""Enforce workspace-scoped rule groups, presets, and runtime snapshots.

Revision ID: 0008_rule_workspace_scope
Revises: 0007_native_jsonb
Create Date: 2026-08-27 18:40:00.000000+00:00
"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from database.rule_workspace_contract import scope_runtime_rule_snapshots


revision: str = "0008_rule_workspace_scope"
down_revision: Union[str, None] = "0007_native_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_workspace(bind, table_name: str) -> None:
    bind.execute(
        sa.text(
            f"""
            UPDATE {table_name} AS target
            SET workspace_id = owner.active_workspace_id
            FROM users AS owner
            WHERE target.workspace_id IS NULL
              AND target.owner_user_id = owner.id
              AND owner.active_workspace_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM workspace_members AS member
                  WHERE member.user_id = owner.id
                    AND member.workspace_id = owner.active_workspace_id
              )
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    for table_name in ("rule_presets", "rule_groups"):
        if table_name in table_names:
            _backfill_workspace(bind, table_name)

    if "rule_group_items" in table_names:
        bind.execute(
            sa.text(
                """
                DELETE FROM rule_group_items AS item
                USING rule_groups AS rule_group, rule_presets AS preset
                WHERE item.group_id = rule_group.id
                  AND item.preset_id = preset.id
                  AND (
                      rule_group.workspace_id IS NULL
                      OR preset.workspace_id IS NULL
                      OR rule_group.workspace_id <> preset.workspace_id
                  )
                """
            )
        )

    if "rule_groups" in table_names:
        bind.execute(
            sa.text(
                """
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY workspace_id
                               ORDER BY position, id
                           ) - 1 AS scoped_position
                    FROM rule_groups
                    WHERE workspace_id IS NOT NULL
                )
                UPDATE rule_groups AS rule_group
                SET position = ranked.scoped_position
                FROM ranked
                WHERE rule_group.id = ranked.id
                  AND rule_group.position <> ranked.scoped_position
                """
            )
        )
        op.create_index(
            "ix_rule_groups_workspace_position",
            "rule_groups",
            ["workspace_id", "position", "id"],
            unique=False,
        )

    if "rule_examples_bootstrap" in table_names:
        marker_columns = {
            column["name"]
            for column in inspector.get_columns("rule_examples_bootstrap")
        }
        if "workspace_id" not in marker_columns:
            op.add_column(
                "rule_examples_bootstrap",
                sa.Column("workspace_id", sa.Integer(), nullable=True),
            )
        bind.execute(
            sa.text(
                """
                UPDATE rule_examples_bootstrap AS marker
                SET workspace_id = owner.active_workspace_id
                FROM users AS owner
                WHERE marker.owner_user_id = owner.id
                  AND marker.workspace_id IS NULL
                  AND owner.active_workspace_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM workspace_members AS member
                      WHERE member.user_id = owner.id
                        AND member.workspace_id = owner.active_workspace_id
                  )
                """
            )
        )
        bind.execute(
            sa.text("DELETE FROM rule_examples_bootstrap WHERE workspace_id IS NULL")
        )
        for constraint in sa.inspect(bind).get_unique_constraints(
            "rule_examples_bootstrap"
        ):
            if constraint.get("column_names") == ["owner_user_id"]:
                op.drop_constraint(
                    constraint["name"],
                    "rule_examples_bootstrap",
                    type_="unique",
                )
        op.alter_column(
            "rule_examples_bootstrap",
            "workspace_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.create_foreign_key(
            "fk_rule_examples_bootstrap_workspace",
            "rule_examples_bootstrap",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(
            "ix_rule_examples_bootstrap_workspace_id",
            "rule_examples_bootstrap",
            ["workspace_id"],
            unique=False,
        )
        op.create_unique_constraint(
            "uq_rule_examples_ws_owner",
            "rule_examples_bootstrap",
            ["workspace_id", "owner_user_id"],
        )

    if "accounts" in table_names:
        preset_workspaces = {
            int(row["id"]): row["workspace_id"]
            for row in bind.execute(
                sa.text("SELECT id, workspace_id FROM rule_presets")
            ).mappings()
        }
        for row in bind.execute(
            sa.text(
                "SELECT id, workspace_id, active_rules, rules_enabled FROM accounts"
            )
        ).mappings():
            normalized, changed, _ = scope_runtime_rule_snapshots(
                row["active_rules"],
                account_workspace_id=row["workspace_id"],
                preset_workspaces=preset_workspaces,
            )
            if not changed:
                continue
            has_executable = any(
                rule.get("workspace_id") == row["workspace_id"]
                and rule.get("enabled", True) is not False
                and rule.get("needs_review", False) is not True
                for rule in normalized
            )
            bind.execute(
                sa.text(
                    "UPDATE accounts SET active_rules = :active_rules, "
                    "rules_enabled = :rules_enabled WHERE id = :id"
                ),
                {
                    "id": row["id"],
                    "active_rules": json.dumps(
                        normalized,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "rules_enabled": bool(row["rules_enabled"] and has_executable),
                },
            )

    if bind.dialect.name == "postgresql" and "rule_group_items" in table_names:
        bind.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION enforce_rule_group_item_workspace()
                RETURNS trigger AS $$
                DECLARE
                    group_workspace INTEGER;
                    preset_workspace INTEGER;
                BEGIN
                    SELECT workspace_id INTO group_workspace
                    FROM rule_groups WHERE id = NEW.group_id;
                    SELECT workspace_id INTO preset_workspace
                    FROM rule_presets WHERE id = NEW.preset_id;
                    IF group_workspace IS NULL
                       OR preset_workspace IS NULL
                       OR group_workspace <> preset_workspace THEN
                        RAISE EXCEPTION 'rule group and preset must share workspace';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        bind.execute(
            sa.text(
                """
                CREATE TRIGGER trg_rule_group_item_workspace
                BEFORE INSERT OR UPDATE ON rule_group_items
                FOR EACH ROW EXECUTE FUNCTION enforce_rule_group_item_workspace()
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    table_names = set(sa.inspect(bind).get_table_names())
    if bind.dialect.name == "postgresql" and "rule_group_items" in table_names:
        bind.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS trg_rule_group_item_workspace "
                "ON rule_group_items"
            )
        )
        bind.execute(
            sa.text("DROP FUNCTION IF EXISTS enforce_rule_group_item_workspace()")
        )
    if "rule_groups" in table_names:
        op.drop_index("ix_rule_groups_workspace_position", table_name="rule_groups")
    if "rule_examples_bootstrap" in table_names:
        bind.execute(
            sa.text(
                """
                DELETE FROM rule_examples_bootstrap AS duplicate
                USING rule_examples_bootstrap AS keeper
                WHERE duplicate.owner_user_id = keeper.owner_user_id
                  AND duplicate.id > keeper.id
                """
            )
        )
        op.drop_constraint(
            "uq_rule_examples_ws_owner",
            "rule_examples_bootstrap",
            type_="unique",
        )
        op.drop_index(
            "ix_rule_examples_bootstrap_workspace_id",
            table_name="rule_examples_bootstrap",
        )
        op.drop_constraint(
            "fk_rule_examples_bootstrap_workspace",
            "rule_examples_bootstrap",
            type_="foreignkey",
        )
        op.create_unique_constraint(
            "uq_rule_examples_bootstrap_owner_user_id",
            "rule_examples_bootstrap",
            ["owner_user_id"],
        )
        op.drop_column("rule_examples_bootstrap", "workspace_id")
