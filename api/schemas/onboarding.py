from typing import List, Optional
from pydantic import BaseModel, Field

from api.schemas.auth import UserProfileResponse
from api.schemas.members import WorkspaceInviteItem
from api.schemas.workspaces import WorkspaceItem


class OnboardingStatusResponse(BaseModel):
    onboarding_step: str
    onboarding_completed: bool
    user: UserProfileResponse
    active_workspace: Optional[WorkspaceItem] = None


class PersonalDetailsRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=60)
    last_name: str = Field(..., min_length=1, max_length=60)
    email: Optional[str] = Field(None, max_length=255)


class CheckSlugResponse(BaseModel):
    slug: str
    available: bool
    message: str


class OnboardingWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    slug: Optional[str] = Field(None, max_length=60)
    badge_color: Optional[str] = Field("#F5A300", max_length=30)
    badge_text: Optional[str] = Field(None, max_length=5)
    logo_url: Optional[str] = Field(None, max_length=500)


class BulkInviteItem(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: str = Field(default="buyer", pattern="^(admin|buyer|viewer)$")


class OnboardingBulkInvitesRequest(BaseModel):
    invites: List[BulkInviteItem] = Field(default_factory=list)


class OnboardingBulkInvitesResponse(BaseModel):
    sent_count: int
    invites: List[WorkspaceInviteItem]
    onboarding_completed: bool
    redirect_url: str = "/"
