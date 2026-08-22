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
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    full_name: str
    role: str
    message: str = "Успешный вход"


class ChangePasswordRequest(BaseModel):
    old_password: str = ""
    new_password: str


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    telegram_id: Optional[str] = None
