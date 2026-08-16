import logging
import random
import asyncio
import json
import time
from datetime import datetime, timezone
import zoneinfo
from typing import Optional, Callable, Awaitable, Any
from sqlalchemy import select

from database.db import async_session_maker
from database.models import Account, AppSettings
from meta_api.client import MetaClient
from rules.engine import RuleEngine, RuleAction, RuleEvaluationResult

logger = logging.getLogger(__name__)

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
        self._clock = clock or time.monotonic
        self._adset_cooldowns: dict[str, float] = {}
        self._account_last_checked: dict[str, float] = {}
        self._rule_last_checked: dict[str, float] = {}

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

    async def run_cycle(self) -> dict:
        stats = {
            "accounts_checked": 0,
            "accounts_skipped": 0,
            "rules_checked": 0,
            "adsets_checked": 0,
            "adsets_stopped": 0,
            "budgets_changed": 0,
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
                active_rule_keys = set()
                if acc.rules_enabled:
                    for index, rule in enumerate(active_rules):
                        rule_key = self._rule_key(acc.account_id, index, rule)
                        active_rule_keys.add(rule_key)
                        interval = self._interval_minutes(
                            rule.get("check_interval"),
                            default_interval,
                        )
                        last_checked = self._rule_last_checked.get(rule_key)
                        if last_checked is None or now - last_checked >= interval * 60:
                            due_rule_entries.append((rule_key, rule))

                account_last_checked = self._account_last_checked.get(acc.account_id)
                account_monitor_due = (
                    account_last_checked is None
                    or now - account_last_checked >= default_interval * 60
                )
                if not account_monitor_due and not due_rule_entries:
                    stats["accounts_skipped"] += 1
                    continue

                self._account_last_checked[acc.account_id] = now
                for rule_key, _ in due_rule_entries:
                    self._rule_last_checked[rule_key] = now
                for stale_key in [
                    key
                    for key in self._rule_last_checked
                    if key.startswith(f"{acc.account_id}:") and key not in active_rule_keys
                ]:
                    self._rule_last_checked.pop(stale_key, None)

                due_rules = [rule for _, rule in due_rule_entries]
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
                        acc.last_started_date = today_str
                        stats["starts_notified"] += 1
                        logger.info(f"Account {acc.name} started spending today ({today_str} {time_str} {acc.timezone_name})")
                        
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
                        cooldown_mins = eval_res.cooldown_minutes

                        # Проверка паузы между срабатываниями (cooldown)
                        cooldown_key = f"{acc.account_id}_{a_id}_{eval_res.action}"
                        if cooldown_mins > 0 and eval_res.action != RuleAction.NOOP:
                            last_time = self._adset_cooldowns.get(cooldown_key)
                            if last_time and (time.time() - last_time < cooldown_mins * 60):
                                logger.debug(f"AdSet {a_id} action {eval_res.action} in cooldown ({cooldown_mins}m). Skipping.")
                                continue

                        # СТОП адсета
                        if eval_res.action == RuleAction.STOP:
                            try:
                                await self.meta_client.set_adset_status(
                                    adset_id=a_id,
                                    access_token=acc.access_token,
                                    status="PAUSED"
                                )
                                self._adset_cooldowns[cooldown_key] = time.time()
                                
                                stats["adsets_stopped"] += 1
                                logger.info(f"STOPPED AdSet: {a_id} ({eval_res.adset_name}) - {eval_res.reason}")

                                if should_notify_tg and self.telegram_notifier:
                                    await self.telegram_notifier(
                                        event_type="STOP",
                                        eval_result=eval_res,
                                        account_name=acc.name,
                                        account_id=acc.account_id,
                                        target_chat_id=acc.owner_id
                                    )
                            except Exception as e:
                                logger.error(f"Error pausing adset {a_id}: {e}")
                                stats["errors"].append(f"Pause error {a_id}: {e}")

                        # ТОЛЬКО УВЕДОМЛЕНИЕ (Send notification only)
                        elif eval_res.action == RuleAction.NOTIFY_ONLY:
                            self._adset_cooldowns[cooldown_key] = time.time()
                            logger.info(f"NOTIFY ONLY AdSet: {a_id} ({eval_res.adset_name}) - {eval_res.reason}")
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
                            self._adset_cooldowns[cooldown_key] = time.time()
                            stats["proposals_sent"] += 1
                            logger.info(f"PROPOSE REACTIVATE AdSet: {a_id} ({eval_res.adset_name}) - {eval_res.reason}")

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
                            try:
                                await self.meta_client.set_adset_status(
                                    adset_id=a_id,
                                    access_token=acc.access_token,
                                    status="ACTIVE"
                                )
                                
                                logger.info(f"AUTO REACTIVATED AdSet: {a_id} ({eval_res.adset_name})")

                                if self.telegram_notifier:
                                    await self.telegram_notifier(
                                        event_type="AUTO_REACTIVATE",
                                        eval_result=eval_res,
                                        account_name=acc.name,
                                        account_id=acc.account_id,
                                        target_chat_id=acc.owner_id
                                    )
                            except Exception as e:
                                logger.error(f"Error auto-reactivating adset {a_id}: {e}")
                                stats["errors"].append(f"Auto-reactivate error {a_id}: {e}")

                        # УВЕЛИЧЕНИЕ БЮДЖЕТА
                        elif eval_res.action == RuleAction.INCREASE_BUDGET:
                            current_budget = adset.get("daily_budget", 0.0)
                            if current_budget > 0 and eval_res.budget_change_percent > 0:
                                new_budget = current_budget * (1 + eval_res.budget_change_percent / 100.0)
                                if eval_res.budget_max_daily > 0:
                                    new_budget = min(new_budget, eval_res.budget_max_daily)
                                try:
                                    await self.meta_client.update_adset_budget(
                                        adset_id=a_id,
                                        access_token=acc.access_token,
                                        new_daily_budget_dollars=new_budget
                                    )
                                    self._adset_cooldowns[cooldown_key] = time.time()
                                    stats["budgets_changed"] += 1
                                    if should_notify_tg and self.telegram_notifier:
                                        await self.telegram_notifier(
                                            event_type="INCREASE_BUDGET",
                                            eval_result=eval_res,
                                            account_name=acc.name,
                                            account_id=acc.account_id,
                                            target_chat_id=acc.owner_id,
                                            old_budget=current_budget,
                                            new_budget=new_budget
                                        )
                                except Exception as e:
                                    logger.error(f"Error increasing budget for adset {a_id}: {e}")
                                    stats["errors"].append(f"Budget increase error {a_id}: {e}")

                        # УМЕНЬШЕНИЕ БЮДЖЕТА
                        elif eval_res.action == RuleAction.DECREASE_BUDGET:
                            current_budget = adset.get("daily_budget", 0.0)
                            if current_budget > 0 and eval_res.budget_change_percent > 0:
                                new_budget = current_budget * (1 - eval_res.budget_change_percent / 100.0)
                                new_budget = max(new_budget, 1.0)
                                try:
                                    await self.meta_client.update_adset_budget(
                                        adset_id=a_id,
                                        access_token=acc.access_token,
                                        new_daily_budget_dollars=new_budget
                                    )
                                    self._adset_cooldowns[cooldown_key] = time.time()
                                    stats["budgets_changed"] += 1
                                    if should_notify_tg and self.telegram_notifier:
                                        await self.telegram_notifier(
                                            event_type="DECREASE_BUDGET",
                                            eval_result=eval_res,
                                            account_name=acc.name,
                                            account_id=acc.account_id,
                                            target_chat_id=acc.owner_id,
                                            old_budget=current_budget,
                                            new_budget=new_budget
                                        )
                                except Exception as e:
                                    logger.error(f"Error decreasing budget for adset {a_id}: {e}")
                                    stats["errors"].append(f"Budget decrease error {a_id}: {e}")

                except Exception as e:
                    logger.error(f"Error processing account {acc.account_id}: {e}")
                    stats["errors"].append(f"Account {acc.account_id}: {e}")

                # Межаккаунтный случайный джиттер (0.5–1.5с) для сглаживания нагрузки на Meta API
                jitter = random.uniform(0.5, 1.5)
                await asyncio.sleep(jitter)

            await session.commit()

        return stats
