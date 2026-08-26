import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, delete, func, or_, select

from api.auth import get_current_user
from api.deps import (
    _clean_rule_group_name,
    _ensure_compatible_presets,
    _ensure_compatible_rule_set,
    _ensure_stable_account_owner,
    _get_owned_presets,
    _load_active_rules,
    _load_group_presets,
    _preset_response,
    _preset_snapshot,
    _rule_group_response,
    _validated_condition_payloads,
    ensure_workspace_write_access,
    get_user_workspace,
    get_user_workspace_member,
    record_security_event_and_raise,
)
from api.schemas import (
    ApplyPresetRequest,
    ConditionItem,
    CreatePresetRequest,
    RuleGroupResponse,
    RuleGroupsReorderRequest,
    RuleGroupWriteRequest,
    RulePresetItem,
)
from core.ownership import owned_by
from core.rule_examples import ensure_rule_examples
from database.db import async_session_maker
from database.models import (
    Account,
    RuleGroup,
    RuleGroupItem,
    RulePreset,
    User,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Rules & Presets"])


@router.get("/presets", response_model=List[RulePresetItem])
async def list_presets(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        await ensure_rule_examples(session, user)
        ws = await get_user_workspace(session, user)
        scope_clause = (
            or_(RulePreset.workspace_id == ws.id, and_(RulePreset.workspace_id.is_(None), owned_by(RulePreset, user)))
            if ws
            else owned_by(RulePreset, user)
        )
        stmt = select(RulePreset).where(scope_clause).order_by(RulePreset.id.desc())
        res = await session.execute(stmt)
        presets = res.scalars().all()
        return [_preset_response(preset) for preset in presets]


@router.post("/presets", response_model=RulePresetItem)
async def create_preset(payload: CreatePresetRequest, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "создания правил")

        condition_payloads = _validated_condition_payloads(payload.conditions)
        preset = RulePreset(
            workspace_id=ws.id if ws else None,
            owner_user_id=user.id,
            name=payload.name.strip() or "Новое правило",
            action=payload.action or "turn_off",
            conditions=condition_payloads,
            condition_logic=payload.condition_logic or "and",
            cooldown_minutes=payload.cooldown_minutes or 0,
            check_interval_minutes=payload.check_interval_minutes or 5,
            notify_tg=payload.notify_tg if payload.notify_tg is not None else True,
            budget_change_percent=payload.budget_change_percent or 0.0,
            budget_max_daily=payload.budget_max_daily or 0.0,
        )
        session.add(preset)
        await session.commit()
        await session.refresh(preset)
        return RulePresetItem(
            id=preset.id,
            name=preset.name,
            action=preset.action,
            conditions=[ConditionItem(**condition) for condition in condition_payloads],
            condition_logic=preset.condition_logic,
            cooldown_minutes=preset.cooldown_minutes,
            check_interval_minutes=preset.check_interval_minutes,
            notify_tg=preset.notify_tg,
            budget_change_percent=preset.budget_change_percent,
            budget_max_daily=preset.budget_max_daily,
            created_at=preset.created_at.strftime("%Y-%m-%d %H:%M") if preset.created_at else "",
        )


@router.put("/presets/{preset_id}", response_model=RulePresetItem)
async def update_preset(preset_id: int, payload: CreatePresetRequest, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "редактирования правил")

        condition_payloads = _validated_condition_payloads(payload.conditions)
        scope_clause = (
            or_(RulePreset.workspace_id == ws.id, and_(RulePreset.workspace_id.is_(None), owned_by(RulePreset, user)))
            if ws
            else owned_by(RulePreset, user)
        )
        stmt = select(RulePreset).where(RulePreset.id == preset_id, scope_clause)
        res = await session.execute(stmt)
        preset = res.scalar_one_or_none()
        if not preset:
            raise HTTPException(status_code=404, detail="Пресет не найден")

        preset.name = payload.name.strip() or preset.name
        preset.action = payload.action or "turn_off"
        preset.conditions = json.dumps(condition_payloads)
        if payload.condition_logic is not None:
            preset.condition_logic = payload.condition_logic
        if payload.cooldown_minutes is not None:
            preset.cooldown_minutes = payload.cooldown_minutes
        if payload.check_interval_minutes is not None:
            preset.check_interval_minutes = payload.check_interval_minutes
        if payload.notify_tg is not None:
            preset.notify_tg = payload.notify_tg
        if payload.budget_change_percent is not None:
            preset.budget_change_percent = payload.budget_change_percent
        if payload.budget_max_daily is not None:
            preset.budget_max_daily = payload.budget_max_daily

        updated_snapshot = _preset_snapshot(preset)
        acc_stmt = select(Account)
        if ws:
            acc_stmt = acc_stmt.where(or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user))))
        else:
            acc_stmt = acc_stmt.where(owned_by(Account, user))
        account_res = await session.execute(acc_stmt)
        for account in account_res.scalars().all():
            active_rules = _load_active_rules(account.active_rules)
            changed = False
            for index, active_rule in enumerate(active_rules):
                if active_rule.get("preset_id") == preset_id:
                    active_rules[index] = updated_snapshot.copy()
                    changed = True
            if changed:
                _ensure_compatible_rule_set(active_rules)
                account.active_rules = json.dumps(active_rules)

        await session.commit()
        await session.refresh(preset)
        return RulePresetItem(
            id=preset.id,
            name=preset.name,
            action=preset.action,
            conditions=[ConditionItem(**condition) for condition in condition_payloads],
            condition_logic=preset.condition_logic,
            cooldown_minutes=preset.cooldown_minutes,
            check_interval_minutes=preset.check_interval_minutes,
            notify_tg=preset.notify_tg,
            budget_change_percent=preset.budget_change_percent,
            budget_max_daily=preset.budget_max_daily,
            created_at=preset.created_at.strftime("%Y-%m-%d %H:%M") if preset.created_at else "",
        )


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "удаления правил")

        scope_clause = (
            or_(RulePreset.workspace_id == ws.id, and_(RulePreset.workspace_id.is_(None), owned_by(RulePreset, user)))
            if ws
            else owned_by(RulePreset, user)
        )
        stmt = select(RulePreset).where(RulePreset.id == preset_id, scope_clause)
        res = await session.execute(stmt)
        preset = res.scalar_one_or_none()
        if not preset:
            raise HTTPException(status_code=404, detail="Пресет не найден")

        # Remove the exact preset ID from linked account snapshots in this workspace.
        acc_stmt = select(Account)
        if ws:
            acc_stmt = acc_stmt.where(or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user))))
        else:
            acc_stmt = acc_stmt.where(owned_by(Account, user))
        acc_res = await session.execute(acc_stmt)
        for acc in acc_res.scalars().all():
            active_rules = _load_active_rules(acc.active_rules)
            remaining_rules = [r for r in active_rules if r.get("preset_id") != preset_id]
            if len(remaining_rules) != len(active_rules):
                acc.active_rules = json.dumps(remaining_rules)
                if not remaining_rules:
                    acc.rules_enabled = False

        await session.execute(delete(RuleGroupItem).where(RuleGroupItem.preset_id == preset_id))
        await session.execute(delete(RulePreset).where(RulePreset.id == preset_id))
        await session.commit()
        return {"success": True, "message": "Пресет удален"}


@router.get("/rule-groups", response_model=List[RuleGroupResponse])
async def list_rule_groups(user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        await ensure_rule_examples(session, user)
        ws = await get_user_workspace(session, user)
        scope_clause = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        groups = (
            await session.execute(
                select(RuleGroup)
                .where(scope_clause)
                .order_by(RuleGroup.position.asc(), RuleGroup.id.asc())
            )
        ).scalars().all()
        presets_by_group = await _load_group_presets(session, [group.id for group in groups])
        return [
            _rule_group_response(group, presets_by_group.get(group.id, []))
            for group in groups
        ]


@router.put("/rule-groups/reorder", response_model=List[RuleGroupResponse])
async def reorder_rule_groups(
    payload: RuleGroupsReorderRequest,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws = await get_user_workspace(session, user)
        scope_clause = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        groups = (
            await session.execute(
                select(RuleGroup).where(scope_clause)
            )
        ).scalars().all()
        group_map = {g.id: g for g in groups}

        for idx, gid in enumerate(payload.group_ids):
            if gid in group_map:
                group_map[gid].position = idx

        await session.commit()

        ordered_groups = (
            await session.execute(
                select(RuleGroup)
                .where(scope_clause)
                .order_by(RuleGroup.position.asc(), RuleGroup.id.asc())
            )
        ).scalars().all()
        presets_by_group = await _load_group_presets(session, [g.id for g in ordered_groups])
        return [
            _rule_group_response(g, presets_by_group.get(g.id, []))
            for g in ordered_groups
        ]


@router.post("/rule-groups", response_model=RuleGroupResponse)
async def create_rule_group(
    payload: RuleGroupWriteRequest,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "создания групп правил")

        presets = await _get_owned_presets(session, user, payload.preset_ids)
        _ensure_compatible_presets(presets)
        scope_clause = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        if payload.position is not None:
            position = payload.position
        else:
            max_pos = (
                await session.execute(
                    select(func.max(RuleGroup.position)).where(owned_by(RuleGroup, user))
                )
            ).scalar()
            position = (max_pos + 1) if max_pos is not None else 0

        group = RuleGroup(
            workspace_id=ws.id if ws else None,
            owner_user_id=user.id,
            name=_clean_rule_group_name(payload.name),
            description=payload.description.strip(),
            position=position,
        )
        session.add(group)
        await session.flush()
        session.add_all(
            RuleGroupItem(group_id=group.id, preset_id=preset.id, position=pos)
            for pos, preset in enumerate(presets)
        )
        await session.commit()
        await session.refresh(group)
        return _rule_group_response(group, presets)


@router.put("/rule-groups/{group_id}", response_model=RuleGroupResponse)
async def update_rule_group(
    group_id: int,
    payload: RuleGroupWriteRequest,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "редактирования групп правил")

        scope_clause = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        group = (
            await session.execute(
                select(RuleGroup).where(
                    RuleGroup.id == group_id,
                    scope_clause,
                )
            )
        ).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="Группа правил не найдена.")

        presets = await _get_owned_presets(session, user, payload.preset_ids)
        _ensure_compatible_presets(presets)
        group.name = _clean_rule_group_name(payload.name)
        group.description = payload.description.strip()
        if payload.position is not None:
            group.position = payload.position
        await session.execute(delete(RuleGroupItem).where(RuleGroupItem.group_id == group.id))
        session.add_all(
            RuleGroupItem(group_id=group.id, preset_id=preset.id, position=pos)
            for pos, preset in enumerate(presets)
        )
        await session.commit()
        await session.refresh(group)
        return _rule_group_response(group, presets)


@router.delete("/rule-groups/{group_id}")
async def delete_rule_group(
    group_id: int,
    user: User = Depends(get_current_user),
):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "удаления групп правил")

        scope_clause = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        group = (
            await session.execute(
                select(RuleGroup).where(
                    RuleGroup.id == group_id,
                    scope_clause,
                )
            )
        ).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="Группа правил не найдена.")
        await session.execute(delete(RuleGroupItem).where(RuleGroupItem.group_id == group.id))
        await session.delete(group)
        await session.commit()
        return {"success": True, "message": "Группа удалена. Назначенные правила сохранены в кабинетах."}


@router.post("/accounts/{account_id}/assign-rule")
async def assign_rule_to_account(
    account_id: str,
    payload: ApplyPresetRequest,
    user: User = Depends(get_current_user),
):
    """Добавляет правило/пресет к списку правил кабинета."""
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "привязки правил к кабинету")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id, scope_clause)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")
        await _ensure_stable_account_owner(session, acc)

        # If preset_id provided, load preset
        if payload.preset_id:
            p_scope = (
                or_(RulePreset.workspace_id == ws.id, and_(RulePreset.workspace_id.is_(None), owned_by(RulePreset, user)))
                if ws
                else owned_by(RulePreset, user)
            )
            p_stmt = select(RulePreset).where(RulePreset.id == payload.preset_id, p_scope)
            p_res = await session.execute(p_stmt)
            preset = p_res.scalar_one_or_none()
            if not preset:
                raise HTTPException(status_code=404, detail="Пресет не найден.")

            new_rule = _preset_snapshot(preset)
            if new_rule.get("needs_review"):
                raise HTTPException(
                    status_code=400,
                    detail="Правило имеет небезопасные или устаревшие параметры. Откройте и пересохраните его.",
                )
        else:
            raise HTTPException(status_code=400, detail="Custom rules without preset are no longer supported.")

        active_rules = _load_active_rules(acc.active_rules)

        # Check if preset already attached
        if any(r.get("preset_id") == new_rule["preset_id"] for r in active_rules):
            raise HTTPException(status_code=400, detail="Это правило уже привязано к кабинету.")

        active_rules.append(new_rule)
        _ensure_compatible_rule_set(active_rules)
        acc.active_rules = json.dumps(active_rules)
        acc.rules_enabled = True

        await session.commit()
        return {
            "account_id": acc.account_id,
            "active_rules": active_rules,
            "rules_enabled": acc.rules_enabled,
            "message": f"Правило '{new_rule['name']}' успешно добавлено к кабинету",
        }


@router.post("/accounts/{account_id}/assign-rule-group/{group_id}")
async def assign_rule_group_to_account(
    account_id: str,
    group_id: int,
    user: User = Depends(get_current_user),
):
    """Atomically attach every rule in a reusable group, skipping duplicates."""
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "назначения группы правил")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        account_stmt = select(Account).where(Account.account_id == acc_id, scope_clause)
        account = (await session.execute(account_stmt)).scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")
        await _ensure_stable_account_owner(session, account)

        group_scope = (
            or_(RuleGroup.workspace_id == ws.id, and_(RuleGroup.workspace_id.is_(None), owned_by(RuleGroup, user)))
            if ws
            else owned_by(RuleGroup, user)
        )
        group_stmt = select(RuleGroup).where(RuleGroup.id == group_id, group_scope)
        group = (await session.execute(group_stmt)).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="Группа правил не найдена.")

        group_items = (
            await session.execute(
                select(RuleGroupItem)
                .where(RuleGroupItem.group_id == group.id)
                .order_by(RuleGroupItem.position, RuleGroupItem.id)
            )
        ).scalars().all()
        if not group_items:
            raise HTTPException(status_code=400, detail="В группе нет правил.")

        presets = await _get_owned_presets(
            session,
            user,
            [item.preset_id for item in group_items],
            owner_user_id=account.owner_user_id,
        )
        active_rules = _load_active_rules(account.active_rules)
        attached_ids = {rule.get("preset_id") for rule in active_rules}
        added_presets = [preset for preset in presets if preset.id not in attached_ids]
        new_snapshots = [_preset_snapshot(preset) for preset in added_presets]
        if any(snapshot.get("needs_review") for snapshot in new_snapshots):
            raise HTTPException(
                status_code=400,
                detail="В группе есть небезопасное или устаревшее правило. Пересохраните его перед назначением.",
            )
        active_rules.extend(new_snapshots)
        _ensure_compatible_rule_set(active_rules)
        account.active_rules = json.dumps(active_rules)
        account.rules_enabled = bool(active_rules)
        await session.commit()

        skipped_count = len(presets) - len(added_presets)
        message = (
            f"Группа '{group.name}' назначена: добавлено правил — {len(added_presets)}"
            if added_presets
            else f"Все правила группы '{group.name}' уже назначены кабинету"
        )
        return {
            "account_id": account.account_id,
            "group_id": group.id,
            "group_name": group.name,
            "added_count": len(added_presets),
            "skipped_count": skipped_count,
            "active_rules": active_rules,
            "rules_enabled": account.rules_enabled,
            "message": message,
        }


@router.post("/accounts/{account_id}/detach-rule/{preset_id}")
async def detach_rule_from_account(
    account_id: str,
    preset_id: int,
    user: User = Depends(get_current_user),
):
    """Удаляет конкретное правило из списка кабинета."""
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "отвязки правил от кабинета")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id, scope_clause)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        active_rules = _load_active_rules(acc.active_rules)

        initial_len = len(active_rules)
        active_rules = [r for r in active_rules if r.get("preset_id") != preset_id]

        if len(active_rules) == initial_len:
            raise HTTPException(status_code=404, detail="Правило не найдено в этом кабинете.")

        acc.active_rules = json.dumps(active_rules)
        if len(active_rules) == 0:
            acc.rules_enabled = False

        await session.commit()
        return {
            "status": "ok",
            "message": "Правило успешно отвязано от кабинета.",
            "active_rules": active_rules,
            "rules_enabled": acc.rules_enabled,
        }


@router.post("/accounts/{account_id}/toggle-rules")
async def toggle_rules(account_id: str, user: User = Depends(get_current_user)):
    async with async_session_maker() as session:
        ws, member = await get_user_workspace_member(session, user)
        ensure_workspace_write_access(user, member, "включения/выключения правил")

        acc_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        scope_clause = (
            or_(Account.workspace_id == ws.id, and_(Account.workspace_id.is_(None), owned_by(Account, user)))
            if ws
            else owned_by(Account, user)
        )
        stmt = select(Account).where(Account.account_id == acc_id, scope_clause)

        res = await session.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Кабинет не найден.")

        active_rules = _load_active_rules(acc.active_rules)
        if not acc.rules_enabled and not active_rules:
            raise HTTPException(
                status_code=400,
                detail="Сначала привяжите хотя бы одно правило к кабинету.",
            )

        if not acc.rules_enabled:
            _ensure_compatible_rule_set(active_rules)

        acc.rules_enabled = not acc.rules_enabled
        await session.commit()
        return {
            "account_id": acc.account_id,
            "rules_enabled": acc.rules_enabled,
            "message": f"Авто-правила {'включены' if acc.rules_enabled else 'выключены'}",
        }
