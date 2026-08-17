import logging
import random
import asyncio
import json
import hashlib
import time
import uuid
from datetime import datetime, timezone
import zoneinfo
from typing import Optional, Callable, Awaitable, Any
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.db import async_session_maker
from database.models import (
    Account,
    AppSettings,
    AutomationScheduleState,
    RuleExecutionState,
    StoppedAdSet,
)
from core.audit import build_audit_event
from meta_api.client import MetaClient
from rules.engine import RuleEngine, RuleAction, RuleEvaluationResult

logger = logging.getLogger(__name__)

PENDING_RECONCILIATION_SECONDS = 15 * 60

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
        self.meta_client = meta_client or MetaClient()
        self.telegram_notifier = telegram_notifier
        # A wall clock is intentionally used: persisted timestamps must remain
        # meaningful after a process restart, unlike time.monotonic().
        self._clock = clock or time.time
        self._current_cycle_id = ""

    @staticmethod
    def _load_rules(raw_rules: Any) -> list[dict[str, Any]]:
        try:
            rules = json.loads(raw_rules) if isinstance(raw_rules, str) else raw_rules
        except (TypeError, ValueError):
            return []
        if not isinstance(rules, list):
            return []
        return [rule for rule in rules if isinstance(rule, dict)]

    @staticmethod
    def _interval_minutes(value: Any, fallback: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return max(1, fallback)

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
    ) -> AutomationScheduleState:
        state = (
            await session.execute(
                select(AutomationScheduleState).where(
                    AutomationScheduleState.state_key == state_key
                )
            )
        ).scalar_one_or_none()
        if state is not None:
            return state
        state = AutomationScheduleState(
            state_key=state_key,
            owner_id=str(account.owner_id or ""),
            account_id=str(account.account_id),
            rule_key=rule_key,
            last_checked_at=0.0,
        )
        session.add(state)
        try:
            await session.flush()
            return state
        except IntegrityError:
            await session.rollback()
            return (
                await session.execute(
                    select(AutomationScheduleState).where(
                        AutomationScheduleState.state_key == state_key
                    )
                )
            ).scalar_one()

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
                owner_id=str(account.owner_id or ""),
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
                state.details = json.dumps(
                    {"reconciled_after_restart": True},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                await session.commit()
                return False, "reconciled", state
            if now - float(state.last_attempt_at or 0.0) < PENDING_RECONCILIATION_SECONDS:
                await session.commit()
                return False, "pending", state
            state.status = "ERROR"
            state.details = json.dumps(
                {"reason": "stale_pending_not_confirmed"},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await session.commit()
            state = (await session.execute(query.with_for_update())).scalar_one()

        cooldown_seconds = max(0, int(evaluation.cooldown_minutes or 0)) * 60
        if (
            cooldown_seconds > 0
            and state.last_success_at is not None
            and now - float(state.last_success_at) < cooldown_seconds
        ):
            await session.commit()
            return False, "cooldown", state

        state.owner_id = str(account.owner_id or "")
        state.status = "PENDING"
        state.correlation_id = self._current_cycle_id
        state.last_attempt_at = now
        state.before_state = json.dumps(observed_state, ensure_ascii=False, separators=(",", ":"))
        state.after_state = json.dumps(desired_state, ensure_ascii=False, separators=(",", ":"))
        state.details = "{}"
        await session.commit()
        return True, "claimed", state

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
        state.details = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))

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

    async def run_cycle(self) -> dict:
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
            "proposals_sent": 0,
            "starts_notified": 0,
            "errors": []
        }

        async with async_session_maker() as session:
            # 1. Загружаем все активные аккаунты
            stmt = select(Account).where(Account.is_active == True)
            result = await session.execute(stmt)
            accounts = result.scalars().all()

            settings_result = await session.execute(select(AppSettings).limit(1))
            app_settings = settings_result.scalar_one_or_none()
            default_interval = self._interval_minutes(
                app_settings.poll_interval_minutes if app_settings else 10,
                10,
            )

            for acc in accounts:
                now = self._clock()
                active_rules = self._load_rules(acc.active_rules)
                due_rule_entries = []
                if acc.rules_enabled:
                    for index, rule in enumerate(active_rules):
                        rule_key = self._rule_key(acc.account_id, index, rule)
                        interval = self._interval_minutes(
                            rule.get("check_interval"),
                            default_interval,
                        )
                        state_key = self._schedule_key("rule", acc.account_id, rule_key)
                        schedule_state = await self._get_or_create_schedule_state(
                            session,
                            state_key=state_key,
                            account=acc,
                            rule_key=rule_key,
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
                )
                account_monitor_due = (
                    account_state.last_checked_at <= 0
                    or now - account_state.last_checked_at >= default_interval * 60
                )
                if not account_monitor_due and not due_rule_entries:
                    stats["accounts_skipped"] += 1
                    await session.commit()
                    continue

                if account_monitor_due:
                    account_state.last_checked_at = now
                for _, _, schedule_state in due_rule_entries:
                    schedule_state.last_checked_at = now
                await session.commit()

                due_rules = [rule for _, rule, _ in due_rule_entries]
                stats["accounts_checked"] += 1
                stats["rules_checked"] += len(due_rules)

                try:
                    # 2. Проверяем здоровье кабинета и статус в Meta
                    try:
                        acc_info = await self.meta_client.get_account_info(acc.account_id, acc.access_token)
                        status_code = acc_info.get("account_status", 1)
                        if status_code != 1:
                            status_label = acc_info.get("status_label", f"Статус #{status_code}")
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
                                    target_chat_id=acc.owner_id,
                                    local_time=status_label
                                )
                            continue
                    except PermissionError as pe:
                        logger.error(f"Token expired for account {acc.account_id}: {pe}")
                        acc.is_active = False
                        await self._persist_audit_event(
                            session,
                            acc,
                            event_type="TOKEN_EXPIRED",
                            status="ERROR",
                            category="ACCOUNT_HEALTH",
                            action="DISABLE_MONITORING",
                            message=str(pe),
                            before_state={"is_active": True},
                            after_state={"is_active": False},
                        )
                        if self.telegram_notifier:
                            await self.telegram_notifier(
                                event_type="TOKEN_EXPIRED",
                                account_name=acc.name,
                                account_id=acc.account_id,
                                target_chat_id=acc.owner_id
                            )
                        continue

                    # 3. Определяем текущее локальное время и дату в часовом поясе кабинета
                    try:
                        tz = zoneinfo.ZoneInfo(acc.timezone_name)
                    except Exception:
                        tz = timezone.utc
                    
                    now_in_tz = datetime.now(tz)
                    today_str = now_in_tz.strftime("%Y-%m-%d")
                    time_str = now_in_tz.strftime("%H:%M")

                    # 4. Собираем данные за нужные time_windows, кроме 'today'
                    windows = set()
                    for rule in due_rules:
                        conds = rule.get("conditions", [])
                        for cond in conds:
                            if isinstance(cond, dict):
                                window = cond.get("time_window", "today")
                                if window != "today":
                                    windows.add(window)
                    
                    insights_by_window = {}
                    for w in windows:
                        try:
                            w_insights = await self.meta_client.get_adsets_insights(
                                account_id=acc.account_id,
                                access_token=acc.access_token,
                                date_preset=w
                            )
                            insights_by_window[w] = {str(a["adset_id"]): a for a in w_insights}
                        except Exception as e:
                            logger.error(f"Error fetching insights for window {w} (account {acc.account_id}): {e}")

                    # 5. Запрашиваем актуальные данные из Meta API за сегодня
                    adsets = await self.meta_client.get_adsets_insights(
                        account_id=acc.account_id,
                        access_token=acc.access_token,
                        date_preset="today"
                    )

                    stats["adsets_checked"] += len(adsets)

                    # 6. Проверяем старт открута рекламы в новые сутки (00:00)
                    total_spend = sum(a["spend"] for a in adsets)
                    active_adsets = [a for a in adsets if a["status"] == "ACTIVE"]

                    if total_spend > 0 and acc.last_started_date != today_str:
                        previous_started_date = acc.last_started_date
                        acc.last_started_date = today_str
                        stats["starts_notified"] += 1
                        logger.info(f"Account {acc.name} started spending today ({today_str} {time_str} {acc.timezone_name})")
                        await self._persist_audit_event(
                            session,
                            acc,
                            event_type="DAY_START",
                            status="SUCCESS",
                            category="MONITORING",
                            action="DETECT_SPEND_START",
                            message=f"Кабинет начал открутку {today_str} в {time_str}",
                            before_state={"last_started_date": previous_started_date},
                            after_state={"last_started_date": today_str},
                            details={
                                "timezone_name": acc.timezone_name,
                                "active_adsets": len(active_adsets),
                                "start_spend": total_spend,
                            },
                        )
                        
                        if self.telegram_notifier:
                            await self.telegram_notifier(
                                event_type="DAY_START",
                                account_name=acc.name,
                                account_id=acc.account_id,
                                target_chat_id=acc.owner_id,
                                timezone_name=acc.timezone_name,
                                local_time=f"{time_str} ({acc.timezone_name})",
                                active_count=len(active_adsets),
                                start_spend=total_spend
                            )

                    # 7. Оцениваем каждый адсет через RuleEngine (только если авто-правила включены!)
                    if not acc.rules_enabled or not due_rules:
                        # Авто-правила выключены: кабинет только собирает статистику
                        continue

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
                            continue

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
                                await self.meta_client.set_adset_status(
                                    adset_id=a_id,
                                    access_token=acc.access_token,
                                    status="PAUSED"
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
                                        target_chat_id=acc.owner_id,
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
                                    target_chat_id=acc.owner_id
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
                                    target_chat_id=acc.owner_id
                                )

                        # АВТО-ВКЛЮЧЕНИЕ
                        elif eval_res.action == RuleAction.AUTO_REACTIVATE:
                            action_started = time.perf_counter()
                            try:
                                await self.meta_client.set_adset_status(
                                    adset_id=a_id,
                                    access_token=acc.access_token,
                                    status="ACTIVE"
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
                                        target_chat_id=acc.owner_id,
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
                                await self.meta_client.update_adset_budget(
                                    adset_id=a_id,
                                    access_token=acc.access_token,
                                    new_daily_budget_dollars=new_budget
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
                                        target_chat_id=acc.owner_id,
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
                                await self.meta_client.update_adset_budget(
                                    adset_id=a_id,
                                    access_token=acc.access_token,
                                    new_daily_budget_dollars=new_budget
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
                                        target_chat_id=acc.owner_id,
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
                    logger.error(f"Error processing account {acc.account_id}: {e}")
                    stats["errors"].append(f"Account {acc.account_id}: {e}")

                # Межаккаунтный случайный джиттер (0.5–1.5с) для сглаживания нагрузки на Meta API
                jitter = random.uniform(0.5, 1.5)
                await asyncio.sleep(jitter)

            await session.commit()

        return stats
