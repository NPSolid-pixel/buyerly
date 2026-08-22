from typing import List, Dict
from pydantic import BaseModel, Field

SUMMARY_TABLE_COLUMNS = (
    "account",
    "custom_name",
    "note",
    "data",
    "spend",
    "impressions",
    "reach",
    "frequency",
    "cpm",
    "clicks",
    "link_clicks",
    "unique_clicks",
    "outbound_clicks",
    "landing_page_views",
    "ctr",
    "ctr_link",
    "cpc",
    "cpc_link",
    "leads",
    "registrations",
    "purchases",
    "cpl",
    "cpreg",
    "cpp",
)
SUMMARY_FILTER_KEYS = {"query", "status", "group_id"}


class AnalyticsViewPreferenceRequest(BaseModel):
    view_mode: str = Field(default="all", pattern="^(all|overview|delivery|traffic|funnel|custom)$")
    visible_columns: List[str] = Field(default_factory=lambda: list(SUMMARY_TABLE_COLUMNS), max_length=len(SUMMARY_TABLE_COLUMNS))
    column_order: List[str] = Field(default_factory=lambda: list(SUMMARY_TABLE_COLUMNS), max_length=len(SUMMARY_TABLE_COLUMNS))
    column_widths: Dict[str, int] = Field(default_factory=dict, max_length=len(SUMMARY_TABLE_COLUMNS))
    sort_column: str = Field(default="", max_length=64)
    sort_direction: str = Field(default="desc", pattern="^(asc|desc)$")
    filters: Dict[str, str] = Field(default_factory=dict, max_length=len(SUMMARY_FILTER_KEYS))
    period: str = Field(default="today", pattern="^(today|yesterday|last_3d|last_7d)$")
