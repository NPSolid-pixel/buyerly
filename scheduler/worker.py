import logging
import random
import asyncio
from datetime import datetime, timezone
import zoneinfo
from typing import Optional, Callable, Awaitable
from sqlalchemy import select

from database.db import async_session_maker
from database.models import Account, StoppedAdSet
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
        telegram_notifier: Optional[Callable[..., Awaitable[None]]] = None
    ):
        self.meta_client = meta_client or MetaClient()
        self.telegram_notifier = telegram_notifier

    async def run_cycle(self) -> dict:
        stats = {
            "accounts_checked": 0,
            "adsets_checked": 0,
            "adsets_stopped": 0,
            "proposals_sent": 0,
            "starts_notified": 0,
            "errors": []
        }

        async with async_session_maker() as session:
            # 1. Загружаем все активные аккаунты
            stmt = select(Account).where(Account.is_active == True)
            result = await session.execute(stmt)
            accounts = result.scalars().all()

            stats["accounts_checked"] = len(accounts)

            for acc in accounts:
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

                    # 4. Получаем список остановленных адсетов по этому аккаунту
                    stopped_stmt = select(StoppedAdSet).where(
                        StoppedAdSet.account_id == acc.account_id,
                        StoppedAdSet.is_resolved == False
                    )
                    stopped_res = await session.execute(stopped_stmt)
                    stopped_records = {s.adset_id: s for s in stopped_res.scalars().all()}

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
                    if not acc.rules_enabled:
                        # Авто-правила выключены: кабинет только собирает статистику
                        continue

                    for adset in adsets:
                        a_id = str(adset["adset_id"])
                        is_stopped_today = a_id in stopped_records

                        eval_res = RuleEngine.evaluate(
                            adset=adset,
                            account=acc,
                            is_stopped_today=is_stopped_today
                        )

                        # СТОП адсета
                        if eval_res.action == RuleAction.STOP:
                            try:
                                await self.meta_client.set_adset_status(
                                    adset_id=a_id,
                                    access_token=acc.access_token,
                                    status="PAUSED"
                                )
                                if a_id not in stopped_records:
                                    stopped_entry = StoppedAdSet(
                                        account_id=acc.account_id,
                                        adset_id=a_id,
                                        adset_name=eval_res.adset_name,
                                        stop_spend=eval_res.spend,
                                        stop_leads=eval_res.leads,
                                        stop_registrations=eval_res.registrations,
                                        is_resolved=False
                                    )
                                    session.add(stopped_entry)
                                
                                stats["adsets_stopped"] += 1
                                logger.info(f"STOPPED AdSet: {a_id} ({eval_res.adset_name}) - {eval_res.reason}")

                                if self.telegram_notifier:
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

                        # ПРЕДЛОЖЕНИЕ ВКЛЮЧИТЬ (долет)
                        elif eval_res.action == RuleAction.PROPOSE_REACTIVATE:
                            stats["proposals_sent"] += 1
                            logger.info(f"PROPOSE REACTIVATE AdSet: {a_id} ({eval_res.adset_name}) - {eval_res.reason}")

                            if self.telegram_notifier:
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
                                if a_id in stopped_records:
                                    stopped_records[a_id].is_resolved = True
                                
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

                except Exception as e:
                    logger.error(f"Error processing account {acc.account_id}: {e}")
                    stats["errors"].append(f"Account {acc.account_id}: {e}")

                # Межаккаунтный случайный джиттер (0.5–1.5с) для сглаживания нагрузки на Meta API
                jitter = random.uniform(0.5, 1.5)
                await asyncio.sleep(jitter)

            await session.commit()

        return stats
