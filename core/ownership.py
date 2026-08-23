from sqlalchemy import false


def owned_by(model, user):
    """Owner check using workspace_id or owner_user_id."""
    if getattr(model, "workspace_id", None) is not None and getattr(user, "active_workspace_id", None):
        return model.workspace_id == user.active_workspace_id
    if user and getattr(user, "id", None):
        return model.owner_user_id == user.id
    return false()


def owned_by_ids(model, owner_user_id: int, legacy_owner_id: str = None):
    if owner_user_id is not None:
        return model.owner_user_id == owner_user_id
    return false()


def assign_owner(entity, user) -> None:
    """Assign owner user and active workspace."""
    entity.owner_user_id = getattr(user, "id", None)
    if hasattr(entity, "workspace_id") and getattr(user, "active_workspace_id", None):
        entity.workspace_id = user.active_workspace_id


def entity_is_owned_by(entity, user) -> bool:
    if not user:
        return False
    if hasattr(entity, "workspace_id") and entity.workspace_id and getattr(user, "active_workspace_id", None):
        return entity.workspace_id == user.active_workspace_id
    return getattr(entity, "owner_user_id", None) == getattr(user, "id", None)
