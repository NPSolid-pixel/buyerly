"""Workspace-isolated analytics fact store service.

Provides high-performance, deadlock-safe insertion and fast multi-tenant SQL
aggregations for Meta advertising metrics across all hierarchy levels:
Account -> Campaign -> AdSet -> Ad.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.handlers import get_short_account_label
from core.currency import UNKNOWN_CURRENCY, normalize_currency
from core.metrics import SUMMARY_METRIC_DEFINITIONS, cost_per_event
from core.timezones import canonical_timezone_name, resolve_account_clock
from database.models import Account, AnalyticsEntityFact

logger = logging.getLogger(__name__)


def resolve_account_period_dates(
    timezone_name: str,
    period: str = "today",
    now_utc: Optional[datetime] = None,
) -> List[str]:
    """Calculate the list of local YYYY-MM-DD dates for an account timezone and reporting period."""
    now = now_utc or datetime.now(timezone.utc)
    clock = resolve_account_clock(timezone_name)
    local_now = now.astimezone(clock.zone) if clock else now
    local_today = local_now.date()

    if period == "today":
        return [local_today.isoformat()]
    elif period == "yesterday":
        return [(local_today - timedelta(days=1)).isoformat()]
    elif period == "last_3d":
        return [(local_today - timedelta(days=i)).isoformat() for i in range(3)]
    elif period == "last_7d":
        return [(local_today - timedelta(days=i)).isoformat() for i in range(7)]
    else:
        return [local_today.isoformat()]


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(val)) if val is not None else default
    except (TypeError, ValueError):
        return default


class AnalyticsFactService:
    """Core domain service for the PostgreSQL-backed Analytics Fact Store."""

    @staticmethod
    async def upsert_entity_facts(
        session,
        workspace_id: int,
        account_id: str,
        facts: List[Dict[str, Any]],
    ) -> int:
        """Atomically and idempotently insert or update metric facts with deadlock protection.

        Rows are deterministically sorted by their composite unique key before insertion.
        """
        if not facts:
            return 0

        # Validate and normalize facts payload
        now_ts = datetime.now(timezone.utc)
        normalized_facts = []
        for raw in facts:
            fact = dict(raw)
            fact["workspace_id"] = int(workspace_id)
            fact["account_id"] = str(account_id)
            fact["entity_level"] = str(fact.get("entity_level") or "account")
            fact["entity_id"] = str(fact.get("entity_id") or account_id)
            fact["entity_name"] = str(fact.get("entity_name") or "")[:255]
            fact["parent_entity_id"] = str(fact.get("parent_entity_id") or "")[:64]
            fact["date"] = str(fact.get("date") or now_ts.date().isoformat())[:10]
            fact["currency"] = normalize_currency(fact.get("currency") or "UNKNOWN")
            fact["spend"] = round(_safe_float(fact.get("spend")), 2)
            fact["impressions"] = _safe_int(fact.get("impressions"))
            fact["reach"] = _safe_int(fact.get("reach"))
            fact["frequency"] = round(_safe_float(fact.get("frequency")), 2)
            fact["cpm"] = round(_safe_float(fact.get("cpm")), 2)
            fact["clicks"] = _safe_int(fact.get("clicks"))
            fact["unique_clicks"] = _safe_int(fact.get("unique_clicks"))
            fact["link_clicks"] = _safe_int(fact.get("link_clicks"))
            fact["outbound_clicks"] = _safe_int(fact.get("outbound_clicks"))
            fact["landing_page_views"] = _safe_int(fact.get("landing_page_views"))
            fact["cpc"] = round(_safe_float(fact.get("cpc")), 2)
            fact["cpc_link"] = (
                round(_safe_float(fact["cpc_link"]), 2)
                if fact.get("cpc_link") is not None
                else None
            )
            fact["ctr"] = round(_safe_float(fact.get("ctr")), 2)
            fact["ctr_link"] = (
                round(_safe_float(fact["ctr_link"]), 2)
                if fact.get("ctr_link") is not None
                else None
            )
            fact["ctr_outbound"] = (
                round(_safe_float(fact["ctr_outbound"]), 2)
                if fact.get("ctr_outbound") is not None
                else None
            )
            fact["leads"] = _safe_int(fact.get("leads"))
            fact["registrations"] = _safe_int(fact.get("registrations"))
            fact["purchases"] = _safe_int(fact.get("purchases"))
            fact["cost_per_lead"] = (
                round(_safe_float(fact["cost_per_lead"]), 2)
                if fact.get("cost_per_lead") is not None
                else None
            )
            fact["cost_per_registration"] = (
                round(_safe_float(fact["cost_per_registration"]), 2)
                if fact.get("cost_per_registration") is not None
                else None
            )
            fact["cost_per_purchase"] = (
                round(_safe_float(fact["cost_per_purchase"]), 2)
                if fact.get("cost_per_purchase") is not None
                else None
            )
            fact["cost_per_landing_page_view"] = (
                round(_safe_float(fact["cost_per_landing_page_view"]), 2)
                if fact.get("cost_per_landing_page_view") is not None
                else None
            )
            fact["raw_actions"] = fact.get("raw_actions") if isinstance(fact.get("raw_actions"), list) else []
            fact["status"] = str(fact.get("status") or "UNKNOWN")[:32]
            fact["effective_status"] = str(fact.get("effective_status") or fact["status"])[:32]
            fact["daily_budget"] = round(_safe_float(fact.get("daily_budget")), 2)
            fact["fetched_at"] = fact.get("fetched_at") or now_ts
            fact["updated_at"] = now_ts
            normalized_facts.append(fact)

        # 1. Deterministic sort to avoid deadlocks in concurrent PostgreSQL bulk UPSERTs
        normalized_facts.sort(
            key=lambda x: (
                x["workspace_id"],
                x["account_id"],
                x["entity_level"],
                x["entity_id"],
                x["date"],
            )
        )

        # 2. Execute dialect-safe UPSERT
        bind = session.bind
        dialect_name = bind.dialect.name if bind is not None else "postgresql"

        if dialect_name == "postgresql":
            stmt = pg_insert(AnalyticsEntityFact).values(normalized_facts)
            stmt = stmt.on_conflict_do_update(
                index_elements=["workspace_id", "account_id", "entity_level", "entity_id", "date"],
                set_={
                    "entity_name": stmt.excluded.entity_name,
                    "parent_entity_id": stmt.excluded.parent_entity_id,
                    "currency": stmt.excluded.currency,
                    "spend": stmt.excluded.spend,
                    "impressions": stmt.excluded.impressions,
                    "reach": stmt.excluded.reach,
                    "frequency": stmt.excluded.frequency,
                    "cpm": stmt.excluded.cpm,
                    "clicks": stmt.excluded.clicks,
                    "unique_clicks": stmt.excluded.unique_clicks,
                    "link_clicks": stmt.excluded.link_clicks,
                    "outbound_clicks": stmt.excluded.outbound_clicks,
                    "landing_page_views": stmt.excluded.landing_page_views,
                    "cpc": stmt.excluded.cpc,
                    "cpc_link": stmt.excluded.cpc_link,
                    "ctr": stmt.excluded.ctr,
                    "ctr_link": stmt.excluded.ctr_link,
                    "ctr_outbound": stmt.excluded.ctr_outbound,
                    "leads": stmt.excluded.leads,
                    "registrations": stmt.excluded.registrations,
                    "purchases": stmt.excluded.purchases,
                    "cost_per_lead": stmt.excluded.cost_per_lead,
                    "cost_per_registration": stmt.excluded.cost_per_registration,
                    "cost_per_purchase": stmt.excluded.cost_per_purchase,
                    "cost_per_landing_page_view": stmt.excluded.cost_per_landing_page_view,
                    "raw_actions": stmt.excluded.raw_actions,
                    "status": stmt.excluded.status,
                    "effective_status": stmt.excluded.effective_status,
                    "daily_budget": stmt.excluded.daily_budget,
                    "fetched_at": stmt.excluded.fetched_at,
                    "updated_at": now_ts,
                },
            )
            await session.execute(stmt)
        else:
            # Fallback for SQLite / generic dialects (used in some test environments)
            for item in normalized_facts:
                existing = (
                    await session.execute(
                        select(AnalyticsEntityFact).where(
                            AnalyticsEntityFact.workspace_id == item["workspace_id"],
                            AnalyticsEntityFact.account_id == item["account_id"],
                            AnalyticsEntityFact.entity_level == item["entity_level"],
                            AnalyticsEntityFact.entity_id == item["entity_id"],
                            AnalyticsEntityFact.date == item["date"],
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    for k, v in item.items():
                        setattr(existing, k, v)
                else:
                    session.add(AnalyticsEntityFact(**item))

        return len(normalized_facts)

    @staticmethod
    async def get_workspace_summary_report(
        session,
        workspace_id: int,
        period: str,
        user_accounts: List[Account],
        group_ids_by_account: Optional[Dict[str, List[int]]] = None,
    ) -> Dict[str, Any]:
        """Aggregate fast workspace summary directly from the Analytics Fact Store."""
        group_map = group_ids_by_account or {}
        if not user_accounts:
            return {
                "period": period,
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "source": "PostgreSQL Fact Store",
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
                    "monetary_totals_available": False,
                    "currency_issue": "",
                },
                "metric_definitions": SUMMARY_METRIC_DEFINITIONS,
            }

        # 1. Resolve date targets per account timezone
        acc_dates_map: Dict[str, List[str]] = {}
        all_query_dates: Set[str] = set()
        for acc in user_accounts:
            dates = resolve_account_period_dates(acc.timezone_name or "UTC", period)
            acc_dates_map[acc.account_id] = dates
            all_query_dates.update(dates)

        # 2. Query Account-Level Facts for this workspace
        acc_ids = [acc.account_id for acc in user_accounts]
        stmt = (
            select(AnalyticsEntityFact)
            .where(
                AnalyticsEntityFact.workspace_id == workspace_id,
                AnalyticsEntityFact.account_id.in_(acc_ids),
                AnalyticsEntityFact.entity_level == "account",
                AnalyticsEntityFact.date.in_(list(all_query_dates)),
            )
            .order_by(AnalyticsEntityFact.date.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()

        # Group facts by account
        facts_by_account: Dict[str, List[AnalyticsEntityFact]] = {}
        for r in rows:
            target_dates = acc_dates_map.get(r.account_id, [])
            if r.date in target_dates:
                facts_by_account.setdefault(r.account_id, []).append(r)

        # 3. Aggregate each account's numbers
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

        for acc in user_accounts:
            short_name = get_short_account_label(acc.name, acc.account_id)
            account_currency = normalize_currency(acc.currency)
            is_blocked = not acc.is_active or acc.account_status in [2, 101]
            acc_facts = facts_by_account.get(acc.account_id, [])

            if not acc_facts:
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
                    "group_ids": group_map.get(acc.account_id, []),
                    "timezone_name": acc.timezone_name,
                    "currency": account_currency,
                    "account_status": acc.account_status,
                    "status_label": "Нет сохранённых данных" if not is_blocked else "Заблокирован",
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
                        else "Метрики ещё не синхронизированы в хранилище"
                    ),
                })
                continue

            accounts_synced += 1
            acc_spend = sum(f.spend for f in acc_facts)
            acc_impressions = sum(f.impressions for f in acc_facts)
            acc_reach = max((f.reach for f in acc_facts), default=0)
            acc_clicks = sum(f.clicks for f in acc_facts)
            acc_unique_clicks = sum(f.unique_clicks for f in acc_facts)
            acc_link_clicks = sum(f.link_clicks for f in acc_facts)
            acc_outbound_clicks = sum(f.outbound_clicks for f in acc_facts)
            acc_landing_page_views = sum(f.landing_page_views for f in acc_facts)
            acc_leads = sum(f.leads for f in acc_facts)
            acc_regs = sum(f.registrations for f in acc_facts)
            acc_purchases = sum(f.purchases for f in acc_facts)

            acc_cpc = (acc_spend / acc_clicks) if acc_clicks > 0 else 0.0
            acc_ctr = ((acc_clicks / acc_impressions) * 100) if acc_impressions > 0 else 0.0
            acc_frequency = (acc_impressions / acc_reach) if acc_reach > 0 else 0.0
            acc_cpm = (acc_spend / acc_impressions * 1000) if acc_impressions > 0 else 0.0
            acc_ctr_link = ((acc_link_clicks / acc_impressions) * 100) if acc_impressions > 0 else 0.0
            acc_ctr_outbound = ((acc_outbound_clicks / acc_impressions) * 100) if acc_impressions > 0 else 0.0

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

            account_results.append({
                "account_id": acc.account_id,
                "name": acc.name,
                "short_name": short_name,
                "custom_name": acc.custom_name or "",
                "note": acc.note or "",
                "group_ids": group_map.get(acc.account_id, []),
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
                "cost_per_lead": cost_per_event(acc_spend, acc_leads, digits=2),
                "cost_per_registration": cost_per_event(acc_spend, acc_regs, digits=2),
                "cost_per_purchase": cost_per_event(acc_spend, acc_purchases, digits=2),
                "cpc": round(acc_cpc, 2),
                "ctr": round(acc_ctr, 2),
                "cpc_link": cost_per_event(acc_spend, acc_link_clicks, digits=2),
                "ctr_link": round(acc_ctr_link, 2),
                "ctr_outbound": round(acc_ctr_outbound, 2),
                "cost_per_landing_page_view": cost_per_event(acc_spend, acc_landing_page_views, digits=2),
                "adsets": [],
                "has_error": False,
                "is_banned": is_blocked,
                "data_status": "synced",
                "data_status_label": "Метрики загружены из Analytics Fact Store",
            })

        # 4. Currency totals & mixed currency logic (BL-015)
        currency_totals = [
            {
                "currency": curr,
                "accounts_count": int(data["accounts_count"]),
                "spend": round(data["spend"], 2),
                "impressions": data["impressions"],
                "clicks": data["clicks"],
                "link_clicks": data["link_clicks"],
                "landing_page_views": data["landing_page_views"],
                "leads": data["leads"],
                "registrations": data["registrations"],
                "purchases": data["purchases"],
                "cpm": cost_per_event(data["spend"] * 1000, data["impressions"], digits=2),
                "cpc": cost_per_event(data["spend"], data["clicks"], digits=2),
                "cpc_link": cost_per_event(data["spend"], data["link_clicks"], digits=2),
                "cost_per_landing_page_view": cost_per_event(
                    data["spend"], data["landing_page_views"], digits=2
                ),
                "cost_per_lead": cost_per_event(data["spend"], data["leads"], digits=2),
                "cost_per_registration": cost_per_event(data["spend"], data["registrations"], digits=2),
                "cost_per_purchase": cost_per_event(data["spend"], data["purchases"], digits=2),
            }
            for curr, data in sorted(currency_buckets.items())
        ]
        mixed_currencies = len(currency_totals) > 1
        display_currency = (
            currency_totals[0]["currency"]
            if len(currency_totals) == 1 and currency_totals[0]["currency"] != UNKNOWN_CURRENCY
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
            if total_impressions > 0
            else None
        ) if monetary_totals_available else None
        avg_cpc_link = (
            cost_per_event(total_spend, total_link_clicks, digits=2)
            if monetary_totals_available
            else None
        )
        avg_ctr_link = (
            (total_link_clicks / total_impressions) * 100
            if total_impressions > 0
            else None
        )
        avg_ctr_outbound = (
            (total_outbound_clicks / total_impressions) * 100
            if total_impressions > 0
            else None
        )
        metrics_coverage = round((accounts_synced / len(user_accounts)) * 100, 1) if user_accounts else 0.0
        quality_status = (
            "complete"
            if accounts_synced == len(user_accounts)
            else ("partial" if accounts_synced else "unavailable")
        )

        return {
            "period": period,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": "PostgreSQL Fact Store",
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
                cost_per_event(total_spend, total_landing_page_views, digits=2)
                if monetary_totals_available
                else None
            ),
            "cost_per_lead": (
                cost_per_event(total_spend, total_leads, digits=2)
                if monetary_totals_available
                else None
            ),
            "cost_per_registration": (
                cost_per_event(total_spend, total_regs, digits=2)
                if monetary_totals_available
                else None
            ),
            "cost_per_purchase": (
                cost_per_event(total_spend, total_purchases, digits=2)
                if monetary_totals_available
                else None
            ),
            "accounts_count": len(user_accounts),
            "accounts": account_results,
            "data_quality": {
                "status": quality_status,
                "accounts_total": len(user_accounts),
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

    @staticmethod
    async def get_hierarchy_breakdown(
        session,
        workspace_id: int,
        parent_entity_id: str,
        entity_level: str,
        period: str = "today",
        user_accounts: Optional[List[Account]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve drill-down rows (Campaigns for Account, AdSets for Campaign, Ads for AdSet).

        Strict multi-tenancy enforcement: checks workspace_id and validates parent ownership.
        """
        valid_levels = {"campaign", "adset", "ad"}
        if entity_level not in valid_levels:
            return []

        # Find timezone for the parent account if user_accounts provided
        target_account = None
        if user_accounts:
            for acc in user_accounts:
                if acc.account_id == parent_entity_id:
                    target_account = acc
                    break

        timezone_name = target_account.timezone_name if target_account else "UTC"
        dates = resolve_account_period_dates(timezone_name, period)

        stmt = (
            select(AnalyticsEntityFact)
            .where(
                AnalyticsEntityFact.workspace_id == workspace_id,
                AnalyticsEntityFact.parent_entity_id == parent_entity_id,
                AnalyticsEntityFact.entity_level == entity_level,
                AnalyticsEntityFact.date.in_(dates),
            )
            .order_by(AnalyticsEntityFact.entity_id.asc(), AnalyticsEntityFact.date.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return []

        # Group facts by entity_id across the target date range
        grouped: Dict[str, List[AnalyticsEntityFact]] = {}
        for r in rows:
            grouped.setdefault(r.entity_id, []).append(r)

        results = []
        for entity_id, entity_facts in grouped.items():
            first_fact = entity_facts[-1]  # Latest snapshot for status/budget
            spend = sum(f.spend for f in entity_facts)
            impressions = sum(f.impressions for f in entity_facts)
            reach = max((f.reach for f in entity_facts), default=0)
            clicks = sum(f.clicks for f in entity_facts)
            link_clicks = sum(f.link_clicks for f in entity_facts)
            outbound_clicks = sum(f.outbound_clicks for f in entity_facts)
            landing_page_views = sum(f.landing_page_views for f in entity_facts)
            leads = sum(f.leads for f in entity_facts)
            regs = sum(f.registrations for f in entity_facts)
            purchases = sum(f.purchases for f in entity_facts)

            cpc = (spend / clicks) if clicks > 0 else 0.0
            ctr = ((clicks / impressions) * 100) if impressions > 0 else 0.0
            cpm = ((spend / impressions) * 1000) if impressions > 0 else 0.0
            ctr_link = ((link_clicks / impressions) * 100) if impressions > 0 else 0.0
            ctr_outbound = ((outbound_clicks / impressions) * 100) if impressions > 0 else 0.0

            results.append({
                "entity_id": entity_id,
                "entity_name": first_fact.entity_name or f"{entity_level.capitalize()} {entity_id}",
                "entity_level": entity_level,
                "parent_entity_id": parent_entity_id,
                "account_id": first_fact.account_id,
                "currency": first_fact.currency,
                "status": first_fact.status,
                "effective_status": first_fact.effective_status,
                "daily_budget": first_fact.daily_budget,
                "spend": round(spend, 2),
                "impressions": impressions,
                "reach": reach,
                "cpm": round(cpm, 2),
                "clicks": clicks,
                "link_clicks": link_clicks,
                "outbound_clicks": outbound_clicks,
                "landing_page_views": landing_page_views,
                "cpc": round(cpc, 2),
                "ctr": round(ctr, 2),
                "cpc_link": cost_per_event(spend, link_clicks, digits=2),
                "ctr_link": round(ctr_link, 2),
                "ctr_outbound": round(ctr_outbound, 2),
                "leads": leads,
                "registrations": regs,
                "purchases": purchases,
                "cost_per_lead": cost_per_event(spend, leads, digits=2),
                "cost_per_registration": cost_per_event(spend, regs, digits=2),
                "cost_per_purchase": cost_per_event(spend, purchases, digits=2),
                "cost_per_landing_page_view": cost_per_event(spend, landing_page_views, digits=2),
            })

        # Sort by spend descending
        results.sort(key=lambda x: x["spend"], reverse=True)
        return results

    @staticmethod
    async def cleanup_expired_facts(
        session,
        ad_days: int = 60,
        adset_days: int = 90,
        campaign_days: int = 90,
        account_days: int = 365,
    ) -> int:
        """Purge metric facts older than the specified retention limits."""
        now = datetime.now(timezone.utc).date()
        deleted_count = 0

        retention_matrix = [
            ("ad", (now - timedelta(days=ad_days)).isoformat()),
            ("adset", (now - timedelta(days=adset_days)).isoformat()),
            ("campaign", (now - timedelta(days=campaign_days)).isoformat()),
            ("account", (now - timedelta(days=account_days)).isoformat()),
        ]

        for level, cutoff_date in retention_matrix:
            stmt = delete(AnalyticsEntityFact).where(
                AnalyticsEntityFact.entity_level == level,
                AnalyticsEntityFact.date < cutoff_date,
            )
            res = await session.execute(stmt)
            deleted_count += res.rowcount or 0

        if deleted_count > 0:
            await session.commit()
            logger.info("Cleaned up %d expired analytics facts from storage", deleted_count)

        return deleted_count
