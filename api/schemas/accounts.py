from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class AccountLatestMetrics(BaseModel):
    period: Literal["today"] = "today"
    generated_at: str = ""
    saved_at: str = ""
    data_status: Literal["synced", "blocked", "error"]
    data_status_label: str = ""
    spend: Optional[float] = None
    impressions: int = 0
    reach: int = 0
    frequency: Optional[float] = None
    cpm: Optional[float] = None
    clicks: int = 0
    unique_clicks: int = 0
    link_clicks: int = 0
    outbound_clicks: int = 0
    landing_page_views: int = 0
    leads: int = 0
    registrations: int = 0
    purchases: int = 0
    cpl: Optional[float] = None
    cpreg: Optional[float] = None
    cpp: Optional[float] = None
    cpc: Optional[float] = None
    ctr: Optional[float] = None
    ctr_link: Optional[float] = None
    cpc_link: Optional[float] = None
    cost_per_lpv: Optional[float] = None
    roas: Optional[float] = None


class AccountItem(BaseModel):
    id: int
    account_id: str
    name: str
    custom_name: str
    note: str
    connection_type: Literal["facebook_login", "system_user"]
    owner_id: str
    batch_name: str
    timezone_name: str
    currency: str
    account_status: int
    status_label: str
    rules_enabled: bool
    is_active: bool
    active_rules: List[Dict[str, Any]] = Field(default_factory=list)
    group_ids: List[int] = Field(default_factory=list)
    latest_metrics: Optional[AccountLatestMetrics] = None
    created_at: str


class AccountProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    custom_name: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)


class AccountGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    account_ids: List[str] = Field(default_factory=list, max_length=250)


class AccountGroupItem(BaseModel):
    id: int
    name: str
    description: str
    account_ids: List[str] = Field(default_factory=list)
    accounts_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class ParseRawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(..., min_length=1, max_length=65536)


class ParsedAccountItem(BaseModel):
    account_id: str
    parsed_name: str


class BatchAddAccountEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(..., min_length=1, max_length=60)
    name: Optional[str] = Field(default="", max_length=120)


class BatchAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: List[BatchAddAccountEntry] = Field(..., min_length=1, max_length=500)
    batch_name: Optional[str] = Field(default="-", max_length=120)
    access_token: str = Field(..., min_length=1, max_length=1024)
