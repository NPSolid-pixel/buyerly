import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

from database.db import async_session_maker
from database.models import AdsetInventoryCache

logger = logging.getLogger(__name__)


class AdsetInventoryService:
    """PostgreSQL-backed shared inventory cache management for Meta ad sets."""

    @staticmethod
    def _normalize_account_id(account_id: str) -> str:
        acc = str(account_id or "").strip()
        if not acc:
            return ""
        return acc if acc.startswith("act_") else f"act_{acc}"

    @classmethod
    async def get_cached_inventory(
        cls,
        session,
        account_id: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch cached adset rows if they exist and have not expired."""
        acc_id = cls._normalize_account_id(account_id)
        if not acc_id:
            return None

        try:
            stmt = select(AdsetInventoryCache).where(
                AdsetInventoryCache.account_id == acc_id
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None

            now = datetime.now(timezone.utc)
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at <= now:
                return None

            payload = row.adsets_payload
            if isinstance(payload, list):
                return [dict(item) for item in payload if isinstance(item, dict)]
            return None
        except Exception as error:
            logger.warning("Failed to read inventory cache from PostgreSQL for %s: %s", acc_id, error)
            return None

    @classmethod
    async def save_inventory(
        cls,
        session,
        account_id: str,
        adsets: List[Dict[str, Any]],
        ttl_seconds: int = 300,
        request_started_at: Optional[datetime] = None,
    ) -> bool:
        """Save freshly fetched inventory with Stale Overwrite protection."""
        acc_id = cls._normalize_account_id(account_id)
        if not acc_id:
            return False

        now = datetime.now(timezone.utc)
        fetch_time = request_started_at or now
        if fetch_time.tzinfo is None:
            fetch_time = fetch_time.replace(tzinfo=timezone.utc)

        expires_at = now + timedelta(seconds=max(30, int(ttl_seconds)))
        clean_rows = [dict(row) for row in adsets if isinstance(row, dict)]

        try:
            stmt = (
                select(AdsetInventoryCache)
                .where(AdsetInventoryCache.account_id == acc_id)
                .with_for_update()
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()

            if row is not None:
                row_updated_at = row.updated_at
                if row_updated_at.tzinfo is None:
                    row_updated_at = row_updated_at.replace(tzinfo=timezone.utc)

                # Stale Overwrite protection: if a mutation happened after our fetch began, don't overwrite
                if row_updated_at > fetch_time:
                    logger.info(
                        "Skipping inventory cache overwrite for %s: newer mutation recorded at %s > %s",
                        acc_id,
                        row_updated_at,
                        fetch_time,
                    )
                    return False

                row.adsets_payload = clean_rows
                row.fetched_at = fetch_time
                row.expires_at = expires_at
                row.updated_at = now
                row.version = (row.version or 0) + 1
                flag_modified(row, "adsets_payload")
            else:
                new_row = AdsetInventoryCache(
                    account_id=acc_id,
                    adsets_payload=clean_rows,
                    version=1,
                    fetched_at=fetch_time,
                    expires_at=expires_at,
                    updated_at=now,
                )
                session.add(new_row)

            await session.commit()
            return True
        except Exception as error:
            await session.rollback()
            logger.warning("Failed to save inventory cache to PostgreSQL for %s: %s", acc_id, error)
            return False

    @classmethod
    async def update_adset_status(
        cls,
        session,
        account_id: str,
        adset_id: str,
        status: str,
    ) -> bool:
        """Update an adset's status in the cached payload across all processes."""
        acc_id = cls._normalize_account_id(account_id)
        target_adset_id = str(adset_id).strip()
        if not acc_id or not target_adset_id:
            return False

        now = datetime.now(timezone.utc)
        try:
            stmt = (
                select(AdsetInventoryCache)
                .where(AdsetInventoryCache.account_id == acc_id)
                .with_for_update()
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False

            payload = list(row.adsets_payload or [])
            found = False
            for item in payload:
                if str(item.get("id") or item.get("adset_id")) == target_adset_id:
                    item["status"] = status
                    item["effective_status"] = status
                    found = True

            if found:
                row.adsets_payload = payload
                row.updated_at = now
                row.version = (row.version or 0) + 1
                flag_modified(row, "adsets_payload")
                await session.commit()
                return True
            return False
        except Exception as error:
            await session.rollback()
            logger.warning(
                "Failed to update adset %s status in inventory cache for %s: %s",
                target_adset_id,
                acc_id,
                error,
            )
            return False

    @classmethod
    async def invalidate_inventory(
        cls,
        session,
        account_id: str,
    ) -> bool:
        """Atomically invalidate the inventory cache for an account."""
        acc_id = cls._normalize_account_id(account_id)
        if not acc_id:
            return False

        try:
            stmt = delete(AdsetInventoryCache).where(
                AdsetInventoryCache.account_id == acc_id
            )
            await session.execute(stmt)
            await session.commit()
            return True
        except Exception as error:
            await session.rollback()
            logger.warning("Failed to invalidate inventory cache for %s: %s", acc_id, error)
            return False


class PostgreSQLInventoryCache:
    """Provider adapter implementing the cache interface for MetaClient."""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or async_session_maker

    async def get_inventory(self, account_id: str) -> Optional[List[Dict[str, Any]]]:
        try:
            async with self._session_factory() as session:
                return await AdsetInventoryService.get_cached_inventory(session, account_id)
        except Exception as error:
            logger.warning("PostgreSQLInventoryCache get error: %s", error)
            return None

    async def set_inventory(
        self,
        account_id: str,
        rows: List[Dict[str, Any]],
        ttl_seconds: int = 300,
        request_started_at: Optional[datetime] = None,
    ) -> None:
        try:
            async with self._session_factory() as session:
                await AdsetInventoryService.save_inventory(
                    session,
                    account_id,
                    rows,
                    ttl_seconds=ttl_seconds,
                    request_started_at=request_started_at,
                )
        except Exception as error:
            logger.warning("PostgreSQLInventoryCache set error: %s", error)

    async def update_status(
        self,
        account_id: str,
        adset_id: str,
        status: str,
    ) -> None:
        try:
            async with self._session_factory() as session:
                updated = await AdsetInventoryService.update_adset_status(
                    session, account_id, adset_id, status
                )
                if not updated:
                    await AdsetInventoryService.invalidate_inventory(session, account_id)
        except Exception as error:
            logger.warning("PostgreSQLInventoryCache update_status error: %s", error)

    async def invalidate(self, account_id: str) -> None:
        try:
            async with self._session_factory() as session:
                await AdsetInventoryService.invalidate_inventory(session, account_id)
        except Exception as error:
            logger.warning("PostgreSQLInventoryCache invalidate error: %s", error)
