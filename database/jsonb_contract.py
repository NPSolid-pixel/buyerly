"""Structured JSONB column contracts shared by runtime and Alembic migrations."""

import json
from typing import Any


# table, primary key, column, required top-level JSON type
JSONB_NATIVE_COLUMNS = (
    ("automation_runtime_states", "state_key", "payload", "object"),
    ("rule_presets", "id", "conditions", "array"),
    ("meta_connections", "id", "granted_scopes", "array"),
    ("summary_snapshots", "id", "payload", "object"),
    ("analytics_view_preferences", "id", "config", "object"),
    ("adset_inventory_cache", "account_id", "adsets_payload", "array"),
    ("audit_events", "id", "before_state", "object"),
    ("audit_events", "id", "after_state", "object"),
    ("audit_events", "id", "details", "object"),
    ("rule_execution_states", "id", "before_state", "object"),
    ("rule_execution_states", "id", "after_state", "object"),
    ("rule_execution_states", "id", "details", "object"),
    ("action_undo_states", "id", "expected_state", "object"),
    ("action_undo_states", "id", "desired_state", "object"),
)


def decode_legacy_jsonb_string(
    value: Any,
    expected_type: str,
) -> tuple[bool, Any]:
    """Decode one top-level JSON string only when its payload matches the contract."""

    if not isinstance(value, str):
        return False, None
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return False, None
    expected_python_type = list if expected_type == "array" else dict
    return (True, decoded) if isinstance(decoded, expected_python_type) else (False, None)
