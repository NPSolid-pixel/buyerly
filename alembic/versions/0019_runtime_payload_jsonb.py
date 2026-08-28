"""Convert legacy monitoring runtime payloads from TEXT to JSONB.

Revision ID: 0019_runtime_payload_jsonb
Revises: 0018_account_health
Create Date: 2026-08-28 18:45:00.000000+00:00
"""

import json
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0019_runtime_payload_jsonb"
down_revision: Union[str, None] = "0018_account_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _object_payload(value: Any) -> dict[str, Any]:
    """Recover a native object from legacy or double-encoded JSON text."""

    candidate = value
    for _ in range(2):
        if isinstance(candidate, dict):
            return candidate
        if not isinstance(candidate, str):
            return {}
        try:
            candidate = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            return {}
    return candidate if isinstance(candidate, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "automation_runtime_states" not in set(inspector.get_table_names()):
        return
    columns = {
        column["name"]: column
        for column in inspector.get_columns("automation_runtime_states")
    }
    payload_column = columns.get("payload")
    if payload_column is None:
        return

    rows = bind.execute(
        sa.text(
            "SELECT state_key, payload AS payload_value "
            "FROM automation_runtime_states"
        )
    ).mappings().all()
    payload_type = str(payload_column["type"]).upper()

    if "JSONB" in payload_type:
        for row in rows:
            normalized = _object_payload(row["payload_value"])
            if normalized != row["payload_value"]:
                bind.execute(
                    sa.text(
                        "UPDATE automation_runtime_states "
                        "SET payload = CAST(:payload AS JSONB) "
                        "WHERE state_key = :state_key"
                    ),
                    {
                        "payload": json.dumps(normalized, separators=(",", ":")),
                        "state_key": row["state_key"],
                    },
                )
        return

    op.add_column(
        "automation_runtime_states",
        sa.Column("payload_jsonb", postgresql.JSONB(), nullable=True),
    )
    for row in rows:
        normalized = _object_payload(row["payload_value"])
        bind.execute(
            sa.text(
                "UPDATE automation_runtime_states "
                "SET payload_jsonb = CAST(:payload AS JSONB) "
                "WHERE state_key = :state_key"
            ),
            {
                "payload": json.dumps(normalized, separators=(",", ":")),
                "state_key": row["state_key"],
            },
        )
    op.alter_column(
        "automation_runtime_states",
        "payload_jsonb",
        existing_type=postgresql.JSONB(),
        nullable=False,
    )
    op.drop_column("automation_runtime_states", "payload")
    op.alter_column(
        "automation_runtime_states",
        "payload_jsonb",
        existing_type=postgresql.JSONB(),
        new_column_name="payload",
    )


def downgrade() -> None:
    # JSONB is accepted by every supported reader. Reverting to the unsafe
    # legacy TEXT representation would discard the repaired type contract.
    pass
