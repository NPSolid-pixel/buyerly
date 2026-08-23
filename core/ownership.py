from sqlalchemy import or_, and_, false


def owned_by(model, user):
    """Owner check using workspace_id or owner_user_id."""
    if not user:
        return false()

    conditions = []
    if getattr(user, "active_workspace_id", None) and hasattr(model, "workspace_id"):
        conditions.append(model.workspace_id == user.active_workspace_id)
    if getattr(user, "id", None) and hasattr(model, "owner_user_id"):
        conditions.append(model.owner_user_id == user.id)

    if len(conditions) == 1:
        return conditions[0]
    elif len(conditions) > 1:
        return or_(*conditions)
    return false()


def owned_by_ids(model, owner_user_id: int, legacy_owner_id: str = None):
    if owner_user_id is not None and hasattr(model, "owner_user_id"):
        return model.owner_user_id == owner_user_id
    return false()


def assign_owner(entity, user) -> None:
    """Assign owner user and active workspace."""
    if user:
        if hasattr(entity, "owner_user_id"):
            entity.owner_user_id = getattr(user, "id", None)
        if hasattr(entity, "workspace_id") and getattr(user, "active_workspace_id", None):
            entity.workspace_id = user.active_workspace_id


def entity_is_owned_by(entity, user) -> bool:
    if not user:
        return False
    if hasattr(entity, "workspace_id") and getattr(entity, "workspace_id", None) is not None and getattr(user, "active_workspace_id", None):
        if entity.workspace_id == user.active_workspace_id:
            return True
    if hasattr(entity, "owner_user_id") and getattr(entity, "owner_user_id", None) is not None and getattr(user, "id", None):
        return entity.owner_user_id == user.id
    return False
