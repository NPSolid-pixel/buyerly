from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

from api.schemas.workspaces import WorkspaceItem


class UserProfileResponse(BaseModel):
    telegram_id: Optional[str] = None
    username: str
    full_name: str
    first_name: str = ""
    last_name: str = ""
    email: Optional[str] = None
    avatar_url: str = ""
    role: str
    is_approved: bool
    onboarding_step: str = "completed"
    onboarding_completed: bool = False
    active_workspace: Optional[WorkspaceItem] = None
    workspaces: List[WorkspaceItem] = Field(default_factory=list)


class RequestTemporaryPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=3, max_length=255)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class LoginResponse(BaseModel):
    token: str
    username: str
    full_name: str
    role: str
    message: str = "Авторизация успешна"


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(default="", max_length=255)
    new_password: str = Field(..., min_length=8, max_length=255)


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: Optional[str] = Field(None, max_length=120)
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=255)
    avatar_url: Optional[str] = Field(None, max_length=500)
    telegram_id: Optional[str] = Field(None, max_length=64)
