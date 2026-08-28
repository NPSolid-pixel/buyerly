"""Analytics Fact Store API endpoints.

Provides high-performance, workspace-isolated hierarchical drill-down
queries (Account -> Campaign -> AdSet -> Ad) directly from the Analytics Fact Store.
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import get_current_user
from api.deps import get_user_accounts, get_user_workspace
from database.db import async_session_maker
from database.models import User
from services.analytics_store import AnalyticsFactService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["Analytics Fact Store"])


@router.get("/hierarchy")
async def get_analytics_hierarchy(
    parent_id: str = Query(..., description="Meta ID родительской сущности (account_id, campaign_id, adset_id)"),
    level: str = Query("campaign", pattern="^(campaign|adset|ad)$", description="Уровень детализации"),
    period: str = Query("today", pattern="^(today|yesterday|last_3d|last_7d)$", description="Отчетный период"),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve normalized metrics breakdown for child entities under a parent hierarchy node.

    Strict multi-tenant security guarantees that data is only visible to authorized members
    of the owning workspace.
    """
    async with async_session_maker() as session:
        ws = await get_user_workspace(session, user)
        ws_id = ws.id if ws else getattr(user, "active_workspace_id", None)
        if not ws_id:
            return {
                "parent_id": parent_id,
                "level": level,
                "period": period,
                "total": 0,
                "items": [],
            }

        accounts = await get_user_accounts(session, user, workspace_id=ws_id)
        if not accounts:
            return {
                "parent_id": parent_id,
                "level": level,
                "period": period,
                "total": 0,
                "items": [],
            }

        # If requesting campaigns for an account, verify that account belongs to this workspace
        if level == "campaign":
            acc_id = parent_id if parent_id.startswith("act_") else f"act_{parent_id}"
            user_acc_ids = {a.account_id for a in accounts}
            if acc_id not in user_acc_ids and parent_id not in user_acc_ids:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Рекламный кабинет не найден или недоступен в текущем воркспейсе",
                )

        items = await AnalyticsFactService.get_hierarchy_breakdown(
            session=session,
            workspace_id=ws_id,
            parent_entity_id=parent_id,
            entity_level=level,
            period=period,
            user_accounts=accounts,
        )

        return {
            "parent_id": parent_id,
            "level": level,
            "period": period,
            "total": len(items),
            "items": items,
        }
