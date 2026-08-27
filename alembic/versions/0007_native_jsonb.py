"""Normalize structured JSONB values to native arrays and objects.

Revision ID: 0007_native_jsonb
Revises: 0006_audit_ws_ownership
Create Date: 2026-08-27 16:20:00.000000+00:00
"""

import json
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from database.jsonb_contract import JSONB_NATIVE_COLUMNS, decode_legacy_jsonb_string


logger = logging.getLogger(__name__)


revision: str = "0007_native_jsonb"
down_revision: Union[str, None] = "0006_audit_ws_ownership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    malformed = []

    for table_name, primary_key, column_name, expected_type in JSONB_NATIVE_COLUMNS:
        if table_name not in table_names:
            continue
        columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name)
        }
        column = columns.get(column_name)
        if column is None or "JSONB" not in str(column["type"]).upper():
            continue
        rows = bind.execute(
            sa.text(
                f"SELECT {primary_key} AS _pk, {column_name} AS _value "
                f"FROM {table_name} "
                f"WHERE jsonb_typeof({column_name}) <> :expected_type"
            ),
            {"expected_type": expected_type},
        ).mappings()
        for row in rows:
            is_valid, decoded = decode_legacy_jsonb_string(
                row["_value"],
                expected_type,
            )
            if not is_valid:
                malformed.append(f"{table_name}.{column_name}:{row['_pk']}")
                continue
            bind.execute(
                sa.text(
                    f"UPDATE {table_name} "
                    f"SET {column_name} = CAST(:native_json AS JSONB) "
                    f"WHERE {primary_key} = :primary_key "
                    f"AND jsonb_typeof({column_name}) = 'string'"
                ),
                {
                    "native_json": json.dumps(
                        decoded,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "primary_key": row["_pk"],
                },
            )

    if malformed:
        logger.warning(
            "JSONB preflight preserved malformed rows: %s",
            ", ".join(malformed),
        )


def downgrade() -> None:
    # Native arrays/objects are accepted by both the new and previous readers.
    # Reintroducing double encoding would be destructive and is intentionally
    # not performed; restore a database backup only if the representation itself
    # must be reverted.
    pass
