import json
from typing import Any, Optional

from core.logging_config import redact_secrets
from database.models import Account, AuditEvent
from rules.engine import RuleEvaluationResult


def _json_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str, separators=(",", ":"))


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_audit_event(
    *,
    account: Account,
    event_type: str,
    status: str,
    correlation_id: str,
    category: str = "RULE_ACTION",
    evaluation: Optional[RuleEvaluationResult] = None,
    action: str = "",
    message: str = "",
    before_state: Any = None,
    after_state: Any = None,
    details: Any = None,
    duration_ms: int = 0,
    actor_type: str = "system",
    actor_id: str = "monitoring_worker",
    adset_id: str = "",
    adset_name: str = "",
) -> AuditEvent:
    """Build a secret-safe audit row without committing the caller's transaction."""

    evaluation_details = {}
    if evaluation is not None:
        evaluation_details = {
            "reason": evaluation.reason,
            "conditions": evaluation.conditions_snapshot,
            "spend": evaluation.spend,
            "leads": evaluation.leads,
            "registrations": evaluation.registrations,
            "purchases": evaluation.purchases,
            "cpl": evaluation.cpl,
            "cpreg": evaluation.cpreg,
            "cpp": evaluation.cpp,
            "notify_tg": evaluation.notify_tg,
            "cooldown_minutes": evaluation.cooldown_minutes,
        }

    merged_details = {**evaluation_details, **(details or {})}
    safe_message = redact_secrets(message or (evaluation.reason if evaluation else ""))

    return AuditEvent(
        owner_id=str(account.owner_id or ""),
        owner_user_id=account.owner_user_id,
        actor_type=str(actor_type),
        actor_id=str(actor_id),
        category=category,
        event_type=str(event_type),
        status=str(status),
        account_id=str(account.account_id or ""),
        account_name=str(account.name or ""),
        adset_id=str(evaluation.adset_id if evaluation else adset_id),
        adset_name=str(evaluation.adset_name if evaluation else adset_name),
        rule_id=_optional_int(evaluation.rule_id if evaluation else None),
        rule_name=str(evaluation.rule_name if evaluation else ""),
        action=action or (evaluation.action.value if evaluation else ""),
        message=safe_message,
        before_state=_json_text(before_state),
        after_state=_json_text(after_state),
        details=_json_text(merged_details),
        correlation_id=str(correlation_id),
        duration_ms=max(0, int(duration_ms or 0)),
    )
