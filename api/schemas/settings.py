from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class SetIntervalRequest(BaseModel):
    minutes: int = Field(ge=1, le=1440)
    current_password: SecretStr


class AutomationSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr
    poll_interval_minutes: int = Field(ge=5, le=120)
    critical_rule_interval_minutes: int = Field(ge=1, le=15)
    stop_confirmation_minutes: int = Field(default=10, ge=0, le=60)
    inventory_cache_minutes: int = Field(ge=1, le=30)
    account_health_interval_minutes: int = Field(ge=5, le=120)
    max_concurrent_accounts: int = Field(ge=1, le=5)
    max_concurrent_actions: int = Field(ge=1, le=10)
    usage_soft_limit_percent: int = Field(ge=40, le=85)
    usage_hard_limit_percent: int = Field(ge=60, le=95)
    adaptive_polling_enabled: bool = True

    @model_validator(mode="after")
    def validate_usage_thresholds(self):
        if self.usage_soft_limit_percent >= self.usage_hard_limit_percent:
            raise ValueError("Мягкий порог квоты должен быть ниже жёсткого")
        return self
