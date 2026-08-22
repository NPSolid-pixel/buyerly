import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, delete, func, or_, select

from api.auth import get_current_user
from api.deps import (
    _account_group_ids_by_account,
    _account_group_items,
    _latest_account_metrics_by_id,
    _load_active_rules,
    _load_persisted_summary,
    _validate_account_group_members,
    ensure_workspace_write_access,
    get_user_accounts,
    get_user_workspace,
    get_user_workspace_member,
)
from api.schemas import (
    AccountGroupItem,
    AccountGroupRequest,
    AccountItem,
    AccountProfileUpdateRequest,
    BatchAddRequest,
    ParsedAccountItem,
    ParseRawRequest,
)
from bot.handlers import parse_fb_raw_accounts
from core.currency import normalize_currency
from core.ownership import owned_by
from core.timezones import resolve_account_clock
from database.db import async_session_maker
from database.models import (
    Account,
    AccountGroup,
    AccountGroupMember,
    TelegramUser,
)
from meta_api.client import MetaClient

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Accounts & Groups"])
meta_client = MetaClient()


@router.get("/accounts", response_model=List[AccountItem])
async def list_accounts(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        accounts = await get_user_accounts(session, user)
        group_ids_by_account = await _account_group_ids_by_account(session, user)
        latest_summary = await _load_persisted_summary(
            session,
            owner_id=str(user.telegram_id or ""),
            owner_user_id=user.id,
            period="today",
        )
        latest_metrics = _latest_account_metrics_by_id(latest_summary)

        items = []
        for a in accounts:
            active_rules_list = _load_active_rules(a.active_rules)

            items.append(
                AccountItem(
                    id=a.id,
                    account_id=a.account_id,
                    name=a.name,
                    custom_name=a.custom_name or "",
                    note=a.note or "",
                    connection_type=(
                        "facebook_login" if a.meta_connection_id else "system_user"
                    ),
                    owner_id=a.owner_id,
                    batch_name=a.batch_name or "",
                    timezone_name=a.timezone_name or "UTC",
                    currency=normalize_currency(a.currency),
                    account_status=a.account_status,
                    status_label=a.status_label or "Активен (ACTIVE)",
                    rules_enabled=a.rules_enabled,
                    is_active=a.is_active,
                    active_rules=active_rules_list,
                    group_ids=group_ids_by_account.get(a.account_id, []),
                    latest_metrics=latest_metrics.get(a.account_id),
                    created_at=a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
                )
            )
        return items


@router.get("/account-groups", response_model=List[AccountGroupItem])
async def list_account_groups(user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        return await _account_group_items(session, user)


@router.post("/account-groups", response_model=AccountGroupItem, status_code=status.HTTP_201_CREATED)
async def create_account_group(
    payload: AccountGroupRequest,
    user: TelegramUser = Depends(get_current_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Название группы не может быть пустым")
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "создания групп кабинетов")

        scope_clause = (
            or_(AccountGroup.workspace_id == ws.id, and_(AccountGroup.workspace_id.is_(None), owned_by(AccountGroup, user)))
            if ws
            else owned_by(AccountGroup, user)
        )
        duplicate = (
            await session.execute(
                select(AccountGroup.id).where(
                    scope_clause,
                    func.lower(AccountGroup.name) == name.lower(),
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Группа с таким названием уже существует")

        accounts = await _validate_account_group_members(session, user, payload.account_ids)
        group = AccountGroup(
            workspace_id=ws.id if ws else None,
            owner_id=str(user.telegram_id or ""),
            owner_user_id=user.id,
            name=name,
            description=payload.description.strip(),
        )
        session.add(group)
        await session.flush()
        for position, account in enumerate(accounts):
            session.add(AccountGroupMember(group_id=group.id, account_id=account.id, position=position))
        await session.commit()
        items = await _account_group_items(session, user)
        return next(item for item in items if item.id == group.id)


@router.put("/account-groups/{group_id}", response_model=AccountGroupItem)
async def update_account_group(
    group_id: int,
    payload: AccountGroupRequest,
    user: TelegramUser = Depends(get_current_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Название группы не может быть пустым")
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "редактирования групп кабинетов")

        scope_clause = (
            or_(AccountGroup.workspace_id == ws.id, and_(AccountGroup.workspace_id.is_(None), owned_by(AccountGroup, user)))
            if ws
            else owned_by(AccountGroup, user)
        )
        group = (
            await session.execute(
                select(AccountGroup).where(
                    AccountGroup.id == group_id,
                    scope_clause,
                )
            )
        ).scalar_one_or_none()
        if group is None:
            raise HTTPException(status_code=404, detail="Группа кабинетов не найдена")
        duplicate = (
            await session.execute(
                select(AccountGroup.id).where(
                    scope_clause,
                    func.lower(AccountGroup.name) == name.lower(),
                    AccountGroup.id != group_id,
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Группа с таким названием уже существует")

        accounts = await _validate_account_group_members(session, user, payload.account_ids)
        group.name = name
        group.description = payload.description.strip()
        group.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.execute(delete(AccountGroupMember).where(AccountGroupMember.group_id == group.id))
        for position, account in enumerate(accounts):
            session.add(AccountGroupMember(group_id=group.id, account_id=account.id, position=position))
        await session.commit()
        items = await _account_group_items(session, user)
        return next(item for item in items if item.id == group.id)


@router.delete("/account-groups/{group_id}")
async def delete_account_group(
    group_id: int,
    user: TelegramUser = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "удаления групп кабинетов")

        scope_clause = (
            or_(AccountGroup.workspace_id == ws.id, and_(AccountGroup.workspace_id.is_(None), owned_by(AccountGroup, user)))
            if ws
            else owned_by(AccountGroup, user)
        )
        group = (
            await session.execute(
                select(AccountGroup).where(
                    AccountGroup.id == group_id,
                    scope_clause,
                )
            )
        ).scalar_one_or_none()
        if group is None:
            raise HTTPException(status_code=404, detail="Группа кабинетов не найдена")
        await session.execute(delete(AccountGroupMember).where(AccountGroupMember.group_id == group.id))
        await session.delete(group)
        await session.commit()
    return {"message": "Группа кабинетов удалена", "group_id": group_id}


@router.patch("/accounts/{account_id}/profile")
async def update_account_profile(
    account_id: str,
    payload: AccountProfileUpdateRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """Update owner-only Buyerly labels without changing the Meta account name."""
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "редактирования кабинета")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(scope_clause)
        account = (await session.execute(stmt)).scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        account.custom_name = payload.custom_name.strip()
        account.note = payload.note.strip()
        await session.commit()
        return {
            "account_id": account.account_id,
            "custom_name": account.custom_name,
            "note": account.note,
            "message": "Название и заметка сохранены",
        }


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, user: TelegramUser = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "удаления кабинета")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id)
        if user.role != "admin":
            stmt = stmt.where(scope_clause)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        await session.execute(delete(AccountGroupMember).where(AccountGroupMember.account_id == acc.id))
        await session.execute(delete(Account).where(Account.account_id == acc_id))
        await session.commit()
        return {"success": True, "message": f"Кабинет {acc_id} удален"}


@router.post("/accounts/parse-raw", response_model=List[ParsedAccountItem])
async def parse_raw_text(payload: ParseRawRequest, user: TelegramUser = Depends(get_current_user)):
    parsed = parse_fb_raw_accounts(payload.raw_text)
    return [ParsedAccountItem(account_id=p["account_id"], parsed_name=p["parsed_name"]) for p in parsed]


@router.post("/accounts/batch-add")
async def batch_add_accounts(payload: BatchAddRequest, user: TelegramUser = Depends(get_current_user)):
    if not payload.accounts:
        raise HTTPException(status_code=400, detail="Список кабинетов пуст.")
    if not payload.access_token.strip():
        raise HTTPException(status_code=400, detail="Укажите Access Token Meta.")

    token = payload.access_token.strip()
    batch_name = (payload.batch_name or "-").strip()
    owner_id = user.telegram_id

    added_list = []
    error_list = []

    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "добавления рекламных кабинетов")
        for idx, item in enumerate(payload.accounts, start=1):
            acc_id = item.account_id if item.account_id.startswith("act_") else f"act_{item.account_id}"
            custom_name = item.name.strip() if item.name else ""

            try:
                acc_info = await meta_client.get_account_info(acc_id, token)
                timezone_name = str(acc_info.get("timezone_name") or "").strip()
                if resolve_account_clock(timezone_name) is None:
                    raise RuntimeError(
                        "Meta не вернула поддерживаемый часовой пояс рекламного кабинета."
                    )
                fb_name = acc_info.get("name", acc_id)
                status_code = acc_info.get("account_status", 1)
                status_label = acc_info.get("status_label", "Активен (ACTIVE)")
                currency = normalize_currency(acc_info.get("currency"))

                if batch_name != "-" and len(batch_name) > 0:
                    display_name = f"{batch_name} {idx}" if len(payload.accounts) > 1 else batch_name
                elif custom_name:
                    display_name = custom_name
                else:
                    display_name = fb_name

                res = await session.execute(select(Account).where(Account.account_id == acc_id))
                existing = res.scalar_one_or_none()

                ws = await get_user_workspace(session, user)
                if existing:
                    if (
                        existing.workspace_id is not None
                        and ws is not None
                        and existing.workspace_id != ws.id
                        and user.role != "admin"
                    ):
                        error_list.append({
                            "account_id": acc_id,
                            "error": "Кабинет уже подключён в другом рабочем пространстве."
                        })
                        continue

                    if existing.timezone_name != timezone_name:
                        existing.last_day_start_date = ""
                    existing.name = display_name
                    existing.access_token = token
                    existing.meta_connection_id = None
                    existing.timezone_name = timezone_name
                    existing.currency = currency
                    existing.owner_id = owner_id
                    existing.owner_user_id = user.id
                    existing.workspace_id = ws.id if ws else existing.workspace_id
                    existing.batch_name = batch_name if batch_name != "-" else ""
                    existing.account_status = status_code
                    existing.status_label = status_label
                    existing.is_active = True
                else:
                    new_acc = Account(
                        workspace_id=ws.id if ws else None,
                        account_id=acc_id,
                        name=display_name,
                        access_token=token,
                        owner_id=owner_id,
                        owner_user_id=user.id,
                        batch_name=batch_name if batch_name != "-" else "",
                        timezone_name=timezone_name,
                        currency=currency,
                        account_status=status_code,
                        status_label=status_label,
                        rules_enabled=False,
                        is_active=True,
                    )
                    session.add(new_acc)

                added_list.append({
                    "account_id": acc_id,
                    "name": display_name,
                    "timezone_name": timezone_name,
                    "currency": currency,
                    "status_label": status_label,
                })

            except Exception as e:
                logger.error(f"Error in batch_add for {acc_id}: {e}")
                error_list.append({"account_id": acc_id, "error": str(e)})

        await session.commit()

    return {
        "success_count": len(added_list),
        "error_count": len(error_list),
        "added": added_list,
        "errors": error_list,
    }
