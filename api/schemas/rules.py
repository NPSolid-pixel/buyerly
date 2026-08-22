from typing import List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.metrics import validate_runtime_rule


class ConditionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["spend", "cpl", "cpreg", "cpp", "leads", "registrations", "purchases", "ctr", "cpc"] = "spend"
    operator: Literal["gte", "gt", "lte", "lt", "eq"] = "gte"
    value: float = Field(default=0.0, ge=0, le=1_000_000_000, allow_inf_nan=False)
    time_window: Literal["today", "yesterday", "last_3d", "last_7d"] = "today"


class RulePresetItem(BaseModel):
    id: int
    name: str
    action: str
    conditions: List[ConditionItem]
    condition_logic: str = "and"
    cooldown_minutes: int = 0
    check_interval_minutes: int = 5
    notify_tg: bool = True
    budget_change_percent: float = 0.0
    budget_max_daily: float = 0.0
    currency_mode: Literal["account"] = "account"
    created_at: str


class CreatePresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    action: Literal["turn_off", "notify_only", "turn_on", "increase_budget", "decrease_budget"] = "turn_off"
    conditions: List[ConditionItem] = Field(min_length=1, max_length=20)
    condition_logic: Literal["and", "or"] = "and"
    cooldown_minutes: int = Field(default=0, ge=0, le=10_080)
    check_interval_minutes: int = Field(default=5, ge=1, le=1_440)
    notify_tg: bool = True
    budget_change_percent: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)
    budget_max_daily: float = Field(default=0.0, ge=0, le=10_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_safe_action_parameters(self):
        validate_runtime_rule(
            {
                "action": self.action,
                "conditions": [condition.model_dump() for condition in self.conditions],
                "logic": self.condition_logic,
                "cooldown_minutes": self.cooldown_minutes,
                "check_interval": self.check_interval_minutes,
                "budget_change_percent": self.budget_change_percent,
                "budget_max_daily": self.budget_max_daily,
            }
        )
        return self


class RuleGroupWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    position: Optional[int] = None
    preset_ids: List[int] = Field(default_factory=list, min_length=0, max_length=50)


class RuleGroupResponse(BaseModel):
    id: int
    name: str
    description: str
    position: int = 0
    preset_ids: List[int]
    rules: List[RulePresetItem]
    created_at: str


class RuleGroupsReorderRequest(BaseModel):
    group_ids: List[int] = Field(min_length=0, max_length=100)


class ApplyPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: int = Field(gt=0)
