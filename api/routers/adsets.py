import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from api.auth import get_current_user
from api.deps import get_user_accounts
from core.audit import build_audit_event
from core.currency import UNKNOWN_CURRENCY, normalize_currency
from core.meta_tokens import resolve_account_access_token
from core.ownership import entity_is_owned_by
from database.db import async_session_maker
from database.models import Account, StoppedAdSet, User
from meta_api.client import MetaClient

logger = logging.getLogger(__name__)
router = APIRouter(tags=["AdSets"])
meta_client = MetaClient()


@router.get("/adsets/stopped")
async def list_stopped_adsets(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        user_accounts = await get_user_accounts(session, user)
        acc_ids = [a.account_id for a in user_accounts]
        if not acc_ids:
            return []

        stmt = (
            select(StoppedAdSet)
            .where(
                StoppedAdSet.account_id.in_(acc_ids),
                StoppedAdSet.is_resolved == False,
            )
            .order_by(StoppedAdSet.stopped_at.desc())
        )
        res = await session.execute(stmt)
        records = res.scalars().all()
        currencies = {
            account.account_id: normalize_currency(account.currency)
            for account in user_accounts
        }

        return [
            {
                "id": r.id,
                "account_id": r.account_id,
                "adset_id": r.adset_id,
                "adset_name": r.adset_name,
                "stop_spend": r.stop_spend,
                "currency": currencies.get(r.account_id, UNKNOWN_CURRENCY),
                "stop_leads": r.stop_leads,
                "stop_registrations": r.stop_registrations,
                "stopped_at": r.stopped_at.strftime("%Y-%m-%d %H:%M") if r.stopped_at else "",
            }
            for r in records
        ]


@router.post("/adsets/{adset_id}/reactivate")
async def reactivate_adset(adset_id: str, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        stopped_res = await session.execute(select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id))
        stopped_entry = stopped_res.scalar_one_or_none()
        if not stopped_entry:
            raise HTTPException(status_code=404, detail="Запись об остановленном адсете не найдена.")

        acc_res = await session.execute(select(Account).where(Account.account_id == stopped_entry.account_id))
        account = acc_res.scalar_one_or_none()
        if not account or (user.role != "admin" and not entity_is_owned_by(account, user)):
            raise HTTPException(status_code=403, detail="Доступ запрещен.")

        action_started = time.perf_counter()
        try:
            access_token = await resolve_account_access_token(session, account)
            await meta_client.set_adset_status(adset_id=adset_id, access_token=access_token, status="ACTIVE")
        except Exception as e:
            logger.error("Error reactivating adset %s; details stored in audit history", adset_id)
            session.add(
                build_audit_event(
                    account=account,
                    event_type="MANUAL_REACTIVATE",
                    status="ERROR",
                    correlation_id=uuid.uuid4().hex,
                    category="MANUAL_ACTION",
                    action="REACTIVATE_ADSET",
                    message=str(e),
                    before_state={"status": "PAUSED", "is_resolved": False},
                    after_state={"status": "PAUSED", "is_resolved": False},
                    duration_ms=(time.perf_counter() - action_started) * 1000,
                    actor_type="user",
                    actor_id=user.telegram_id,
                    adset_id=adset_id,
                    adset_name=stopped_entry.adset_name,
                )
            )
            try:
                await session.commit()
            except Exception as audit_error:
                await session.rollback()
                logger.error("Failed to persist manual reactivation error: %s", audit_error)
            raise HTTPException(
                status_code=500,
                detail="Meta не смогла включить ad set. Подробности сохранены в логах.",
            )

        stopped_entry.is_resolved = True
        session.add(
            build_audit_event(
                account=account,
                event_type="MANUAL_REACTIVATE",
                status="SUCCESS",
                correlation_id=uuid.uuid4().hex,
                category="MANUAL_ACTION",
                action="REACTIVATE_ADSET",
                message="Ad set вручную включён пользователем.",
                before_state={"status": "PAUSED", "is_resolved": False},
                after_state={"status": "ACTIVE", "is_resolved": True},
                duration_ms=(time.perf_counter() - action_started) * 1000,
                actor_type="user",
                actor_id=user.telegram_id,
                adset_id=adset_id,
                adset_name=stopped_entry.adset_name,
            )
        )
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("Meta activated adset %s but local state commit failed: %s", adset_id, e)
            raise HTTPException(
                status_code=500,
                detail="Meta включила ad set, но Buyerly не смог сохранить локальный статус. Обновите страницу.",
            )
        return {"success": True, "message": f"Адсет {adset_id} успешно включен!"}


@router.post("/adsets/{adset_id}/dismiss")
async def dismiss_adset(adset_id: str, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        stopped_res = await session.execute(select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id))
        stopped_entry = stopped_res.scalar_one_or_none()
        if not stopped_entry:
            raise HTTPException(status_code=404, detail="Запись не найдена.")

        account_res = await session.execute(
            select(Account).where(Account.account_id == stopped_entry.account_id)
        )
        account = account_res.scalar_one_or_none()
        if not account or (user.role != "admin" and not entity_is_owned_by(account, user)):
            raise HTTPException(status_code=403, detail="Доступ запрещен.")

        stopped_entry.is_resolved = True
        session.add(
            build_audit_event(
                account=account,
                event_type="HIDE_STOPPED_NOTIFICATION",
                status="SUCCESS",
                correlation_id=uuid.uuid4().hex,
                category="MANUAL_ACTION",
                action="HIDE_NOTIFICATION",
                message="Карточка выполненной остановки скрыта пользователем. Ad set остался выключенным.",
                before_state={"status": "PAUSED", "is_resolved": False},
                after_state={"status": "PAUSED", "is_resolved": True},
                actor_type="user",
                actor_id=user.telegram_id,
                adset_id=adset_id,
                adset_name=stopped_entry.adset_name,
            )
        )
        await session.commit()
        return {"success": True, "message": "Карточка скрыта. Ad set остался выключенным."}
