from api.routers.accounts import router as accounts_router
from api.routers.admin_support import router as admin_support_router
from api.routers.adsets import router as adsets_router
from api.routers.audit import router as audit_router
from api.routers.auth import router as auth_router
from api.routers.members import router as members_router
from api.routers.onboarding import router as onboarding_router
from api.routers.rules import router as rules_router
from api.routers.settings import router as settings_router
from api.routers.summary import router as summary_router
from api.routers.workspaces import router as workspaces_router

__all__ = [
    "accounts_router",
    "admin_support_router",
    "adsets_router",
    "audit_router",
    "auth_router",
    "members_router",
    "onboarding_router",
    "rules_router",
    "settings_router",
    "summary_router",
    "workspaces_router",
]
