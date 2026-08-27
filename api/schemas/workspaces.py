from typing import Optional
from pydantic import BaseModel, Field, field_validator


def validate_workspace_logo_url_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return ""
    if any(char in cleaned for char in ("<", ">", '"', "'", "\r", "\n", "\t", "\0")):
        raise ValueError("logo_url содержит недопустимые символы")
    if cleaned.startswith("//") or "/../" in cleaned or cleaned.endswith("/.."):
        raise ValueError("Некорректный путь logo_url")
    if cleaned.startswith("/uploads/workspaces/"):
        return cleaned
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    raise ValueError(
        "logo_url должен начинаться с https://, http:// или /uploads/workspaces/"
    )


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

    _validate_logo_url = field_validator("logo_url")(validate_workspace_logo_url_value)


class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    badge_color: Optional[str] = Field(None, max_length=30)
    badge_text: Optional[str] = Field(None, max_length=5)
    logo_url: Optional[str] = Field(None, max_length=500)

    _validate_logo_url = field_validator("logo_url")(validate_workspace_logo_url_value)


class SwitchWorkspaceRequest(BaseModel):
    workspace_id: Optional[int] = None
    slug: Optional[str] = None
