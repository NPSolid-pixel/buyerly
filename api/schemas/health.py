from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class AccountHealthItem(BaseModel):
    status: Literal["unknown", "healthy", "degraded", "critical"] = "unknown"
    cause: Literal["none", "user", "meta", "system"] = "none"
    signals: Dict[str, Any] = Field(default_factory=dict)
    consecutive_failures: int = 0
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    last_error_code: str = ""
    last_error_message: str = ""
    last_checked_at: Optional[str] = None

