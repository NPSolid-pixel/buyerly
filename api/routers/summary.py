import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from api.auth import get_current_user
from api.deps import (
    SUMMARY_CACHE_TTL,
    SUMMARY_VIEW_SCOPE,
    _account_group_ids_by_account,
    _analytics_view_response,
    _cost_or_none,
    _currency_total_payload,
    _enrich_summary_account_metadata,
    _load_persisted_summary,
    _normalize_summary_view_config,
    _persist_summary,
    _summary_cache,
    _summary_owner_key,
    _summary_with_cache_metadata,
    _utc_iso,
    get_user_accounts,
    get_user_workspace,
)
from api.schemas import AnalyticsViewPreferenceRequest
from bot.handlers import get_short_account_label
from core.currency import UNKNOWN_CURRENCY, normalize_currency
from core.metrics import SUMMARY_METRIC_DEFINITIONS
from core.meta_tokens import resolve_account_access_token
from core.ownership import owned_by
from database.db import async_session_maker
from database.models import AnalyticsViewPreference, User
from meta_api.client import MetaClient
from services.inventory_cache import PostgreSQLInventoryCache
from services.analytics_store import AnalyticsFactService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Analytics & Summary"])
meta_client = MetaClient(cache_provider=PostgreSQLInventoryCache())


@router.get("/analytics-view")
async def get_analytics_view(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(AnalyticsViewPreference).where(
                    owned_by(AnalyticsViewPreference, user),
                    AnalyticsViewPreference.scope == SUMMARY_VIEW_SCOPE,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            config = _normalize_summary_view_config({})
        else:
            if isinstance(row.config, dict):
                stored_config = dict(row.config)
            elif isinstance(row.config, str):
                try:
                    stored_config = json.loads(row.config or "{}")
                except (TypeError, json.JSONDecodeError):
                    stored_config = {}
            else:
                stored_config = {}
            stored_order = stored_config.get("column_order")
            if (
                isinstance(stored_order, list)
                and "custom_name" not in stored_order
                and "note" not in stored_order
            ):
                insert_at = stored_order.index("account") + 1 if "account" in stored_order else 0
                stored_order[insert_at:insert_at] = ["custom_name", "note"]
                stored_visible = stored_config.get("visible_columns")
                if isinstance(stored_visible, list):
                    visible_at = stored_visible.index("account") + 1 if "account" in stored_visible else 0
                    stored_visible[visible_at:visible_at] = ["custom_name", "note"]
            config = _normalize_summary_view_config(stored_config, strict=False)
        return _analytics_view_response(row, config)


@router.put("/analytics-view")
async def save_analytics_view(
    payload: AnalyticsViewPreferenceRequest,
    user: User = Depends(get_current_user),
):
    config = _normalize_summary_view_config(payload.model_dump())
    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(AnalyticsViewPreference).where(
                    owned_by(AnalyticsViewPreference, user),
                    AnalyticsViewPreference.scope == SUMMARY_VIEW_SCOPE,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = AnalyticsViewPreference(
                owner_user_id=user.id,
                scope=SUMMARY_VIEW_SCOPE,
                config=config,
            )
            session.add(row)
        else:
            row.config = config
            row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
        return _analytics_view_response(row, config)


@router.get("/summary")
async def get_summary_report(
    period: str = Query("today", pattern="^(today|yesterday|last_3d|last_7d)$"),
    force: bool = Query(False),
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws = await get_user_workspace(session, user)
        ws_id = ws.id if ws else getattr(user, "active_workspace_id", None)
        accounts = await get_user_accounts(session, user, workspace_id=ws_id)
        current_account_ids = {a.account_id for a in accounts}

        owner_key = _summary_owner_key(user, workspace_id=ws_id)
        cache_key = f"{owner_key}:{period}"
        now_ts = time.time()

        # Return cached data if valid and force is False
        if not force and cache_key in _summary_cache:
            cached_ts, cached_data = _summary_cache[cache_key]
            if now_ts - cached_ts < SUMMARY_CACHE_TTL:
                cached_accounts = cached_data.get("accounts", [])
                cached_acc_ids = {
                    str(a.get("account_id") or "")
                    for a in cached_accounts
                    if isinstance(a, dict) and str(a.get("account_id") or "")
                }
                if cached_acc_ids == current_account_ids:
                    enriched_cached_data = await _enrich_summary_account_metadata(
                        session,
                        cached_data,
                        user,
                        workspace_id=ws_id,
                    )
                    return _summary_with_cache_metadata(
                        enriched_cached_data,
                        is_cached=True,
                        age_seconds=now_ts - cached_ts,
                        origin="memory",
                        persisted_at=(cached_data.get("snapshot") or {}).get("saved_at", ""),
                        workspace_id=ws_id,
                    )

        if not force:
            persisted = await _load_persisted_summary(
                session,
                workspace_id=ws_id,
                owner_user_id=user.id,
                period=period,
                current_account_ids=current_account_ids,
            )
            if persisted:
                persisted = await _enrich_summary_account_metadata(
                    session, persisted, user, workspace_id=ws_id
                )
                cached_payload = {
                    key: value
                    for key, value in persisted.items()
                    if key != "cache"
                }
                _summary_cache[cache_key] = (now_ts, cached_payload)
                return persisted

            # Check Analytics Fact Store before live polling
            if ws_id and accounts:
                group_ids_by_account = await _account_group_ids_by_account(session, user, workspace_id=ws_id)
                fact_summary = await AnalyticsFactService.get_workspace_summary_report(
                    session,
                    workspace_id=ws_id,
                    period=period,
                    user_accounts=accounts,
                    group_ids_by_account=group_ids_by_account,
                )
                if fact_summary.get("data_quality", {}).get("accounts_synced", 0) > 0:
                    fact_summary = await _enrich_summary_account_metadata(
                        session, fact_summary, user, workspace_id=ws_id
                    )
                    cached_payload = {
                        key: value
                        for key, value in fact_summary.items()
                        if key != "cache"
                    }
                    _summary_cache[cache_key] = (now_ts, cached_payload)
                    return _summary_with_cache_metadata(
                        fact_summary,
                        is_cached=True,
                        origin="fact_store",
                        persisted_at=fact_summary.get("generated_at", ""),
                        workspace_id=ws_id,
                    )

        group_ids_by_account = await _account_group_ids_by_account(session, user, workspace_id=ws_id)
        if not accounts:
            empty_res = {
                "period": period,
                "generated_at": _utc_iso(datetime.now(timezone.utc)),
                "source": "Meta Marketing API",
                "total_spend": 0.0,
                "display_currency": "",
                "mixed_currencies": False,
                "currency_totals": [],
                "total_clicks": 0,
                "total_impressions": 0,
                "total_reach": 0,
                "total_unique_clicks": 0,
                "total_link_clicks": 0,
                "total_outbound_clicks": 0,
                "total_landing_page_views": 0,
                "avg_frequency": None,
                "avg_cpm": None,
                "total_leads": 0,
                "total_regs": 0,
                "total_purchases": 0,
                "avg_cpc": 0.0,
                "avg_ctr": 0.0,
                "avg_cpc_link": None,
                "avg_ctr_link": None,
                "avg_ctr_outbound": None,
                "cost_per_landing_page_view": None,
                "cost_per_lead": None,
                "cost_per_registration": None,
                "cost_per_purchase": None,
                "accounts_count": 0,
                "accounts": [],
                "data_quality": {
                    "status": "unavailable",
                    "accounts_total": 0,
                    "accounts_synced": 0,
                    "accounts_failed": 0,
                    "accounts_blocked": 0,
                    "metrics_coverage_percent": 0.0,
                },
                "metric_definitions": SUMMARY_METRIC_DEFINITIONS,
            }
            empty_res["snapshot"] = await _persist_summary(
                session,
                workspace_id=ws_id,
                owner_user_id=user.id,
                period=period,
                payload=empty_res,
            )
            _summary_cache[cache_key] = (now_ts, empty_res)
            return _summary_with_cache_metadata(
                empty_res,
                is_cached=False,
                origin="live",
                persisted_at=empty_res["snapshot"]["saved_at"],
                workspace_id=ws_id,
            )

        total_spend = 0.0
        total_clicks = 0
        total_impressions = 0
        total_reach = 0
        total_unique_clicks = 0
        total_link_clicks = 0
        total_outbound_clicks = 0
        total_landing_page_views = 0
        total_leads = 0
        total_regs = 0
        total_purchases = 0
        accounts_synced = 0
        accounts_failed = 0
        accounts_blocked = 0
        currency_buckets: Dict[str, Dict[str, Any]] = {}

        account_results = []

        for acc in accounts:
            short_name = get_short_account_label(acc.name, acc.account_id)
            account_currency = normalize_currency(acc.currency)
            try:
                access_token = await resolve_account_access_token(session, acc)
                if account_currency == UNKNOWN_CURRENCY:
                    account_info = await meta_client.get_account_info(
                        acc.account_id,
                        access_token,
                    )
                    account_currency = normalize_currency(account_info.get("currency"))
                    if account_currency == UNKNOWN_CURRENCY:
                        raise RuntimeError("Meta не вернула валюту рекламного кабинета")
                    acc.currency = account_currency
                    await session.commit()
                account_insights = await meta_client.get_account_insights_summary(
                    account_id=acc.account_id,
                    access_token=access_token,
                    date_preset=period,
                )
                acc_spend = account_insights.get("spend", 0.0)
                acc_clicks = account_insights.get("clicks", 0)
                acc_impressions = account_insights.get("impressions", 0)
                acc_reach = account_insights.get("reach", 0)
                acc_unique_clicks = account_insights.get("unique_clicks", 0)
                acc_link_clicks = account_insights.get("link_clicks", 0)
                acc_outbound_clicks = account_insights.get("outbound_clicks", 0)
                acc_landing_page_views = account_insights.get("landing_page_views", 0)
                acc_leads = account_insights.get("leads", 0)
                acc_regs = account_insights.get("registrations", 0)
                acc_purchases = account_insights.get("purchases", 0)
                acc_cpc = (acc_spend / acc_clicks) if acc_clicks > 0 else 0.0
                acc_ctr = ((acc_clicks / acc_impressions) * 100) if acc_impressions > 0 else 0.0
                acc_frequency = (
                    account_insights.get("frequency")
                    or ((acc_impressions / acc_reach) if acc_reach > 0 else 0.0)
                )
                acc_cpm = (
                    account_insights.get("cpm")
                    or ((acc_spend / acc_impressions) * 1000 if acc_impressions > 0 else 0.0)
                )
                acc_ctr_link = (
                    (acc_link_clicks / acc_impressions) * 100
                    if acc_impressions > 0 else 0.0
                )
                acc_ctr_outbound = (
                    (acc_outbound_clicks / acc_impressions) * 100
                    if acc_impressions > 0 else 0.0
                )

                total_spend += acc_spend
                total_clicks += acc_clicks
                total_impressions += acc_impressions
                total_reach += acc_reach
                total_unique_clicks += acc_unique_clicks
                total_link_clicks += acc_link_clicks
                total_outbound_clicks += acc_outbound_clicks
                total_landing_page_views += acc_landing_page_views
                total_leads += acc_leads
                total_regs += acc_regs
                total_purchases += acc_purchases
                accounts_synced += 1

                bucket = currency_buckets.setdefault(
                    account_currency,
                    {
                        "accounts_count": 0,
                        "spend": 0.0,
                        "impressions": 0,
                        "clicks": 0,
                        "link_clicks": 0,
                        "landing_page_views": 0,
                        "leads": 0,
                        "registrations": 0,
                        "purchases": 0,
                    },
                )
                bucket["accounts_count"] += 1
                bucket["spend"] += acc_spend
                bucket["impressions"] += acc_impressions
                bucket["clicks"] += acc_clicks
                bucket["link_clicks"] += acc_link_clicks
                bucket["landing_page_views"] += acc_landing_page_views
                bucket["leads"] += acc_leads
                bucket["registrations"] += acc_regs
                bucket["purchases"] += acc_purchases

                if ws_id:
                    try:
                        hierarchical_facts = await meta_client.get_hierarchical_insights(
                            account_id=acc.account_id,
                            access_token=access_token,
                            date_preset=period,
                            currency=account_currency,
                            account_name=acc.name,
                        )
                        if hierarchical_facts:
                            await AnalyticsFactService.upsert_entity_facts(
                                session,
                                workspace_id=ws_id,
                                account_id=acc.account_id,
                                facts=hierarchical_facts,
                            )
                    except Exception as fact_err:
                        logger.warning(
                            "Failed to upsert hierarchical facts for %s: %s",
                            acc.account_id,
                            fact_err,
                        )

                account_results.append({
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "short_name": short_name,
                    "custom_name": acc.custom_name or "",
                    "note": acc.note or "",
                    "group_ids": group_ids_by_account.get(acc.account_id, []),
                    "timezone_name": acc.timezone_name,
                    "currency": account_currency,
                    "account_status": acc.account_status,
                    "status_label": acc.status_label,
                    "rules_enabled": acc.rules_enabled,
                    "spend": round(acc_spend, 2),
                    "clicks": acc_clicks,
                    "impressions": acc_impressions,
                    "reach": acc_reach,
                    "frequency": round(acc_frequency, 2),
                    "cpm": round(acc_cpm, 2),
                    "unique_clicks": acc_unique_clicks,
                    "link_clicks": acc_link_clicks,
                    "outbound_clicks": acc_outbound_clicks,
                    "landing_page_views": acc_landing_page_views,
                    "leads": acc_leads,
                    "registrations": acc_regs,
                    "purchases": acc_purchases,
                    "cost_per_lead": _cost_or_none(acc_spend, acc_leads),
                    "cost_per_registration": _cost_or_none(acc_spend, acc_regs),
                    "cost_per_purchase": _cost_or_none(acc_spend, acc_purchases),
                    "cpc": round(acc_cpc, 2),
                    "ctr": round(acc_ctr, 2),
                    "cpc_link": _cost_or_none(acc_spend, acc_link_clicks),
                    "ctr_link": round(acc_ctr_link, 2),
                    "ctr_outbound": round(acc_ctr_outbound, 2),
                    "cost_per_landing_page_view": _cost_or_none(
                        acc_spend,
                        acc_landing_page_views,
                    ),
                    "adsets": [],
                    "has_error": False,
                    "is_banned": not acc.is_active or acc.account_status in [2, 101],
                    "data_status": "synced",
                    "data_status_label": "Account-level метрики получены из Meta независимо от текущего статуса",
                })
            except Exception as e:
                logger.error(f"Error fetching insights for {acc.account_id}: {e}")
                is_blocked = not acc.is_active or acc.account_status in [2, 101]
                if is_blocked:
                    accounts_blocked += 1
                else:
                    accounts_failed += 1
                account_results.append({
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "short_name": short_name,
                    "custom_name": acc.custom_name or "",
                    "note": acc.note or "",
                    "group_ids": group_ids_by_account.get(acc.account_id, []),
                    "timezone_name": acc.timezone_name,
                    "currency": account_currency,
                    "account_status": acc.account_status,
                    "status_label": "Ошибка синхронизации",
                    "rules_enabled": acc.rules_enabled,
                    "spend": 0.0,
                    "clicks": 0,
                    "impressions": 0,
                    "reach": 0,
                    "frequency": None,
                    "cpm": None,
                    "unique_clicks": 0,
                    "link_clicks": 0,
                    "outbound_clicks": 0,
                    "landing_page_views": 0,
                    "leads": 0,
                    "registrations": 0,
                    "purchases": 0,
                    "cost_per_lead": None,
                    "cost_per_registration": None,
                    "cost_per_purchase": None,
                    "cpc": 0.0,
                    "ctr": 0.0,
                    "cpc_link": None,
                    "ctr_link": None,
                    "ctr_outbound": None,
                    "cost_per_landing_page_view": None,
                    "adsets": [],
                    "has_error": not is_blocked,
                    "is_banned": is_blocked,
                    "data_status": "blocked" if is_blocked else "error",
                    "data_status_label": (
                        "Исторические метрики недоступны для текущего статуса кабинета"
                        if is_blocked
                        else "Meta не вернула метрики"
                    ),
                })

        currency_totals = [
            _currency_total_payload(currency, currency_buckets[currency])
            for currency in sorted(currency_buckets)
        ]
        mixed_currencies = len(currency_totals) > 1
        display_currency = (
            currency_totals[0]["currency"]
            if len(currency_totals) == 1
            and currency_totals[0]["currency"] != UNKNOWN_CURRENCY
            else ""
        )
        monetary_totals_available = bool(display_currency)
        avg_cpc = (
            (total_spend / total_clicks) if total_clicks > 0 else 0.0
        ) if monetary_totals_available else None
        avg_ctr = ((total_clicks / total_impressions) * 100) if total_impressions > 0 else 0.0
        avg_frequency = (total_impressions / total_reach) if total_reach > 0 else None
        avg_cpm = (
            ((total_spend / total_impressions) * 1000)
            if total_impressions > 0 else None
        ) if monetary_totals_available else None
        avg_cpc_link = (
            _cost_or_none(total_spend, total_link_clicks)
            if monetary_totals_available else None
        )
        avg_ctr_link = (
            (total_link_clicks / total_impressions) * 100
            if total_impressions > 0 else None
        )
        avg_ctr_outbound = (
            (total_outbound_clicks / total_impressions) * 100
            if total_impressions > 0 else None
        )
        metrics_coverage = round((accounts_synced / len(accounts)) * 100, 1) if accounts else 0.0
        quality_status = "complete" if accounts_synced == len(accounts) else ("partial" if accounts_synced else "unavailable")

        if accounts_synced == 0:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Meta не вернула данные ни по одному кабинету. "
                    "Последний сохранённый снимок не изменён."
                ),
            )

        res_data = {
            "period": period,
            "generated_at": _utc_iso(datetime.now(timezone.utc)),
            "source": "Meta Marketing API",
            "total_spend": round(total_spend, 2) if monetary_totals_available else None,
            "display_currency": display_currency,
            "mixed_currencies": mixed_currencies,
            "currency_totals": currency_totals,
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_reach": total_reach,
            "total_unique_clicks": total_unique_clicks,
            "total_link_clicks": total_link_clicks,
            "total_outbound_clicks": total_outbound_clicks,
            "total_landing_page_views": total_landing_page_views,
            "avg_frequency": round(avg_frequency, 2) if avg_frequency is not None else None,
            "avg_cpm": round(avg_cpm, 2) if avg_cpm is not None else None,
            "total_leads": total_leads,
            "total_regs": total_regs,
            "total_purchases": total_purchases,
            "avg_cpc": round(avg_cpc, 2) if avg_cpc is not None else None,
            "avg_ctr": round(avg_ctr, 2),
            "avg_cpc_link": avg_cpc_link,
            "avg_ctr_link": round(avg_ctr_link, 2) if avg_ctr_link is not None else None,
            "avg_ctr_outbound": round(avg_ctr_outbound, 2) if avg_ctr_outbound is not None else None,
            "cost_per_landing_page_view": (
                _cost_or_none(total_spend, total_landing_page_views)
                if monetary_totals_available else None
            ),
            "cost_per_lead": (
                _cost_or_none(total_spend, total_leads)
                if monetary_totals_available else None
            ),
            "cost_per_registration": (
                _cost_or_none(total_spend, total_regs)
                if monetary_totals_available else None
            ),
            "cost_per_purchase": (
                _cost_or_none(total_spend, total_purchases)
                if monetary_totals_available else None
            ),
            "accounts_count": len(accounts),
            "accounts": account_results,
            "data_quality": {
                "status": quality_status,
                "accounts_total": len(accounts),
                "accounts_synced": accounts_synced,
                "accounts_failed": accounts_failed,
                "accounts_blocked": accounts_blocked,
                "metrics_coverage_percent": metrics_coverage,
                "monetary_totals_available": monetary_totals_available,
                "currency_issue": (
                    "mixed"
                    if mixed_currencies
                    else "unknown"
                    if not monetary_totals_available
                    else ""
                ),
            },
            "metric_definitions": SUMMARY_METRIC_DEFINITIONS,
        }
        res_data["snapshot"] = await _persist_summary(
            session,
            workspace_id=ws_id,
            owner_user_id=user.id,
            period=period,
            payload=res_data,
        )
        _summary_cache[cache_key] = (now_ts, res_data)
        return _summary_with_cache_metadata(
            res_data,
            is_cached=False,
            origin="live",
            persisted_at=res_data["snapshot"]["saved_at"],
            workspace_id=ws_id,
        )
