import logging
import random
import asyncio
import json
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable, Any
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.db import async_session_maker
from database.models import (
    Account,
    AppSettings,
    AutomationRuntimeState,
    AutomationScheduleState,
    MetaConnection,
    RuleExecutionState,
    StoppedAdSet,
    User,
    Workspace,
)
from core.audit import build_audit_event
from core.currency import normalize_currency
from core.meta_tokens import resolve_account_access_token
from core.timezones import (
    canonical_timezone_name,
    evaluate_day_boundary,
    resolve_account_clock,
    utc_offset_label,
)
from meta_api.client import MetaClient
from rules.engine import RuleEngine, RuleAction, RuleEvaluationResult
from services.inventory_cache import AdsetInventoryService, PostgreSQLInventoryCache
from services.account_health import record_account_health

logger = logging.getLogger(__name__)

PENDING_RECONCILIATION_SECONDS = 15 * 60
DAY_BOUNDARY_NOTIFICATION_WINDOW_MINUTES = 5

class MonitoringWorker:
    """
    Фоновый воркер, выполняющий периодический опрос всех активных аккаунтов,
    контроль часовых поясов, сброса суток и правил стопа/реактивации
    с персональной доставкой уведомлений владельцу каждого кабинета.
    """

    def __init__(
        self, 
        meta_client: Optional[MetaClient] = None,
        telegram_notifier: Optional[Callable[..., Awaitable[None]]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.meta_client = meta_client or MetaClient(cache_provider=PostgreSQLInventoryCache())
        self.telegram_notifier = telegram_notifier
        # A wall clock is intentionally used: persisted timestamps must remain
        # meaningful after a process restart, unlike time.monotonic().
        self._clock = clock or time.time
        self._current_cycle_id = ""
        self._action_semaphore = asyncio.Semaphore(1)

    @staticmethod
    def _load_rules(
        raw_rules: Any,
        *,
        workspace_id: int | None,
    ) -> list[dict[str, Any]]:
        try:
            rules = json.loads(raw_rules) if isinstance(raw_rules, str) else raw_rules
        except (TypeError, ValueError):
            return []
        if not isinstance(rules, list):
            return []
        if workspace_id is None:
            return []
        return [
            rule
            for rule in rules
            if isinstance(rule, dict)
            and rule.get("workspace_id") == workspace_id
        ]

    @staticmethod
    def _interval_minutes(value: Any, fallback: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return max(1, fallback)

    @staticmethod
    def _is_critical_stop_rule(rule: dict[str, Any]) -> bool:
        return str(rule.get("action") or "").lower() in {
            "turn_off",
            "stop",
            "pause",
        }

    async def _fetch_account_snapshot(
        self,
        *,
        account: Account,
        access_token: str,
        due_rules: list[dict[str, Any]],
        health_due: bool,
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        """Collect one account's Meta reads under the global concurrency cap."""

        async with semaphore:
            priority = (
                "critical"
                if any(self._is_critical_stop_rule(rule) for rule in due_rules)
                else "normal"
            )
            account_info = None
            currency = normalize_currency(account.currency)

            if health_due or currency == "UNKNOWN":
                account_info = await self.meta_client.get_account_info(
                    account.account_id,
                    access_token,
                    priority=priority,
                )
                currency = normalize_currency(account_info.get("currency") or currency)
            if currency == "UNKNOWN":
                raise RuntimeError(
                    "Meta did not return the ad account currency; automation is blocked"
                )

            today = await self.meta_client.get_adsets_insights(
                account_id=account.account_id,
                access_token=access_token,
                date_preset="today",
                currency=currency,
                priority=priority,
            )
            windows = {
                str(condition.get("time_window") or "today")
                for rule in due_rules
                for condition in (rule.get("conditions") or [])
                if isinstance(condition, dict)
                and str(condition.get("time_window") or "today") != "today"
            }

            async def fetch_window(window: str):
                rows = await self.meta_client.get_adsets_insights(
                    account_id=account.account_id,
                    access_token=access_token,
                    date_preset=window,
                    currency=currency,
                    priority=priority,
                )
                return window, {str(row["adset_id"]): row for row in rows}

            results = await asyncio.gather(
                *(fetch_window(window) for window in sorted(windows)),
                return_exceptions=True,
            )
            insights_by_window = {}
            window_errors = []
            for result in results:
                if isinstance(result, Exception):
                    window_errors.append(str(result))
                else:
                    window, rows = result
                    insights_by_window[window] = rows

            return {
                "account_info": account_info,
                "currency": currency,
                "adsets": today,
                "insights_by_window": insights_by_window,
                "window_errors": window_errors,
            }

    async def _persist_runtime_state(
        self,
        *,
        stats: dict[str, Any],
        started_at: str,
        duration_ms: int,
    ) -> None:
        usage_snapshot = (
            self.meta_client.get_usage_snapshot()
            if hasattr(self.meta_client, "get_usage_snapshot")
            else {}
        )
        # Runtime settings are readable by every signed-in user. Keep the
        # operational quota signal, but never expose another owner's account
        # identifiers from the per-account Meta header breakdown.
        usage = {
            "max_percent": int(usage_snapshot.get("max_percent", 0) or 0),
            "app": dict(usage_snapshot.get("app") or {}),
            "accounts_observed": len(usage_snapshot.get("accounts") or {}),
            "updated_at": usage_snapshot.get("updated_at"),
        }
        payload = {
            "cycle_id": stats.get("cycle_id"),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int(duration_ms),
            "accounts_checked": int(stats.get("accounts_checked", 0)),
            "accounts_skipped": int(stats.get("accounts_skipped", 0)),
            "rules_checked": int(stats.get("rules_checked", 0)),
            "adsets_checked": int(stats.get("adsets_checked", 0)),
            "actions_count": sum(
                int(stats.get(key, 0))
                for key in (
                    "adsets_stopped",
                    "adsets_reactivated",
                    "budgets_changed",
                    "proposals_sent",
                )
            ),
            "errors_count": len(stats.get("errors") or []),
            "recent_errors": list(stats.get("errors") or [])[:5],
            "usage": usage,
        }
        try:
            async with async_session_maker() as session:
                row = await session.get(AutomationRuntimeState, "monitoring")
                if row is None:
                    row = AutomationRuntimeState(state_key="monitoring")
                    session.add(row)
                existing_payload = dict(row.payload or {})
                if existing_payload.get("last_backup_at"):
                    payload["last_backup_at"] = existing_payload["last_backup_at"]
                row.payload = payload
                await session.commit()
        except Exception as error:
            logger.error("Failed to persist monitoring runtime state: %s", error)

    async def _set_account_health(
        self,
        session,
        account: Account,
        *,
        success: bool,
        notification_target: str = "",
        error: Any = None,
        cause: str | None = None,
        signals: dict[str, Any] | None = None,
    ) -> None:
        """Persist health and alert only when status/cause changes."""

        row, transitioned = await record_account_health(
            session,
            account,
            success=success,
            error=error,
            cause=cause,
            signals=signals,
        )
        if row is None or not transitioned:
            return
        health_status = row.status
        health_cause = row.cause
        health_message = row.last_error_message
        health_error_code = row.last_error_code
        health_failures = row.consecutive_failures
        await self._persist_audit_event(
            session,
            account,
            event_type="ACCOUNT_HEALTH_RECOVERED" if success else "ACCOUNT_HEALTH_ALERT",
            status="SUCCESS" if success else health_status.upper(),
            category="ACCOUNT_HEALTH",
            action="MONITOR" if success else "INVESTIGATE",
            message="Account health recovered" if success else health_message,
            after_state={"status": health_status, "cause": health_cause},
            details={
                "error_code": health_error_code,
                "consecutive_failures": health_failures,
            },
        )
        if self.telegram_notifier:
            await self.telegram_notifier(
                event_type="ACCOUNT_HEALTH_RECOVERED" if success else "ACCOUNT_HEALTH_ALERT",
                account_name=account.name,
                account_id=account.account_id,
                target_chat_id=notification_target,
                health_status=health_status,
                health_cause=health_cause,
                health_message=health_message,
            )

    @staticmethod
    def _rule_key(account_id: str, index: int, rule: dict[str, Any]) -> str:
        rule_id = rule.get("preset_id")
        return f"{account_id}:{rule_id if rule_id is not None else f'index-{index}'}"

    @staticmethod
    def _schedule_key(scope: str, account_id: str, rule_key: str = "") -> str:
        return f"{scope}:{account_id}:{rule_key}"

    @staticmethod
    def _evaluation_rule_key(evaluation: RuleEvaluationResult) -> str:
        if evaluation.rule_id is not None:
            return str(evaluation.rule_id)
        fingerprint = json.dumps(
            {
                "name": evaluation.rule_name,
                "conditions": evaluation.conditions_snapshot,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"inline-{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"

    @classmethod
    def _execution_key(
        cls,
        account: Account,
        evaluation: RuleEvaluationResult,
    ) -> tuple[str, str]:
        rule_key = cls._evaluation_rule_key(evaluation)
        raw_key = ":".join(
            (
                str(account.account_id),
                str(evaluation.adset_id),
                rule_key,
                evaluation.action.value,
            )
        )
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        return f"rule-action:{digest}", rule_key

    @staticmethod
    def _json_dict(raw_value: Any) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            return raw_value
        try:
            value = json.loads(raw_value or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _state_matches(observed: dict[str, Any], desired: dict[str, Any]) -> bool:
        if "status" in desired:
            return str(observed.get("status", "")).upper() == str(desired["status"]).upper()
        if "daily_budget" in desired:
            try:
                return abs(float(observed.get("daily_budget")) - float(desired["daily_budget"])) < 0.01
            except (TypeError, ValueError):
                return False
        # Notification-only actions do not mutate Meta. A persisted PENDING
        # claim is treated as delivered-or-ambiguous to avoid duplicates.
        return observed == desired

    @staticmethod
    async def _get_or_create_schedule_state(
        session,
        *,
        state_key: str,
        account: Account,
        rule_key: str = "",
        state_cache: Optional[dict[str, AutomationScheduleState]] = None,
    ) -> AutomationScheduleState:
        if state_cache is not None and state_key in state_cache:
            return state_cache[state_key]

        state = (
            await session.execute(
                select(AutomationScheduleState).where(
                    AutomationScheduleState.state_key == state_key
                )
            )
        ).scalar_one_or_none()
        if state is not None:
            if state_cache is not None:
                state_cache[state_key] = state
            return state

        state = AutomationScheduleState(
            state_key=state_key,
            owner_user_id=account.owner_user_id,
            account_id=str(account.account_id),
            rule_key=rule_key,
            last_checked_at=0.0,
        )
        try:
            async with session.begin_nested():
                session.add(state)
                await session.flush()
            if state_cache is not None:
                state_cache[state_key] = state
            return state
        except IntegrityError:
            refreshed = (
                await session.execute(
                    select(AutomationScheduleState).where(
                        AutomationScheduleState.state_key == state_key
                    )
                )
            ).scalar_one()
            if state_cache is not None:
                state_cache[state_key] = refreshed
            return refreshed

    async def _claim_execution(
        self,
        session,
        account: Account,
        evaluation: RuleEvaluationResult,
        *,
        observed_state: dict[str, Any],
        desired_state: dict[str, Any],
        now: float,
    ) -> tuple[bool, str, RuleExecutionState]:
        """Claim one action before Meta mutation and reconcile ambiguous attempts."""

        execution_key, rule_key = self._execution_key(account, evaluation)
        query = select(RuleExecutionState).where(
            RuleExecutionState.execution_key == execution_key
        )
        state = (await session.execute(query.with_for_update())).scalar_one_or_none()
        if state is None:
            state = RuleExecutionState(
                execution_key=execution_key,
                owner_user_id=account.owner_user_id,
                account_id=str(account.account_id),
                adset_id=str(evaluation.adset_id),
                rule_key=rule_key,
                action=evaluation.action.value,
            )
            session.add(state)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            state = (await session.execute(query.with_for_update())).scalar_one()

        if state.status == "PENDING":
            pending_target = self._json_dict(state.after_state)
            if self._state_matches(observed_state, pending_target):
                state.status = "SUCCESS"
                state.last_success_at = state.last_attempt_at or now
                state.details = {"reconciled_after_restart": True}
                await session.commit()
                return False, "reconciled", state
            if now - float(state.last_attempt_at or 0.0) < PENDING_RECONCILIATION_SECONDS:
                await session.commit()
                return False, "pending", state
            state.status = "ERROR"
            state.details = {"reason": "stale_pending_not_confirmed"}
            await session.commit()
            state = (await session.execute(query.with_for_update())).scalar_one()

        cooldown_minutes = max(0, int(evaluation.cooldown_minutes or 0))
        cooldown_seconds = cooldown_minutes * 60
        if (
            cooldown_seconds > 0
            and state.last_success_at is not None
            and now - float(state.last_success_at) < cooldown_seconds
        ):
            await session.commit()
            return False, "cooldown", state

        state.owner_user_id = account.owner_user_id
        state.status = "PENDING"
        state.correlation_id = self._current_cycle_id
        state.last_attempt_at = now
        state.before_state = observed_state
        state.after_state = desired_state
        state.details = {}
        await session.commit()
        return True, "claimed", state

    async def _confirm_stop_evaluation(
        self,
        session,
        account: Account,
        evaluation: RuleEvaluationResult,
        *,
        now: float,
        confirmation_seconds: int,
        max_gap_seconds: int,
    ) -> tuple[bool, str, Optional[RuleExecutionState]]:
        """Require a durable sequence of matching reads before a destructive STOP."""

        if confirmation_seconds <= 0:
            return True, "disabled", None

        execution_key, rule_key = self._execution_key(account, evaluation)
        query = select(RuleExecutionState).where(
            RuleExecutionState.execution_key == execution_key
        )
        state = (await session.execute(query.with_for_update())).scalar_one_or_none()
        if state is None:
            state = RuleExecutionState(
                execution_key=execution_key,
                owner_user_id=account.owner_user_id,
                account_id=str(account.account_id),
                adset_id=str(evaluation.adset_id),
                rule_key=rule_key,
                action=evaluation.action.value,
            )
            session.add(state)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            state = (await session.execute(query.with_for_update())).scalar_one()

        # Let the normal claim path reconcile a Meta mutation that may have
        # completed immediately before a worker restart.
        if state.status == "PENDING":
            await session.commit()
            return True, "execution_pending", state

        details = self._json_dict(state.details)
        try:
            first_seen_at = float(details.get("first_seen_at", 0.0) or 0.0)
            last_seen_at = float(details.get("last_seen_at", 0.0) or 0.0)
            observations = int(details.get("observations", 0) or 0)
        except (TypeError, ValueError):
            first_seen_at = 0.0
            last_seen_at = 0.0
            observations = 0

        is_continuous = (
            state.status == "STOP_CONFIRMING"
            and first_seen_at > 0
            and last_seen_at > 0
            and now >= last_seen_at
            and now - last_seen_at <= max(1, max_gap_seconds)
        )
        if not is_continuous:
            state.status = "STOP_CONFIRMING"
            state.owner_user_id = account.owner_user_id
            state.correlation_id = self._current_cycle_id
            state.last_attempt_at = now
            state.before_state = {"status": "ACTIVE"}
            state.after_state = {"status": "PAUSED"}
            state.details = {
                "first_seen_at": now,
                "last_seen_at": now,
                "observations": 1,
                "confirmation_seconds": confirmation_seconds,
            }
            await session.commit()
            return False, "started", state

        observations += 1
        state.last_attempt_at = now
        state.details = {
            "first_seen_at": first_seen_at,
            "last_seen_at": now,
            "observations": observations,
            "confirmation_seconds": confirmation_seconds,
        }
        if now - first_seen_at < confirmation_seconds:
            await session.commit()
            return False, "waiting", state

        state.status = "IDLE"
        state.details = {
            "confirmed_at": now,
            "first_seen_at": first_seen_at,
            "observations": observations,
            "confirmation_seconds": confirmation_seconds,
        }
        await session.commit()
        return True, "confirmed", state

    async def _reset_stop_confirmations(
        self,
        session,
        account: Account,
        adset_id: str,
        *,
        keep_execution_key: Optional[str] = None,
        now: float,
    ) -> int:
        """Cancel stale STOP candidates when a scheduled STOP check no longer matches."""

        rows = (
            await session.execute(
                select(RuleExecutionState)
                .where(
                    RuleExecutionState.account_id == str(account.account_id),
                    RuleExecutionState.adset_id == str(adset_id),
                    RuleExecutionState.action == RuleAction.STOP.value,
                    RuleExecutionState.status == "STOP_CONFIRMING",
                )
                .with_for_update()
            )
        ).scalars().all()
        reset_count = 0
        for state in rows:
            if keep_execution_key and state.execution_key == keep_execution_key:
                continue
            state.status = "IDLE"
            state.correlation_id = self._current_cycle_id
            state.details = {
                "cancelled_at": now,
                "reason": "stop_condition_no_longer_matched",
            }
            reset_count += 1
        if reset_count:
            await session.commit()
        return reset_count

    @staticmethod
    def _finish_execution(
        state: RuleExecutionState,
        *,
        status: str,
        now: float,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        state.status = status
        if status == "SUCCESS":
            state.last_success_at = now
        state.details = details or {}

    @staticmethod
    async def _record_stopped_adset(session, account: Account, result: RuleEvaluationResult) -> None:
        query = await session.execute(
            select(StoppedAdSet).where(StoppedAdSet.adset_id == result.adset_id)
        )
        stopped = query.scalar_one_or_none()
        stopped_at = datetime.now(timezone.utc)
        if stopped:
            stopped.account_id = account.account_id
            stopped.adset_name = result.adset_name
            stopped.stop_spend = result.spend
            stopped.stop_leads = result.leads
            stopped.stop_registrations = result.registrations
            stopped.is_resolved = False
            stopped.stopped_at = stopped_at
        else:
            session.add(
                StoppedAdSet(
                    account_id=account.account_id,
                    adset_id=result.adset_id,
                    adset_name=result.adset_name,
                    stop_spend=result.spend,
                    stop_leads=result.leads,
                    stop_registrations=result.registrations,
                    is_resolved=False,
                    stopped_at=stopped_at,
                )
            )

    @staticmethod
    async def _resolve_stopped_adset(session, adset_id: str) -> None:
        query = await session.execute(
            select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id)
        )
        stopped = query.scalar_one_or_none()
        if stopped:
            stopped.is_resolved = True

    async def _persist_audit_event(
        self,
        session,
        account: Account,
        *,
        event_type: str,
        status: str,
        evaluation: Optional[RuleEvaluationResult] = None,
        category: str = "RULE_ACTION",
        action: str = "",
        message: str = "",
        before_state: Any = None,
        after_state: Any = None,
        details: Any = None,
        duration_ms: int = 0,
    ) -> Optional[int]:
        """Persist audit independently from Telegram without breaking automation."""

        try:
            if account.workspace_id is None:
                raise ValueError(
                    f"Account {account.account_id} has no workspace; audit event is quarantined"
                )
            if account.owner_user_id is None:
                workspace_owner_id = (
                    await session.execute(
                        select(Workspace.owner_user_id).where(
                            Workspace.id == account.workspace_id
                        )
                    )
                ).scalar_one_or_none()
                if workspace_owner_id is None:
                    raise ValueError(
                        f"Account {account.account_id} has no resolvable audit owner"
                    )
                account.owner_user_id = workspace_owner_id

            audit_event = build_audit_event(
                account=account,
                event_type=event_type,
                status=status,
                correlation_id=self._current_cycle_id,
                category=category,
                evaluation=evaluation,
                action=action,
                message=message,
                before_state=before_state,
                after_state=after_state,
                details=details,
                duration_ms=duration_ms,
            )
            session.add(audit_event)
            await session.flush()
            audit_event_id = audit_event.id
            await session.commit()
            return audit_event_id
        except Exception as audit_error:
            await session.rollback()
            logger.error("Failed to persist audit event %s: %s", event_type, audit_error)
            return None

    async def _handle_token_error(
        self,
        session,
        acc: Account,
        error: Exception,
        connection_cache: dict[int, MetaConnection],
        notification_target: str,
    ) -> None:
        logger.error("Token error for account %s: %s", acc.account_id, error)
        acc.is_active = False

        subcode = getattr(error, "subcode", None)
        subcode_key = getattr(error, "subcode_key", "UNKNOWN")
        title = getattr(error, "title", "")
        description = getattr(error, "description", "")
        action_hint = getattr(error, "action_hint", "")
        user_msg = getattr(error, "error_user_msg", "")
        fbtrace_id = getattr(error, "fbtrace_id", "")
        error_code = getattr(error, "code", 190)

        if acc.meta_connection_id and acc.meta_connection_id in connection_cache:
            conn = connection_cache[acc.meta_connection_id]
            conn.status = "error"
            conn.last_error = description or str(error)

        await self._persist_audit_event(
            session,
            acc,
            event_type="TOKEN_EXPIRED",
            status="ERROR",
            category="ACCOUNT_HEALTH",
            action="DISABLE_MONITORING",
            message=description or str(error),
            before_state={"is_active": True},
            after_state={"is_active": False},
            details={
                "error_code": error_code,
                "error_subcode": subcode,
                "subcode_key": subcode_key,
                "subcode_title": title,
                "subcode_description": description,
                "action_hint": action_hint,
                "error_user_msg": user_msg,
                "fbtrace_id": fbtrace_id,
            },
        )
        if self.telegram_notifier:
            await self.telegram_notifier(
                event_type="TOKEN_EXPIRED",
                account_name=acc.name,
                account_id=acc.account_id,
                target_chat_id=notification_target,
                subcode=subcode,
                subcode_title=title,
                subcode_description=description,
                action_hint=action_hint,
                user_msg=user_msg,
            )

    async def run_day_boundary_cycle(self) -> dict:
        """Notify once when each connected account enters a new local date."""

        self._current_cycle_id = uuid.uuid4().hex
        stats = {
            "cycle_id": self._current_cycle_id,
            "accounts_seen": 0,
            "dates_initialized": 0,
            "days_notified": 0,
            "boundaries_missed": 0,
            "invalid_timezones": 0,
            "errors": [],
        }
        now_ts = self._clock()

        async with async_session_maker() as session:
            accounts = (await session.execute(select(Account))).scalars().all()
            stats["accounts_seen"] = len(accounts)
            owner_user_ids = {
                account.owner_user_id
                for account in accounts
                if account.owner_user_id is not None
            }
            owner_chat_ids = {}
            if owner_user_ids:
                owners = (
                    await session.execute(
                        select(User).where(User.id.in_(owner_user_ids))
                    )
                ).scalars().all()
                owner_chat_ids = {
                    owner.id: str(owner.telegram_id or "")
                    for owner in owners
                }

            for account in accounts:
                account_id = str(account.account_id)
                account_name = str(account.name)
                notification_target = owner_chat_ids.get(account.owner_user_id) or ""
                clock = resolve_account_clock(account.timezone_name)
                if clock is None:
                    stats["invalid_timezones"] += 1
                    stats["errors"].append(
                        f"Account {account_id}: unknown timezone {account.timezone_name!r}"
                    )
                    logger.error(
                        "Account %s day boundary skipped: unknown timezone %r",
                        account_id,
                        account.timezone_name,
                    )
                    continue

                local_now = datetime.fromtimestamp(now_ts, timezone.utc).astimezone(clock.zone)
                decision = evaluate_day_boundary(
                    account.last_day_start_date,
                    local_now,
                    notification_window_minutes=DAY_BOUNDARY_NOTIFICATION_WINDOW_MINUTES,
                )
                timezone_changed = clock.canonical_name != account.timezone_name
                if timezone_changed:
                    account.timezone_name = clock.canonical_name
                if not decision.should_update:
                    if timezone_changed:
                        await session.commit()
                    continue

                previous_date = str(account.last_day_start_date or "")
                account.last_day_start_date = decision.current_date
                if not decision.should_notify:
                    if decision.reason == "initialized":
                        stats["dates_initialized"] += 1
                    else:
                        stats["boundaries_missed"] += 1
                        logger.warning(
                            "Account %s new local date %s was observed outside the midnight window",
                            account_id,
                            decision.current_date,
                        )
                    await session.commit()
                    continue

                offset = utc_offset_label(local_now)
                local_time = local_now.strftime("%H:%M")
                local_date = local_now.strftime("%d.%m.%Y")
                audit_event_id = await self._persist_audit_event(
                    session,
                    account,
                    event_type="ACCOUNT_DAY_STARTED",
                    status="SUCCESS",
                    category="MONITORING",
                    action="DETECT_ACCOUNT_DAY_BOUNDARY",
                    message=(
                        f"В кабинете начались новые сутки: {decision.current_date} "
                        f"в {local_time} ({clock.canonical_name}, {offset})"
                    ),
                    before_state={"last_day_start_date": previous_date},
                    after_state={"last_day_start_date": decision.current_date},
                    details={
                        "timezone_name": clock.canonical_name,
                        "utc_offset": offset,
                        "local_date": decision.current_date,
                        "local_time": local_time,
                    },
                )
                if audit_event_id is None:
                    stats["errors"].append(
                        f"Account {account_id}: failed to persist account day boundary"
                    )
                    continue

                if self.telegram_notifier:
                    await self.telegram_notifier(
                        event_type="ACCOUNT_DAY_STARTED",
                        account_name=account_name,
                        account_id=account_id,
                        target_chat_id=notification_target,
                        timezone_name=clock.canonical_name,
                        local_time=local_time,
                        local_date=local_date,
                        utc_offset=offset,
                    )
                stats["days_notified"] += 1
                logger.info(
                    "Account %s entered local date %s at %s (%s, %s)",
                    account_id,
                    decision.current_date,
                    local_time,
                    clock.canonical_name,
                    offset,
                )

        return stats

    async def run_cycle(self) -> dict:
        cycle_started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        self._current_cycle_id = uuid.uuid4().hex
        stats = {
            "cycle_id": self._current_cycle_id,
            "accounts_checked": 0,
            "accounts_skipped": 0,
            "rules_checked": 0,
            "adsets_checked": 0,
            "adsets_stopped": 0,
            "adsets_reactivated": 0,
            "budgets_changed": 0,
            "actions_skipped": 0,
            "actions_reconciled": 0,
            "stop_confirmations_waiting": 0,
            "proposals_sent": 0,
            "errors": []
        }

        async with async_session_maker() as session:
            # 1. Загружаем все активные аккаунты
            stmt = select(Account).where(Account.is_active == True)
            result = await session.execute(stmt)
            accounts = result.scalars().all()
            if not accounts:
                await self._persist_runtime_state(
                    stats=stats,
                    started_at=started_at,
                    duration_ms=(time.perf_counter() - cycle_started) * 1000,
                )
                return stats

            owner_user_ids = {
                account.owner_user_id
                for account in accounts
                if account.owner_user_id is not None
            }
            owner_chat_ids = {}
            if owner_user_ids:
                owner_rows = (
                    await session.execute(
                        select(User).where(User.id.in_(owner_user_ids))
                    )
                ).scalars().all()
                owner_chat_ids = {
                    owner.id: str(owner.telegram_id or "")
                    for owner in owner_rows
                }

            # Пакетная предзагрузка AutomationScheduleState (с чанкованием по 500)
            account_ids = [str(acc.account_id) for acc in accounts]
            schedule_cache: dict[str, AutomationScheduleState] = {}
            for i in range(0, len(account_ids), 500):
                chunk = account_ids[i:i + 500]
                chunk_rows = (
                    await session.execute(
                        select(AutomationScheduleState).where(
                            AutomationScheduleState.account_id.in_(chunk)
                        )
                    )
                ).scalars().all()
                for row in chunk_rows:
                    schedule_cache[row.state_key] = row

            # Пакетная предзагрузка MetaConnection (с чанкованием по 500)
            connection_ids = {
                acc.meta_connection_id
                for acc in accounts
                if acc.meta_connection_id is not None
            }
            connection_cache: dict[int, MetaConnection] = {}
            if connection_ids:
                conn_id_list = list(connection_ids)
                for i in range(0, len(conn_id_list), 500):
                    chunk_conn_ids = conn_id_list[i:i + 500]
                    conn_rows = (
                        await session.execute(
                            select(MetaConnection).where(
                                MetaConnection.id.in_(chunk_conn_ids)
                            )
                        )
                    ).scalars().all()
                    for conn in conn_rows:
                        connection_cache[conn.id] = conn

            settings_result = await session.execute(select(AppSettings).limit(1))
            app_settings = settings_result.scalar_one_or_none()
            default_interval = self._interval_minutes(
                app_settings.poll_interval_minutes if app_settings else 10,
                10,
            )
            critical_interval = self._interval_minutes(
                app_settings.critical_rule_interval_minutes if app_settings else 2,
                2,
            )
            stop_confirmation_minutes = max(
                0,
                min(
                    60,
                    int(app_settings.stop_confirmation_minutes if app_settings else 10),
                ),
            )
            health_interval = self._interval_minutes(
                app_settings.account_health_interval_minutes if app_settings else 15,
                15,
            )
            max_concurrent_accounts = self._interval_minutes(
                app_settings.max_concurrent_accounts if app_settings else 3,
                3,
            )
            max_concurrent_actions = self._interval_minutes(
                app_settings.max_concurrent_actions if app_settings else 3,
                3,
            )
            if hasattr(self.meta_client, "configure_automation"):
                self.meta_client.configure_automation(
                    inventory_cache_minutes=(
                        app_settings.inventory_cache_minutes if app_settings else 5
                    ),
                    adaptive_polling_enabled=(
                        app_settings.adaptive_polling_enabled if app_settings else True
                    ),
                    usage_soft_limit_percent=(
                        app_settings.usage_soft_limit_percent if app_settings else 60
                    ),
                    usage_hard_limit_percent=(
                        app_settings.usage_hard_limit_percent if app_settings else 80
                    ),
                )
            read_semaphore = asyncio.Semaphore(max_concurrent_accounts)
            self._action_semaphore = asyncio.Semaphore(max_concurrent_actions)

            prepared_accounts = []
            for acc in accounts:
                account_ref = str(acc.account_id)
                notification_target = owner_chat_ids.get(acc.owner_user_id) or ""
                if acc.meta_connection_id and acc.meta_connection_id in connection_cache:
                    conn = connection_cache[acc.meta_connection_id]
                    if (
                        acc.workspace_id is not None
                        and conn.workspace_id is not None
                        and conn.workspace_id != acc.workspace_id
                    ):
                        logger.warning(
                            "Skipping account %s: Meta connection %s workspace mismatch (%s != %s)",
                            acc.account_id,
                            conn.id,
                            conn.workspace_id,
                            acc.workspace_id,
                        )
                        await self._set_account_health(
                            session,
                            acc,
                            success=False,
                            notification_target=notification_target,
                            error="Meta connection workspace mismatch",
                            cause="system",
                            signals={"token_healthy": False},
                        )
                        continue
                    if conn.status in ("expired", "needs_reconnect", "missing_scopes", "error"):
                        logger.info(
                            "Skipping rule checks for account %s: Meta connection %s is in %s state",
                            acc.account_id,
                            conn.id,
                            conn.status,
                        )
                        await self._set_account_health(
                            session,
                            acc,
                            success=False,
                            notification_target=notification_target,
                            error=f"Meta connection requires user action: {conn.status}",
                            cause="user",
                            signals={"token_healthy": False, "connection_status": conn.status},
                        )
                        continue

                now = self._clock()
                active_rules = self._load_rules(
                    acc.active_rules,
                    workspace_id=acc.workspace_id,
                )
                due_rule_entries = []
                if acc.rules_enabled:
                    for index, rule in enumerate(active_rules):
                        rule_key = self._rule_key(acc.account_id, index, rule)
                        interval = self._interval_minutes(
                            rule.get("check_interval"),
                            default_interval,
                        )
                        if self._is_critical_stop_rule(rule):
                            interval = min(interval, critical_interval)
                        state_key = self._schedule_key("rule", acc.account_id, rule_key)
                        schedule_state = await self._get_or_create_schedule_state(
                            session,
                            state_key=state_key,
                            account=acc,
                            rule_key=rule_key,
                            state_cache=schedule_cache,
                        )
                        if (
                            schedule_state.last_checked_at <= 0
                            or now - schedule_state.last_checked_at >= interval * 60
                        ):
                            due_rule_entries.append((rule_key, rule, schedule_state))

                account_state = await self._get_or_create_schedule_state(
                    session,
                    state_key=self._schedule_key("account", acc.account_id),
                    account=acc,
                    state_cache=schedule_cache,
                )
                account_monitor_due = (
                    account_state.last_checked_at <= 0
                    or now - account_state.last_checked_at >= default_interval * 60
                )
                health_state = await self._get_or_create_schedule_state(
                    session,
                    state_key=self._schedule_key("health", acc.account_id),
                    account=acc,
                    state_cache=schedule_cache,
                )
                health_due = (
                    health_state.last_checked_at <= 0
                    or now - health_state.last_checked_at >= health_interval * 60
                )
                currency_refresh_due = normalize_currency(acc.currency) == "UNKNOWN"
                if (
                    not account_monitor_due
                    and not due_rule_entries
                    and not health_due
                    and not currency_refresh_due
                ):
                    stats["accounts_skipped"] += 1
                    continue

                if account_monitor_due:
                    account_state.last_checked_at = now
                for _, _, schedule_state in due_rule_entries:
                    schedule_state.last_checked_at = now
                if health_due or currency_refresh_due:
                    health_state.last_checked_at = now

                due_rules = [rule for _, rule, _ in due_rule_entries]
                stats["accounts_checked"] += 1
                stats["rules_checked"] += len(due_rules)
                try:
                    access_token = await resolve_account_access_token(
                        session, acc, connection_cache=connection_cache
                    )
                except Exception as error:
                    stats["errors"].append(f"Account {account_ref}: {error}")
                    await self._set_account_health(
                        session,
                        acc,
                        success=False,
                        notification_target=notification_target,
                        error=error,
                        signals={"token_healthy": False},
                    )
                    continue
                prepared_accounts.append(
                    {
                        "account": acc,
                        "account_ref": account_ref,
                        "notification_target": notification_target,
                        "now": now,
                        "due_rules": due_rules,
                        "health_due": health_due or currency_refresh_due,
                        "access_token": access_token,
                    }
                )

            await session.commit()
            snapshots = await asyncio.gather(
                *(
                    self._fetch_account_snapshot(
                        account=item["account"],
                        access_token=item["access_token"],
                        due_rules=item["due_rules"],
                        health_due=item["health_due"],
                        semaphore=read_semaphore,
                    )
                    for item in prepared_accounts
                ),
                return_exceptions=True,
            )

            for item, snapshot in zip(prepared_accounts, snapshots):
                acc = item["account"]
                account_ref = item["account_ref"]
                notification_target = item["notification_target"]
                now = item["now"]
                due_rules = item["due_rules"]
                access_token = item["access_token"]
                try:
                    if isinstance(snapshot, PermissionError):
                        await self._handle_token_error(
                            session,
                            acc,
                            snapshot,
                            connection_cache,
                            notification_target,
                        )
                        await self._set_account_health(
                            session,
                            acc,
                            success=False,
                            notification_target=notification_target,
                            error=snapshot,
                            cause="user",
                            signals={"token_healthy": False},
                        )
                        continue
                    if isinstance(snapshot, Exception):
                        raise snapshot

                    acc_info = snapshot.get("account_info")
                    if acc_info:
                        acc.currency = normalize_currency(acc_info.get("currency"))
                        refreshed_timezone = canonical_timezone_name(
                            acc_info.get("timezone_name") or acc.timezone_name
                        )
                        if refreshed_timezone != acc.timezone_name:
                            acc.timezone_name = refreshed_timezone
                            acc.last_day_start_date = ""
                        status_code = acc_info.get("account_status", 1)
                        status_label = acc_info.get("status_label", f"Статус #{status_code}")
                        acc.account_status = status_code
                        acc.status_label = status_label
                        if status_code != 1:
                            logger.warning(f"Account {acc.account_id} has issue: {status_label}")
                            acc.is_active = False
                            await self._persist_audit_event(
                                session,
                                acc,
                                event_type="ACCOUNT_ISSUE",
                                status="WARNING",
                                category="ACCOUNT_HEALTH",
                                action="DISABLE_MONITORING",
                                message=status_label,
                                before_state={"is_active": True, "account_status": acc.account_status},
                                after_state={"is_active": False, "account_status": status_code},
                                details={"status_label": status_label},
                            )
                            if self.telegram_notifier:
                                await self.telegram_notifier(
                                    event_type="ACCOUNT_ISSUE",
                                    account_name=acc.name,
                                    account_id=acc.account_id,
                                    target_chat_id=notification_target,
                                    local_time=status_label
                                )
                            await self._set_account_health(
                                session,
                                acc,
                                success=False,
                                notification_target=notification_target,
                                error=f"Account status: {status_label}",
                                cause="user",
                                signals={"token_healthy": True, "account_active": False},
                            )
                            continue
                    acc.currency = snapshot["currency"]
                    window_errors = snapshot.get("window_errors") or []
                    if window_errors and due_rules:
                        stats["errors"].extend(
                            f"Account {account_ref} window: {error}"
                            for error in window_errors
                        )
                        await self._set_account_health(
                            session,
                            acc,
                            success=False,
                            notification_target=notification_target,
                            error=window_errors[0],
                            signals={"token_healthy": True, "account_active": True},
                        )
                        continue
                    insights_by_window = snapshot["insights_by_window"]
                    adsets = snapshot["adsets"]
                    stats["adsets_checked"] += len(adsets)

                    await self._set_account_health(
                        session,
                        acc,
                        success=True,
                        notification_target=notification_target,
                        signals={
                            "token_healthy": True,
                            "account_active": True,
                            "meta_read_ok": True,
                            "window_errors_count": len(window_errors),
                        },
                    )

                    if not acc.rules_enabled or not due_rules:
                        continue

                    stop_rules_due = any(
                        self._is_critical_stop_rule(rule) for rule in due_rules
                    )

                    for adset in adsets:
                        a_id = str(adset["adset_id"])
                        current_adset_windows = {
                            window: rows_by_adset.get(a_id, {})
                            for window, rows_by_adset in insights_by_window.items()
                        }

                        eval_res = RuleEngine.evaluate(
                            adset=adset,
                            account=acc,
                            insights_by_window=current_adset_windows,
                            active_rules_override=due_rules,
                        )
                        
                        should_notify_tg = eval_res.notify_tg
                        if eval_res.action == RuleAction.NOOP:
                            if stop_rules_due:
                                await self._reset_stop_confirmations(
                                    session,
                                    acc,
                                    a_id,
                                    now=now,
                                )
                            continue

                        if stop_rules_due:
                            keep_execution_key = None
                            if eval_res.action == RuleAction.STOP:
                                keep_execution_key, _ = self._execution_key(acc, eval_res)
                            await self._reset_stop_confirmations(
                                session,
                                acc,
                                a_id,
                                keep_execution_key=keep_execution_key,
                                now=now,
                            )

                        current_budget = float(adset.get("daily_budget", 0.0) or 0.0)
                        observed_state: dict[str, Any]
                        desired_state: dict[str, Any]
                        if eval_res.action == RuleAction.STOP:
                            observed_state = {"status": adset.get("status", "UNKNOWN")}
                            desired_state = {"status": "PAUSED"}
                        elif eval_res.action == RuleAction.AUTO_REACTIVATE:
                            observed_state = {"status": adset.get("status", "UNKNOWN")}
                            desired_state = {"status": "ACTIVE"}
                        elif eval_res.action == RuleAction.INCREASE_BUDGET:
                            if current_budget <= 0 or eval_res.budget_change_percent <= 0:
                                stats["actions_skipped"] += 1
                                continue
                            new_budget = current_budget * (1 + eval_res.budget_change_percent / 100.0)
                            if eval_res.budget_max_daily > 0:
                                new_budget = min(new_budget, eval_res.budget_max_daily)
                            observed_state = {"daily_budget": current_budget}
                            desired_state = {"daily_budget": new_budget}
                        elif eval_res.action == RuleAction.DECREASE_BUDGET:
                            if current_budget <= 0 or eval_res.budget_change_percent <= 0:
                                stats["actions_skipped"] += 1
                                continue
                            new_budget = max(
                                current_budget * (1 - eval_res.budget_change_percent / 100.0),
                                1.0,
                            )
                            observed_state = {"daily_budget": current_budget}
                            desired_state = {"daily_budget": new_budget}
                        else:
                            observed_state = {"status": adset.get("status", "UNKNOWN")}
                            desired_state = dict(observed_state)

                        if eval_res.action == RuleAction.STOP:
                            confirmed, confirmation_reason, confirmation_state = (
                                await self._confirm_stop_evaluation(
                                    session,
                                    acc,
                                    eval_res,
                                    now=now,
                                    confirmation_seconds=stop_confirmation_minutes * 60,
                                    max_gap_seconds=max(180, critical_interval * 60 * 3),
                                )
                            )
                            if not confirmed:
                                stats["actions_skipped"] += 1
                                stats["stop_confirmations_waiting"] += 1
                                if confirmation_reason == "started" and confirmation_state:
                                    await self._persist_audit_event(
                                        session,
                                        acc,
                                        event_type="STOP_CONFIRMATION_STARTED",
                                        status="WAITING",
                                        category="RULE_ENGINE",
                                        evaluation=eval_res,
                                        action=RuleAction.STOP.value,
                                        message=(
                                            "STOP-кандидат найден. Buyerly повторно проверит "
                                            f"метрики в течение {stop_confirmation_minutes} мин."
                                        ),
                                        before_state=observed_state,
                                        after_state=desired_state,
                                        details={
                                            "confirmation_minutes": stop_confirmation_minutes,
                                            "execution_key": confirmation_state.execution_key,
                                        },
                                    )
                                continue

                        claimed, claim_reason, execution_state = await self._claim_execution(
                            session,
                            acc,
                            eval_res,
                            observed_state=observed_state,
                            desired_state=desired_state,
                            now=now,
                        )
                        if not claimed:
                            stats["actions_skipped"] += 1
                            if claim_reason == "reconciled":
                                stats["actions_reconciled"] += 1
                            if claim_reason in {"cooldown", "pending", "reconciled"}:
                                await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type=(
                                        "RULE_ACTION_RECONCILED"
                                        if claim_reason == "reconciled"
                                        else "RULE_ACTION_COOLDOWN"
                                        if claim_reason == "cooldown"
                                        else "RULE_ACTION_PENDING"
                                    ),
                                    status="SUCCESS" if claim_reason == "reconciled" else "SKIPPED",
                                    evaluation=eval_res,
                                    action=eval_res.action.value,
                                    message={
                                        "cooldown": f"Действие пропущено: cooldown {eval_res.cooldown_minutes} мин.",
                                        "pending": "Действие уже начато в предыдущем цикле; дубль заблокирован.",
                                        "reconciled": "Результат предыдущего действия подтверждён по текущему состоянию Meta.",
                                    }[claim_reason],
                                    before_state=observed_state,
                                    after_state=desired_state,
                                    details={
                                        "claim_reason": claim_reason,
                                        "execution_key": execution_state.execution_key,
                                    },
                                )
                            continue

                        # СТОП адсета
                        if eval_res.action == RuleAction.STOP:
                            action_started = time.perf_counter()
                            try:
                                async with self._action_semaphore:
                                    await self.meta_client.set_adset_status(
                                        adset_id=a_id,
                                        access_token=access_token,
                                        status="PAUSED",
                                        account_id=acc.account_id,
                                    )
                                await AdsetInventoryService.update_adset_status(
                                    session, acc.account_id, a_id, "PAUSED"
                                )
                                stats["adsets_stopped"] += 1
                                logger.info(f"STOPPED AdSet: {a_id} ({eval_res.adset_name}) - {eval_res.reason}")

                                try:
                                    await self._record_stopped_adset(session, acc, eval_res)
                                except Exception as db_error:
                                    await session.rollback()
                                    logger.error(f"Failed to persist stopped adset {a_id}: {db_error}")
                                    stats["errors"].append(f"Stopped-adset persistence error {a_id}: {db_error}")

                                self._finish_execution(
                                    execution_state,
                                    status="SUCCESS",
                                    now=now,
                                )
                                audit_event_id = await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type="STOP",
                                    status="SUCCESS",
                                    evaluation=eval_res,
                                    before_state={"status": adset.get("status", "ACTIVE")},
                                    after_state={"status": "PAUSED"},
                                    duration_ms=(time.perf_counter() - action_started) * 1000,
                                )

                                if should_notify_tg and self.telegram_notifier:
                                    await self.telegram_notifier(
                                        event_type="STOP",
                                        eval_result=eval_res,
                                        account_name=acc.name,
                                        account_id=acc.account_id,
                                        target_chat_id=notification_target,
                                        audit_event_id=audit_event_id,
                                    )
                            except Exception as e:
                                logger.error(f"Error pausing adset {a_id}: {e}")
                                stats["errors"].append(f"Pause error {a_id}: {e}")
                                self._finish_execution(
                                    execution_state,
                                    status="ERROR",
                                    now=now,
                                    details={"error": str(e)},
                                )
                                await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type="STOP",
                                    status="ERROR",
                                    evaluation=eval_res,
                                    message=str(e),
                                    before_state={"status": adset.get("status", "UNKNOWN")},
                                    after_state={"status": adset.get("status", "UNKNOWN")},
                                    duration_ms=(time.perf_counter() - action_started) * 1000,
                                )

                        # ТОЛЬКО УВЕДОМЛЕНИЕ (Send notification only)
                        elif eval_res.action == RuleAction.NOTIFY_ONLY:
                            logger.info(f"NOTIFY ONLY AdSet: {a_id} ({eval_res.adset_name}) - {eval_res.reason}")
                            self._finish_execution(
                                execution_state,
                                status="SUCCESS",
                                now=now,
                                details={"telegram_requested": should_notify_tg},
                            )
                            await self._persist_audit_event(
                                session,
                                acc,
                                event_type="NOTIFY_ONLY",
                                status="SUCCESS",
                                evaluation=eval_res,
                                before_state={"status": adset.get("status", "UNKNOWN")},
                                after_state={"status": adset.get("status", "UNKNOWN")},
                                details={"telegram_requested": should_notify_tg},
                            )
                            if should_notify_tg and self.telegram_notifier:
                                await self.telegram_notifier(
                                    event_type="NOTIFY_ONLY",
                                    eval_result=eval_res,
                                    account_name=acc.name,
                                    account_id=acc.account_id,
                                    target_chat_id=notification_target
                                )

                        # ПРЕДЛОЖЕНИЕ ВКЛЮЧИТЬ (долет)
                        elif eval_res.action == RuleAction.PROPOSE_REACTIVATE:
                            stats["proposals_sent"] += 1
                            logger.info(f"PROPOSE REACTIVATE AdSet: {a_id} ({eval_res.adset_name}) - {eval_res.reason}")
                            self._finish_execution(
                                execution_state,
                                status="SUCCESS",
                                now=now,
                                details={"telegram_requested": should_notify_tg},
                            )
                            await self._persist_audit_event(
                                session,
                                acc,
                                event_type="PROPOSE_REACTIVATE",
                                status="SUCCESS",
                                evaluation=eval_res,
                                before_state={"status": adset.get("status", "UNKNOWN")},
                                after_state={"status": adset.get("status", "UNKNOWN")},
                                details={"telegram_requested": should_notify_tg},
                            )

                            if should_notify_tg and self.telegram_notifier:
                                await self.telegram_notifier(
                                    event_type="PROPOSE_REACTIVATE",
                                    eval_result=eval_res,
                                    account_name=acc.name,
                                    account_id=acc.account_id,
                                    target_chat_id=notification_target
                                )

                        # АВТО-ВКЛЮЧЕНИЕ
                        elif eval_res.action == RuleAction.AUTO_REACTIVATE:
                            action_started = time.perf_counter()
                            try:
                                async with self._action_semaphore:
                                    await self.meta_client.set_adset_status(
                                        adset_id=a_id,
                                        access_token=access_token,
                                        status="ACTIVE",
                                        account_id=acc.account_id,
                                    )
                                await AdsetInventoryService.update_adset_status(
                                    session, acc.account_id, a_id, "ACTIVE"
                                )
                                stats["adsets_reactivated"] += 1

                                try:
                                    await self._resolve_stopped_adset(session, a_id)
                                except Exception as db_error:
                                    await session.rollback()
                                    logger.error(f"Failed to resolve stopped adset {a_id}: {db_error}")
                                    stats["errors"].append(f"Stopped-adset resolution error {a_id}: {db_error}")

                                self._finish_execution(
                                    execution_state,
                                    status="SUCCESS",
                                    now=now,
                                )
                                audit_event_id = await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type="AUTO_REACTIVATE",
                                    status="SUCCESS",
                                    evaluation=eval_res,
                                    before_state={"status": adset.get("status", "PAUSED")},
                                    after_state={"status": "ACTIVE"},
                                    duration_ms=(time.perf_counter() - action_started) * 1000,
                                )
                                
                                logger.info(f"AUTO REACTIVATED AdSet: {a_id} ({eval_res.adset_name})")

                                if should_notify_tg and self.telegram_notifier:
                                    await self.telegram_notifier(
                                        event_type="AUTO_REACTIVATE",
                                        eval_result=eval_res,
                                        account_name=acc.name,
                                        account_id=acc.account_id,
                                        target_chat_id=notification_target,
                                        audit_event_id=audit_event_id,
                                    )
                            except Exception as e:
                                logger.error(f"Error auto-reactivating adset {a_id}: {e}")
                                stats["errors"].append(f"Auto-reactivate error {a_id}: {e}")
                                self._finish_execution(
                                    execution_state,
                                    status="ERROR",
                                    now=now,
                                    details={"error": str(e)},
                                )
                                await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type="AUTO_REACTIVATE",
                                    status="ERROR",
                                    evaluation=eval_res,
                                    message=str(e),
                                    before_state={"status": adset.get("status", "UNKNOWN")},
                                    after_state={"status": adset.get("status", "UNKNOWN")},
                                    duration_ms=(time.perf_counter() - action_started) * 1000,
                                )

                        # УВЕЛИЧЕНИЕ БЮДЖЕТА
                        elif eval_res.action == RuleAction.INCREASE_BUDGET:
                            action_started = time.perf_counter()
                            try:
                                async with self._action_semaphore:
                                    await self.meta_client.update_adset_budget(
                                        adset_id=a_id,
                                        access_token=access_token,
                                        new_daily_budget_dollars=new_budget,
                                        currency=acc.currency,
                                        account_id=acc.account_id,
                                    )
                                stats["budgets_changed"] += 1
                                self._finish_execution(execution_state, status="SUCCESS", now=now)
                                audit_event_id = await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type="INCREASE_BUDGET",
                                    status="SUCCESS",
                                    evaluation=eval_res,
                                    before_state={"daily_budget": current_budget},
                                    after_state={"daily_budget": new_budget},
                                    duration_ms=(time.perf_counter() - action_started) * 1000,
                                )
                                if should_notify_tg and self.telegram_notifier:
                                    await self.telegram_notifier(
                                        event_type="INCREASE_BUDGET",
                                        eval_result=eval_res,
                                        account_name=acc.name,
                                        account_id=acc.account_id,
                                        target_chat_id=notification_target,
                                        old_budget=current_budget,
                                        new_budget=new_budget,
                                        audit_event_id=audit_event_id,
                                    )
                            except Exception as e:
                                logger.error(f"Error increasing budget for adset {a_id}: {e}")
                                stats["errors"].append(f"Budget increase error {a_id}: {e}")
                                self._finish_execution(
                                    execution_state,
                                    status="ERROR",
                                    now=now,
                                    details={"error": str(e)},
                                )
                                await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type="INCREASE_BUDGET",
                                    status="ERROR",
                                    evaluation=eval_res,
                                    message=str(e),
                                    before_state={"daily_budget": current_budget},
                                    after_state={"daily_budget": current_budget},
                                    duration_ms=(time.perf_counter() - action_started) * 1000,
                                )

                        # УМЕНЬШЕНИЕ БЮДЖЕТА
                        elif eval_res.action == RuleAction.DECREASE_BUDGET:
                            action_started = time.perf_counter()
                            try:
                                async with self._action_semaphore:
                                    await self.meta_client.update_adset_budget(
                                        adset_id=a_id,
                                        access_token=access_token,
                                        new_daily_budget_dollars=new_budget,
                                        currency=acc.currency,
                                        account_id=acc.account_id,
                                    )
                                stats["budgets_changed"] += 1
                                self._finish_execution(execution_state, status="SUCCESS", now=now)
                                audit_event_id = await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type="DECREASE_BUDGET",
                                    status="SUCCESS",
                                    evaluation=eval_res,
                                    before_state={"daily_budget": current_budget},
                                    after_state={"daily_budget": new_budget},
                                    duration_ms=(time.perf_counter() - action_started) * 1000,
                                )
                                if should_notify_tg and self.telegram_notifier:
                                    await self.telegram_notifier(
                                        event_type="DECREASE_BUDGET",
                                        eval_result=eval_res,
                                        account_name=acc.name,
                                        account_id=acc.account_id,
                                        target_chat_id=notification_target,
                                        old_budget=current_budget,
                                        new_budget=new_budget,
                                        audit_event_id=audit_event_id,
                                    )
                            except Exception as e:
                                logger.error(f"Error decreasing budget for adset {a_id}: {e}")
                                stats["errors"].append(f"Budget decrease error {a_id}: {e}")
                                self._finish_execution(
                                    execution_state,
                                    status="ERROR",
                                    now=now,
                                    details={"error": str(e)},
                                )
                                await self._persist_audit_event(
                                    session,
                                    acc,
                                    event_type="DECREASE_BUDGET",
                                    status="ERROR",
                                    evaluation=eval_res,
                                    message=str(e),
                                    before_state={"daily_budget": current_budget},
                                    after_state={"daily_budget": current_budget},
                                    duration_ms=(time.perf_counter() - action_started) * 1000,
                                )

                except Exception as e:
                    logger.error(f"Error processing account {account_ref}: {e}")
                    stats["errors"].append(f"Account {account_ref}: {e}")
                    await self._set_account_health(
                        session,
                        acc,
                        success=False,
                        notification_target=notification_target,
                        error=e,
                    )

                # Межаккаунтный случайный джиттер (0.5–1.5с) для сглаживания нагрузки на Meta API
                jitter = random.uniform(0.5, 1.5)
                await asyncio.sleep(jitter)

            await session.commit()

        await self._persist_runtime_state(
            stats=stats,
            started_at=started_at,
            duration_ms=(time.perf_counter() - cycle_started) * 1000,
        )
        return stats
