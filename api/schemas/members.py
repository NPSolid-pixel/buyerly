from typing import Optional
from pydantic import BaseModel, Field


class WorkspaceMemberItem(BaseModel):
    id: int
    user_id: int
    username: str
    full_name: str
    first_name: str = ""
    last_name: str = ""
    email: Optional[str] = None
    avatar_url: str = ""
    telegram_id: Optional[str] = None
    role: str
    joined_at: str
    is_current_user: bool = False


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|buyer|viewer)$")


class TransferOwnershipRequest(BaseModel):
    new_owner_user_id: int


class CreateWorkspaceInviteRequest(BaseModel):
    email: Optional[str] = Field(None, max_length=255)
    role: str = Field(default="buyer", pattern="^(admin|buyer|viewer)$")
    expires_in_days: int = Field(default=7, ge=1, le=365)
    max_uses: int = Field(default=1, ge=0, le=1000)


class WorkspaceInviteItem(BaseModel):
    id: int
    workspace_id: int
    workspace_name: str
    token: str
    invite_url: str
    email: Optional[str] = None
    role: str
    status: str
    max_uses: int
    used_count: int
    inviter_name: str
    expires_at: Optional[str] = None
    created_at: str


class PublicInviteInfoResponse(BaseModel):
    valid: bool
    status: str
    workspace_name: Optional[str] = None
    workspace_slug: Optional[str] = None
    workspace_badge_text: Optional[str] = None
    workspace_badge_color: Optional[str] = None
    inviter_name: Optional[str] = None
    role: Optional[str] = None
    target_email: Optional[str] = None
    expires_at: Optional[str] = None
    message: Optional[str] = None
