from typing import Optional
from pydantic import BaseModel, Field


class WorkspaceItem(BaseModel):
    id: int
    name: str
    slug: str
    badge_text: str
    badge_color: str
    logo_url: str = ""
    role: str
    is_active: bool
    accounts_count: int = 0
    members_count: int = 1


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    slug: Optional[str] = Field(None, max_length=60)
    badge_color: Optional[str] = Field("#F5A300", max_length=30)
    badge_text: Optional[str] = Field(None, max_length=5)
    logo_url: Optional[str] = Field(None, max_length=500)


class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    badge_color: Optional[str] = Field(None, max_length=30)
    badge_text: Optional[str] = Field(None, max_length=5)
    logo_url: Optional[str] = Field(None, max_length=500)


class SwitchWorkspaceRequest(BaseModel):
    workspace_id: Optional[int] = None
    slug: Optional[str] = None
