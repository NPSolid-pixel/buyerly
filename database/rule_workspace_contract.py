"""Pure helpers for migrating and enforcing workspace-scoped rule snapshots."""

import json
from typing import Any, Mapping


WORKSPACE_REVIEW_REASON = (
    "Правило отключено: его workspace нельзя подтвердить для этого кабинета."
)


def scope_runtime_rule_snapshots(
    raw_rules: Any,
    *,
    account_workspace_id: int | None,
    preset_workspaces: Mapping[int, int | None],
) -> tuple[list[dict[str, Any]], bool, int]:
    """Stamp valid snapshots and fail closed for unknown/cross-workspace rules."""

    if isinstance(raw_rules, str):
        try:
            rules = json.loads(raw_rules or "[]")
        except (TypeError, json.JSONDecodeError):
            return [], bool(raw_rules.strip()), 0
    else:
        rules = raw_rules
    if not isinstance(rules, list):
        return [], rules not in (None, []), 0

    normalized: list[dict[str, Any]] = []
    changed = False
    disabled = 0
    for raw_rule in rules:
        if not isinstance(raw_rule, dict):
            changed = True
            continue
        rule = dict(raw_rule)
        try:
            preset_id = int(rule.get("preset_id"))
        except (TypeError, ValueError):
            preset_id = None
        preset_workspace_id = preset_workspaces.get(preset_id) if preset_id else None
        same_workspace = bool(
            account_workspace_id is not None
            and preset_workspace_id == account_workspace_id
        )
        if same_workspace:
            if rule.get("workspace_id") != account_workspace_id:
                rule["workspace_id"] = account_workspace_id
                changed = True
        else:
            if rule.get("enabled") is not False:
                rule["enabled"] = False
                changed = True
            if rule.get("needs_review") is not True:
                rule["needs_review"] = True
                changed = True
            if rule.get("review_reason") != WORKSPACE_REVIEW_REASON:
                rule["review_reason"] = WORKSPACE_REVIEW_REASON
                changed = True
            disabled += 1
        normalized.append(rule)
    return normalized, changed, disabled
