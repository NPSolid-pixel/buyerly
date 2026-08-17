from sqlalchemy import and_, false, or_


def owned_by(model, user):
    """Stable owner check with a legacy fallback during rolling migration."""

    legacy_id = str(user.telegram_id or "")
    stable = model.owner_user_id == user.id
    if not legacy_id:
        return stable
    return or_(
        stable,
        and_(model.owner_user_id.is_(None), model.owner_id == legacy_id),
    )


def owned_by_ids(model, owner_user_id: int, legacy_owner_id: str):
    return or_(
        model.owner_user_id == owner_user_id if owner_user_id is not None else false(),
        and_(
            model.owner_user_id.is_(None),
            model.owner_id == str(legacy_owner_id or ""),
        ),
    )


def assign_owner(entity, user) -> None:
    """Write both stable identity and the legacy notification identifier."""

    entity.owner_user_id = user.id
    entity.owner_id = str(user.telegram_id or "")


def entity_is_owned_by(entity, user) -> bool:
    if getattr(entity, "owner_user_id", None) is not None:
        return entity.owner_user_id == user.id
    return bool(user.telegram_id) and entity.owner_id == str(user.telegram_id)
