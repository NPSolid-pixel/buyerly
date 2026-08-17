import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import or_, select

from core.audit import build_audit_event
from core.currency import UNKNOWN_CURRENCY, normalize_currency
from core.logging_config import redact_secrets
from database.models import Account, ActionUndoState, AuditEvent, StoppedAdSet


UNDO_WINDOW_SECONDS = 24 * 60 * 60
UNDO_PENDING_LEASE_SECONDS = 120
REVERSIBLE_EVENT_TYPES = {
    "STOP",
    "AUTO_REACTIVATE",
    "MANUAL_REACTIVATE",
    "MANUAL_PAUSE",
    "INCREASE_BUDGET",
    "DECREASE_BUDGET",
}
MUTATING_EVENT_TYPES = REVERSIBLE_EVENT_TYPES | {"UNDO_ACTION"}


class UndoError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class UndoSpec:
    kind: str
    expected_state: dict[str, Any]
    desired_state: dict[str, Any]


def _load_state(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    try:
        value = json.loads(raw_value or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_state(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utc_timestamp(value: Optional[datetime]) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def undo_spec_for_event(event: AuditEvent) -> UndoSpec:
    event_type = str(event.event_type or "").upper()
    before = _load_state(event.before_state)
    after = _load_state(event.after_state)

    if event_type in {"STOP", "MANUAL_PAUSE"}:
        return UndoSpec("status", {"status": "PAUSED"}, {"status": "ACTIVE"})
    if event_type in {"AUTO_REACTIVATE", "MANUAL_REACTIVATE"}:
        return UndoSpec("status", {"status": "ACTIVE"}, {"status": "PAUSED"})
    if event_type in {"INCREASE_BUDGET", "DECREASE_BUDGET"}:
        try:
            previous_budget = float(before["daily_budget"])
            changed_budget = float(after["daily_budget"])
        except (KeyError, TypeError, ValueError):
            raise UndoError("В истории нет точных значений бюджета до и после действия.")
        if not all(
            math.isfinite(value) and value >= 1.0
            for value in (previous_budget, changed_budget)
        ):
            raise UndoError("Значения бюджета в истории небезопасны для отмены.")
        return UndoSpec(
            "budget",
            {"daily_budget": changed_budget},
            {"daily_budget": previous_budget},
        )
    raise UndoError("Это действие нельзя безопасно отменить.")


def state_matches(spec: UndoSpec, current: dict[str, Any], target: dict[str, Any]) -> bool:
    if spec.kind == "status":
        return str(current.get("status") or "").upper() == str(target["status"]).upper()
    try:
        return abs(float(current.get("daily_budget")) - float(target["daily_budget"])) <= 0.011
    except (TypeError, ValueError):
        return False


def event_is_within_undo_window(event: AuditEvent, *, now: Optional[float] = None) -> bool:
    created_at = _utc_timestamp(event.created_at)
    return created_at > 0 and (time.time() if now is None else now) - created_at <= UNDO_WINDOW_SECONDS


async def _mark_failed(
    session,
    *,
    undo_state: ActionUndoState,
    source: AuditEvent,
    account: Account,
    actor_type: str,
    actor_id: str,
    error: Exception,
    action_started: float,
) -> None:
    safe_error = redact_secrets(str(error))
    undo_state.status = "ERROR"
    undo_state.last_error = safe_error
    failure = build_audit_event(
        account=account,
        event_type="UNDO_ACTION_FAILED",
        status="ERROR",
        correlation_id=undo_state.correlation_id,
        category="MANUAL_ACTION",
        action=f"UNDO_{source.action or source.event_type}",
        message=safe_error,
        before_state=_load_state(source.after_state),
        after_state=_load_state(source.after_state),
        details={"original_event_id": source.id, "original_event_type": source.event_type},
        duration_ms=(time.perf_counter() - action_started) * 1000,
        actor_type=actor_type,
        actor_id=actor_id,
        adset_id=source.adset_id,
        adset_name=source.adset_name,
    )
    session.add(failure)
    try:
        await session.commit()
    except Exception:
        await session.rollback()


async def undo_audit_action(
    session,
    *,
    meta_client,
    event_id: int,
    actor_type: str,
    actor_id: str,
    owner_id: Optional[str],
    owner_user_id: Optional[int],
    is_admin: bool = False,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Safely and idempotently reverse one successful Meta mutation."""

    now_ts = time.time() if now is None else now
    source = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.id == event_id).with_for_update()
        )
    ).scalar_one_or_none()
    if source is None:
        raise UndoError("Событие не найдено.", 404)
    source_owned = (
        source.owner_user_id == owner_user_id
        if source.owner_user_id is not None
        else source.owner_id == str(owner_id or "")
    )
    if not is_admin and not source_owned:
        raise UndoError("Доступ к этому действию запрещён.", 403)

    existing_reversal = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.reverts_event_id == source.id)
        )
    ).scalar_one_or_none()
    if existing_reversal is not None:
        return {
            "success": True,
            "already_reverted": True,
            "original_event_id": source.id,
            "reversal_event_id": existing_reversal.id,
            "message": "Действие уже отменено.",
        }
    if str(source.status).upper() != "SUCCESS":
        raise UndoError("Отменять можно только успешно выполненное действие.")
    spec = undo_spec_for_event(source)
    if not source.account_id or not source.adset_id:
        raise UndoError("В истории нет кабинета или ad set для отмены.")
    if not event_is_within_undo_window(source, now=now_ts):
        raise UndoError("Безопасное окно отмены 24 часа уже закрыто.")

    newer_action = (
        await session.execute(
            select(AuditEvent.id).where(
                AuditEvent.id > source.id,
                AuditEvent.account_id == source.account_id,
                AuditEvent.adset_id == source.adset_id,
                AuditEvent.status == "SUCCESS",
                AuditEvent.event_type.in_(MUTATING_EVENT_TYPES),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if newer_action is not None:
        raise UndoError("После этого события ad set уже изменялся. Старая отмена заблокирована.")

    account = (
        await session.execute(select(Account).where(Account.account_id == source.account_id))
    ).scalar_one_or_none()
    account_owned = bool(
        account
        and (
            account.owner_user_id == owner_user_id
            if account.owner_user_id is not None
            else account.owner_id == str(owner_id or "")
        )
    )
    if account is None or (not is_admin and not account_owned):
        raise UndoError("Кабинет для этого действия не найден или недоступен.", 403)

    undo_state = (
        await session.execute(
            select(ActionUndoState)
            .where(ActionUndoState.original_event_id == source.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    retry_after_crash = False
    if undo_state is None:
        undo_state = ActionUndoState(
            original_event_id=source.id,
            owner_id=source.owner_id,
            owner_user_id=source.owner_user_id,
            status="PENDING",
            correlation_id=uuid.uuid4().hex,
            attempt_count=1,
            expected_state=_json_state(spec.expected_state),
            desired_state=_json_state(spec.desired_state),
        )
        session.add(undo_state)
    elif undo_state.status == "PENDING":
        pending_age = now_ts - _utc_timestamp(undo_state.updated_at)
        if pending_age < UNDO_PENDING_LEASE_SECONDS:
            raise UndoError("Отмена уже выполняется. Обновите историю через пару минут.")
        retry_after_crash = True
        undo_state.attempt_count += 1
        undo_state.correlation_id = uuid.uuid4().hex
        undo_state.last_error = ""
    else:
        undo_state.status = "PENDING"
        undo_state.attempt_count += 1
        undo_state.correlation_id = uuid.uuid4().hex
        undo_state.last_error = ""
    await session.commit()

    action_started = time.perf_counter()
    try:
        currency = normalize_currency(account.currency)
        if currency == UNKNOWN_CURRENCY:
            account_info = await meta_client.get_account_info(
                account.account_id,
                account.access_token,
            )
            currency = normalize_currency(account_info.get("currency"))
            if currency == UNKNOWN_CURRENCY:
                raise RuntimeError("Meta did not return the ad account currency")
            account.currency = currency
            await session.commit()
        current_state = await meta_client.get_adset_state(
            source.adset_id,
            account.access_token,
            currency=currency,
        )
    except Exception as error:
        await _mark_failed(
            session,
            undo_state=undo_state,
            source=source,
            account=account,
            actor_type=actor_type,
            actor_id=actor_id,
            error=error,
            action_started=action_started,
        )
        raise UndoError("Meta не вернула текущее состояние. Отмена не выполнялась.", 502)

    reconciled = retry_after_crash and state_matches(spec, current_state, spec.desired_state)
    if not reconciled and not state_matches(spec, current_state, spec.expected_state):
        undo_state.status = "ERROR"
        undo_state.last_error = "Meta state no longer matches the original action"
        await session.commit()
        raise UndoError("Текущее состояние Meta уже отличается от результа исходного действия.")

    if not reconciled:
        try:
            if spec.kind == "status":
                await meta_client.set_adset_status(
                    source.adset_id,
                    account.access_token,
                    spec.desired_state["status"],
                )
            else:
                await meta_client.update_adset_budget(
                    source.adset_id,
                    account.access_token,
                    float(spec.desired_state["daily_budget"]),
                    currency=currency,
                )
        except Exception as error:
            await _mark_failed(
                session,
                undo_state=undo_state,
                source=source,
                account=account,
                actor_type=actor_type,
                actor_id=actor_id,
                error=error,
                action_started=action_started,
            )
            raise UndoError("Meta не смогла отменить действие. Подробности сохранены в логах.", 502)

    reversal = build_audit_event(
        account=account,
        event_type="UNDO_ACTION",
        status="SUCCESS",
        correlation_id=undo_state.correlation_id,
        category="MANUAL_ACTION",
        action=f"UNDO_{source.action or source.event_type}",
        message=f"Действие №{source.id} безопасно отменено.",
        before_state=current_state,
        after_state=spec.desired_state,
        details={
            "original_event_id": source.id,
            "original_event_type": source.event_type,
            "reconciled_after_restart": reconciled,
            "currency": currency,
        },
        duration_ms=(time.perf_counter() - action_started) * 1000,
        actor_type=actor_type,
        actor_id=actor_id,
        adset_id=source.adset_id,
        adset_name=source.adset_name or current_state.get("adset_name", ""),
    )
    reversal.reverts_event_id = source.id
    session.add(reversal)
    undo_state.status = "SUCCESS"

    stopped = (
        await session.execute(
            select(StoppedAdSet).where(StoppedAdSet.adset_id == source.adset_id)
        )
    ).scalar_one_or_none()
    if spec.kind == "status" and spec.desired_state["status"] == "ACTIVE":
        if stopped:
            stopped.is_resolved = True
    elif spec.kind == "status" and spec.desired_state["status"] == "PAUSED":
        if stopped:
            stopped.is_resolved = False
        else:
            session.add(
                StoppedAdSet(
                    account_id=account.account_id,
                    adset_id=source.adset_id,
                    adset_name=source.adset_name or current_state.get("adset_name") or source.adset_id,
                    stop_spend=0.0,
                    stop_leads=0,
                    stop_registrations=0,
                    is_resolved=False,
                    stopped_at=datetime.now(timezone.utc),
                )
            )
    try:
        await session.commit()
        await session.refresh(reversal)
    except Exception as error:
        await session.rollback()
        raise UndoError(
            "Meta выполнила отмену, но Buyerly ещё не закрыл её в истории. Повторите через две минуты.",
            500,
        ) from error

    return {
        "success": True,
        "already_reverted": False,
        "original_event_id": source.id,
        "reversal_event_id": reversal.id,
        "message": "Действие отменено и подтверждено Meta.",
    }
