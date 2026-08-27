import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import api.auth as api_auth_module
import api.routes as api_routes_module
import api.server as api_server_module
from api.server import create_app
from core.config import settings
from database.db import Base, hash_password
from database.models import (
    Account,
    AuditEvent,
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
    RulePreset,
)
from tests.test_api import generate_valid_telegram_init_data


from tests.test_db_helper import create_test_engine, init_test_db


class TestWorkspaces(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        api_routes_module._summary_cache.clear()
        self.test_engine = create_test_engine()
        self.test_session_maker = async_sessionmaker(self.test_engine, class_=AsyncSession, expire_on_commit=False)
        await init_test_db(self.test_engine)

        api_routes_module.async_session_maker = self.test_session_maker
        api_auth_module.async_session_maker = self.test_session_maker
        api_server_module.async_session_maker = self.test_session_maker

        settings.BOT_TOKEN = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'
        settings.ADMIN_CHAT_ID = '8634201356'

        async with self.test_session_maker() as session:
            artem = User(
                telegram_id='777000111',
                username='artem',
                full_name='Артем',
                password_hash=hash_password('artem-password'),
                role='buyer',
                is_approved=True,
            )
            session.add(artem)
            await session.flush()

            ws1 = Workspace(
                name='Buyerly',
                slug='buyerly',
                badge_text='B',
                badge_color='#F5A300',
                owner_user_id=artem.id,
            )
            session.add(ws1)
            await session.flush()

            session.add(WorkspaceMember(workspace_id=ws1.id, user_id=artem.id, role='owner'))
            artem.active_workspace_id = ws1.id

            acc1 = Account(
                account_id='act_111111',
                name='Buyerly Account 1',
                workspace_id=ws1.id,
                owner_user_id=artem.id,
                timezone_name='UTC',
                currency='USD',
            )
            session.add(acc1)

            rule1 = RulePreset(
                workspace_id=ws1.id,
                owner_user_id=artem.id,
                name='Buyerly Stop Rule',
                action='turn_off',
                conditions='[]',
            )
            session.add(rule1)

            await session.commit()

        self.app = create_app()

    async def asyncTearDown(self):
        await self.test_engine.dispose()

    async def test_workspace_lifecycle_and_data_isolation(self):
        artem_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000111, 'first_name': 'Artem', 'username': 'artem'},
        )
        headers = {'Authorization': f'tma {artem_data}'}
        transport = httpx.ASGITransport(app=self.app)

        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            # 1. Get initial workspaces list
            res = await client.get('/api/workspaces', headers=headers)
            self.assertEqual(res.status_code, 200)
            workspaces = res.json()
            self.assertEqual(len(workspaces), 1)
            self.assertEqual(workspaces[0]['name'], 'Buyerly')
            self.assertEqual(workspaces[0]['slug'], 'buyerly')
            self.assertTrue(workspaces[0]['is_active'])
            self.assertEqual(workspaces[0]['accounts_count'], 1)

            # 2. Get /api/me
            me_res = await client.get('/api/me', headers=headers)
            self.assertEqual(me_res.status_code, 200)
            me = me_res.json()
            self.assertEqual(me['username'], 'artem')
            self.assertEqual(me['active_workspace']['name'], 'Buyerly')
            self.assertEqual(len(me['workspaces']), 1)

            # 3. Create a second workspace: 'Canada Traffic'
            create_res = await client.post(
                '/api/workspaces',
                headers=headers,
                json={
                    'name': 'Canada Traffic',
                    'badge_color': '#7C3AED',
                    'badge_text': 'C',
                },
            )
            self.assertEqual(create_res.status_code, 200)
            canada_ws = create_res.json()
            self.assertEqual(canada_ws['name'], 'Canada Traffic')
            self.assertEqual(canada_ws['slug'], 'canada-traffic')
            self.assertEqual(canada_ws['badge_color'], '#7C3AED')
            self.assertTrue(canada_ws['is_active'])
            self.assertEqual(canada_ws['accounts_count'], 0)

            # 4. Verify that in Canada Traffic workspace, accounts are empty (isolated!)
            acc_res = await client.get('/api/accounts', headers=headers)
            self.assertEqual(acc_res.status_code, 200)
            self.assertEqual(acc_res.json(), [])

            # 5. Add an account to Canada Traffic workspace
            async with self.test_session_maker() as session:
                acc_canada = Account(
                    account_id='act_222222',
                    name='Canada Scale 1',
                    workspace_id=canada_ws['id'],
                    timezone_name='UTC',
                    currency='USD',
                )
                session.add(acc_canada)
                await session.commit()

            acc_res2 = await client.get('/api/accounts', headers=headers)
            self.assertEqual(len(acc_res2.json()), 1)
            self.assertEqual(acc_res2.json()[0]['account_id'], 'act_222222')

            # 6. Switch back to Buyerly workspace
            switch_res = await client.post(
                '/api/workspaces/switch',
                headers=headers,
                json={'slug': 'buyerly'},
            )
            self.assertEqual(switch_res.status_code, 200)
            self.assertEqual(switch_res.json()['active_workspace']['slug'], 'buyerly')

            # Accounts in Buyerly workspace should only be act_111111
            acc_res3 = await client.get('/api/accounts', headers=headers)
            self.assertEqual(len(acc_res3.json()), 1)
            self.assertEqual(acc_res3.json()[0]['account_id'], 'act_111111')

            # 7. Update workspace settings (rename and color change)
            patch_res = await client.patch(
                f"/api/workspaces/{canada_ws['id']}",
                headers=headers,
                json={
                    'name': 'Canada Traffic Pro',
                    'badge_color': '#1D4ED8',
                },
            )
            self.assertEqual(patch_res.status_code, 200)
            self.assertEqual(patch_res.json()['name'], 'Canada Traffic Pro')
            self.assertEqual(patch_res.json()['badge_color'], '#1D4ED8')

            # 8. Delete workspace
            del_res = await client.delete(
                f"/api/workspaces/{canada_ws['id']}",
                headers=headers,
            )
            self.assertEqual(del_res.status_code, 200)
            self.assertEqual(del_res.json()['status'], 'ok')

            # 9. Ensure only 1 workspace left and cannot delete the only one
            ws_final = (await client.get('/api/workspaces', headers=headers)).json()
            self.assertEqual(len(ws_final), 1)

            del_only = await client.delete(
                f"/api/workspaces/{ws_final[0]['id']}",
                headers=headers,
            )
            self.assertEqual(del_only.status_code, 400)

    async def test_spa_workspace_slug_routes(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            routes = [
                '/buyerly/home',
                '/buyerly/accounts',
                '/buyerly/groups/1',
                '/buyerly/groups/canada',
                '/buyerly/facebook-accounts',
                '/buyerly/facebook-groups/1',
                '/buyerly/rules',
                '/buyerly/rule-groups/1',
                '/buyerly/chats',
                '/buyerly/chats/1',
                '/buyerly/collection/1',
                '/buyerly/collection/1/view/default',
                '/buyerly/summary',
                '/buyerly/logs',
                '/canada-traffic/home',
                '/canada-traffic/accounts',
                '/canada-traffic/groups/1',
                '/groups/1',
                '/facebook-groups/1',
                '/rule-groups/1',
                '/chats',
                '/chats/1',
                '/collection/1',
                '/sign-in',
                '/login',
            ]
            for r in routes:
                res = await client.get(r)
                self.assertEqual(res.status_code, 200, f'Route {r} should return 200')

    async def test_workspace_invite_model_schema_and_persistence(self):
        async with self.test_session_maker() as session:
            # 1. Retrieve Artem and his workspace
            artem = (await session.execute(select(User).where(User.username == 'artem'))).scalar_one()
            ws = (await session.execute(select(Workspace).where(Workspace.slug == 'buyerly'))).scalar_one()

            # 2. Create a targeted single-use invite
            invite = WorkspaceInvite(
                workspace_id=ws.id,
                token="inv_test_token_12345",
                email="colleague@agency.com",
                role="buyer",
                inviter_user_id=artem.id,
                status="pending",
                max_uses=1,
                used_count=0,
            )
            session.add(invite)
            await session.commit()
            await session.refresh(invite)

            self.assertIsNotNone(invite.id)
            self.assertEqual(invite.workspace_id, ws.id)
            self.assertEqual(invite.token, "inv_test_token_12345")
            self.assertEqual(invite.email, "colleague@agency.com")
            self.assertEqual(invite.role, "buyer")
            self.assertEqual(invite.inviter_user_id, artem.id)
            self.assertEqual(invite.status, "pending")
            self.assertEqual(invite.max_uses, 1)
            self.assertEqual(invite.used_count, 0)
            self.assertIsNotNone(invite.created_at)
            self.assertIsNotNone(invite.updated_at)

            # 3. Create a public multi-use invite link
            public_link = WorkspaceInvite(
                workspace_id=ws.id,
                token="inv_public_link_67890",
                role="viewer",
                inviter_user_id=artem.id,
                status="pending",
                max_uses=0,
                used_count=0,
            )
            session.add(public_link)
            await session.commit()
            await session.refresh(public_link)

            self.assertIsNotNone(public_link.id)
            self.assertIsNone(public_link.email)
            self.assertEqual(public_link.role, "viewer")
            self.assertEqual(public_link.max_uses, 0)

    async def test_workspace_members_api_lifecycle(self):
        artem_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000111, 'first_name': 'Artem', 'username': 'artem'},
        )
        artem_headers = {'Authorization': f'tma {artem_data}'}

        bob_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000222, 'first_name': 'Bob', 'username': 'bob'},
        )
        bob_headers = {'Authorization': f'tma {bob_data}'}

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            # Create Bob and Charlie in DB
            async with self.test_session_maker() as session:
                bob = User(
                    telegram_id='777000222',
                    username='bob',
                    full_name='Bob Buyer',
                    password_hash=hash_password('bob-password'),
                    role='buyer',
                    is_approved=True,
                )
                charlie = User(
                    telegram_id='777000333',
                    username='charlie',
                    full_name='Charlie Viewer',
                    password_hash=hash_password('charlie-password'),
                    role='buyer',
                    is_approved=True,
                )
                session.add_all([bob, charlie])
                await session.flush()

                ws = (await session.execute(select(Workspace).where(Workspace.slug == 'buyerly'))).scalar_one()
                session.add(WorkspaceMember(workspace_id=ws.id, user_id=bob.id, role='buyer'))
                session.add(WorkspaceMember(workspace_id=ws.id, user_id=charlie.id, role='viewer'))
                bob.active_workspace_id = ws.id
                charlie.active_workspace_id = ws.id
                await session.commit()
                ws_id = ws.id
                bob_id = bob.id
                charlie_id = charlie.id

            # 1. GET members as Artem (owner)
            res = await client.get(f'/api/workspaces/{ws_id}/members', headers=artem_headers)
            self.assertEqual(res.status_code, 200)
            members = res.json()
            self.assertEqual(len(members), 3)
            self.assertEqual(members[0]['username'], 'artem')
            self.assertEqual(members[0]['role'], 'owner')
            self.assertTrue(members[0]['is_current_user'])

            # 2. PATCH Bob role to admin as Artem
            patch_res = await client.patch(
                f'/api/workspaces/{ws_id}/members/{bob_id}',
                json={'role': 'admin'},
                headers=artem_headers,
            )
            self.assertEqual(patch_res.status_code, 200)
            self.assertEqual(patch_res.json()['role'], 'admin')

            # 3. Artem cannot change own role via PATCH
            artem_id = members[0]['user_id']
            patch_self = await client.patch(
                f'/api/workspaces/{ws_id}/members/{artem_id}',
                json={'role': 'buyer'},
                headers=artem_headers,
            )
            self.assertEqual(patch_self.status_code, 400)

            # 4. Bob (admin) promotes Charlie to admin
            patch_charlie = await client.patch(
                f'/api/workspaces/{ws_id}/members/{charlie_id}',
                json={'role': 'admin'},
                headers=bob_headers,
            )
            self.assertEqual(patch_charlie.status_code, 200)
            self.assertEqual(patch_charlie.json()['role'], 'admin')

            # 5. Bob (admin) tries to delete Charlie (admin) -> 403 Forbidden
            del_admin = await client.delete(
                f'/api/workspaces/{ws_id}/members/{charlie_id}',
                headers=bob_headers,
            )
            self.assertEqual(del_admin.status_code, 403)

            # 6. Artem (owner) deletes Charlie -> 200 OK
            del_owner = await client.delete(
                f'/api/workspaces/{ws_id}/members/{charlie_id}',
                headers=artem_headers,
            )
            self.assertEqual(del_owner.status_code, 200)

            # 7. Artem (owner) tries to leave -> 400 Bad Request
            leave_owner = await client.post(f'/api/workspaces/{ws_id}/leave', headers=artem_headers)
            self.assertEqual(leave_owner.status_code, 400)

            # 8. Bob leaves -> 200 OK
            leave_bob = await client.post(f'/api/workspaces/{ws_id}/leave', headers=bob_headers)
            self.assertEqual(leave_bob.status_code, 200)
            self.assertEqual(leave_bob.json()['status'], 'ok')

            # 9. Transfer ownership test
            # Re-add Bob as buyer
            async with self.test_session_maker() as session:
                session.add(WorkspaceMember(workspace_id=ws_id, user_id=bob_id, role='buyer'))
                await session.commit()

            transfer_res = await client.post(
                f'/api/workspaces/{ws_id}/transfer-ownership',
                json={'new_owner_user_id': bob_id},
                headers=artem_headers,
            )
            self.assertEqual(transfer_res.status_code, 200)
            self.assertEqual(transfer_res.json()['status'], 'ok')

            # Verify in DB: Bob is now owner, Artem is admin
            async with self.test_session_maker() as session:
                updated_ws = (await session.execute(select(Workspace).where(Workspace.id == ws_id))).scalar_one()
                self.assertEqual(updated_ws.owner_user_id, bob_id)
                bob_m = (await session.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws_id, WorkspaceMember.user_id == bob_id))).scalar_one()
                self.assertEqual(bob_m.role, 'owner')
                artem_m = (await session.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws_id, WorkspaceMember.user_id == artem_id))).scalar_one()
                self.assertEqual(artem_m.role, 'admin')

    async def test_workspace_invites_api_lifecycle(self):
        artem_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000111, 'first_name': 'Artem', 'username': 'artem'},
        )
        artem_headers = {'Authorization': f'tma {artem_data}'}

        dave_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000444, 'first_name': 'Dave', 'username': 'dave'},
        )
        dave_headers = {'Authorization': f'tma {dave_data}'}

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            # Create Dave in DB
            async with self.test_session_maker() as session:
                dave = User(
                    telegram_id='777000444',
                    username='dave',
                    full_name='Dave Analyst',
                    password_hash=hash_password('dave-password'),
                    role='buyer',
                    is_approved=True,
                )
                session.add(dave)
                ws = (await session.execute(select(Workspace).where(Workspace.slug == 'buyerly'))).scalar_one()
                ws_id = ws.id
                await session.commit()

            # 1. Artem creates a targeted email invite
            create_res = await client.post(
                f'/api/workspaces/{ws_id}/invites',
                json={
                    'email': 'dave@buyerly.app',
                    'role': 'buyer',
                    'expires_in_days': 7,
                    'max_uses': 1,
                },
                headers=artem_headers,
            )
            self.assertEqual(create_res.status_code, 200)
            invite_data = create_res.json()
            self.assertEqual(invite_data['email'], 'dave@buyerly.app')
            self.assertEqual(invite_data['role'], 'buyer')
            self.assertEqual(invite_data['status'], 'pending')
            self.assertTrue(invite_data['token'].startswith('inv_'))
            self.assertIn(invite_data['token'], invite_data['invite_url'])
            invite_id = invite_data['id']
            invite_token = invite_data['token']

            # 2. Artem creates a public multi-use invite link
            public_create_res = await client.post(
                f'/api/workspaces/{ws_id}/invites',
                json={
                    'role': 'viewer',
                    'expires_in_days': 30,
                    'max_uses': 0,
                },
                headers=artem_headers,
            )
            self.assertEqual(public_create_res.status_code, 200)
            public_token = public_create_res.json()['token']

            # 3. List invites as Artem
            list_res = await client.get(f'/api/workspaces/{ws_id}/invites', headers=artem_headers)
            self.assertEqual(list_res.status_code, 200)
            invites_list = list_res.json()
            self.assertGreaterEqual(len(invites_list), 2)
            self.assertEqual(invites_list[0]['inviter_name'], 'Артем')

            # 4. Public check of token (without auth)
            check_res = await client.get(f'/api/invites/{invite_token}')
            self.assertEqual(check_res.status_code, 200)
            check_data = check_res.json()
            self.assertTrue(check_data['valid'])
            self.assertEqual(check_data['workspace_name'], 'Buyerly')
            self.assertEqual(check_data['role'], 'buyer')
            self.assertEqual(check_data['target_email'], 'dave@buyerly.app')

            # Check invalid token
            check_invalid = await client.get('/api/invites/inv_does_not_exist')
            self.assertEqual(check_invalid.status_code, 200)
            self.assertFalse(check_invalid.json()['valid'])

            # 5. Revoke the targeted invite as Artem
            del_invite_res = await client.delete(f'/api/workspaces/{ws_id}/invites/{invite_id}', headers=artem_headers)
            self.assertEqual(del_invite_res.status_code, 200)

            # Public check now shows revoked
            check_revoked = await client.get(f'/api/invites/{invite_token}')
            self.assertEqual(check_revoked.status_code, 200)
            self.assertFalse(check_revoked.json()['valid'])
            self.assertEqual(check_revoked.json()['status'], 'revoked')

            # Dave tries to accept revoked token -> 400
            accept_revoked = await client.post(f'/api/invites/{invite_token}/accept', headers=dave_headers)
            self.assertEqual(accept_revoked.status_code, 400)

            # 6. Dave accepts the public viewer link -> 200 OK
            accept_res = await client.post(f'/api/invites/{public_token}/accept', headers=dave_headers)
            self.assertEqual(accept_res.status_code, 200)
            self.assertEqual(accept_res.json()['status'], 'ok')
            self.assertEqual(accept_res.json()['role'], 'viewer')
            self.assertEqual(accept_res.json()['workspace_id'], ws_id)

            # 7. Dave accepts same link again (already member) -> 200 OK
            accept_again = await client.post(f'/api/invites/{public_token}/accept', headers=dave_headers)
            self.assertEqual(accept_again.status_code, 200)
            self.assertEqual(accept_again.json()['status'], 'ok')

            # 8. Verify Dave in DB
            async with self.test_session_maker() as session:
                dave_db = (await session.execute(select(User).where(User.username == 'dave'))).scalar_one()
                self.assertEqual(dave_db.active_workspace_id, ws_id)
                dave_member = (await session.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws_id, WorkspaceMember.user_id == dave_db.id))).scalar_one()
                self.assertEqual(dave_member.role, 'viewer')

    async def test_workspace_resource_scoping_and_viewer_rbac_protection(self):
        artem_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000111, 'first_name': 'Artem', 'username': 'artem'},
        )
        artem_headers = {'Authorization': f'tma {artem_data}'}

        viewer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000555, 'first_name': 'Victor', 'username': 'victor_viewer'},
        )
        viewer_headers = {'Authorization': f'tma {viewer_data}'}

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            # Create Victor (viewer in Buyerly)
            async with self.test_session_maker() as session:
                victor = User(
                    telegram_id='777000555',
                    username='victor_viewer',
                    full_name='Victor Viewer',
                    password_hash=hash_password('victor-password'),
                    role='buyer',
                    is_approved=True,
                )
                session.add(victor)
                ws = (await session.execute(select(Workspace).where(Workspace.slug == 'buyerly'))).scalar_one()
                session.add(WorkspaceMember(workspace_id=ws.id, user_id=victor.id, role='viewer'))
                victor.active_workspace_id = ws.id
                await session.commit()
                ws_id = ws.id

            # 1. Victor can read accounts and presets
            accs_res = await client.get('/api/accounts', headers=viewer_headers)
            self.assertEqual(accs_res.status_code, 200)

            presets_res = await client.get('/api/presets', headers=viewer_headers)
            self.assertEqual(presets_res.status_code, 200)

            groups_res = await client.get('/api/account-groups', headers=viewer_headers)
            self.assertEqual(groups_res.status_code, 200)

            # 2. Victor (viewer) is blocked from creating presets
            create_preset_res = await client.post(
                '/api/presets',
                json={
                    'name': 'Viewer Rule',
                    'action': 'turn_off',
                    'conditions': [{'metric': 'cpl', 'operator': 'gt', 'value': 25.0}],
                },
                headers=viewer_headers,
            )
            self.assertEqual(create_preset_res.status_code, 403)
            self.assertIn('Viewer', create_preset_res.json()['detail'])

            # 3. Victor is blocked from creating account groups
            create_group_res = await client.post(
                '/api/account-groups',
                json={'name': 'Viewer Group', 'account_ids': []},
                headers=viewer_headers,
            )
            self.assertEqual(create_group_res.status_code, 403)

            # 4. Victor is blocked from batch-adding accounts
            batch_add_res = await client.post(
                '/api/accounts/batch-add',
                json={
                    'accounts': [{'account_id': 'act_999999', 'name': 'Viewer Acc'}],
                    'access_token': 'fake_token',
                },
                headers=viewer_headers,
            )
            self.assertEqual(batch_add_res.status_code, 403)

            # 5. Victor is blocked from mutating account rules
            toggle_res = await client.post('/api/accounts/act_111111/toggle-rules', headers=viewer_headers)
            self.assertEqual(toggle_res.status_code, 403)

            # 6. Victor is blocked from deleting accounts
            del_acc_res = await client.delete('/api/accounts/act_111111', headers=viewer_headers)
            self.assertEqual(del_acc_res.status_code, 403)

    async def test_batch_add_accounts_cannot_take_over_account_from_another_workspace(self):
        # Create second user in a separate workspace
        async with self.test_session_maker() as session:
            hacker = User(
                telegram_id='777000999',
                username='hacker',
                full_name='Hacker Buyer',
                password_hash=hash_password('hacker-password'),
                role='buyer',
                is_approved=True,
            )
            session.add(hacker)
            await session.flush()

            ws_other = Workspace(
                name='Other WS',
                slug='other-ws',
                badge_text='O',
                badge_color='#10B981',
                owner_user_id=hacker.id,
            )
            session.add(ws_other)
            await session.flush()
            session.add(WorkspaceMember(workspace_id=ws_other.id, user_id=hacker.id, role='owner'))
            hacker.active_workspace_id = ws_other.id
            await session.commit()

        hacker_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000999, 'first_name': 'Hacker', 'username': 'hacker'},
        )
        hacker_headers = {'Authorization': f'tma {hacker_data}'}

        mock_meta_info = {
            'id': 'act_111111',
            'name': 'Hijacked Account',
            'account_status': 1,
            'status_label': 'Активен',
            'timezone_name': 'UTC',
            'currency': 'USD',
        }

        transport = httpx.ASGITransport(app=self.app)
        with patch.object(api_routes_module.meta_client, 'get_account_info', new=AsyncMock(return_value=mock_meta_info)):
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                res = await client.post(
                    '/api/accounts/batch-add',
                    json={
                        'accounts': [{'account_id': 'act_111111', 'name': 'Stolen'}],
                        'access_token': 'hacker_token',
                    },
                    headers=hacker_headers,
                )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['success_count'], 0)
        self.assertEqual(data['error_count'], 1)
        self.assertIn('другом рабочем пространстве', data['errors'][0]['error'])

        # Verify account in DB was NOT modified
        async with self.test_session_maker() as session:
            acc = (await session.execute(select(Account).where(Account.account_id == 'act_111111'))).scalar_one()
            artem = (await session.execute(select(User).where(User.telegram_id == '777000111'))).scalar_one()
            self.assertEqual(acc.owner_user_id, artem.id)
            self.assertEqual(acc.name, 'Buyerly Account 1')

    async def test_workspace_admin_can_refresh_account_without_reassigning_owner(self):
        async with self.test_session_maker() as session:
            artem = (await session.execute(select(User).where(User.username == 'artem'))).scalar_one()
            ws = (await session.execute(select(Workspace).where(Workspace.slug == 'buyerly'))).scalar_one()
            workspace_admin = User(
                telegram_id='777000555',
                username='workspace_admin',
                full_name='Workspace Admin',
                password_hash=hash_password('workspace-admin-password'),
                role='buyer',
                is_approved=True,
                active_workspace_id=ws.id,
            )
            session.add(workspace_admin)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=ws.id,
                    user_id=workspace_admin.id,
                    role='admin',
                )
            )
            original_owner_id = artem.id
            await session.commit()

        admin_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000555, 'first_name': 'Workspace', 'username': 'workspace_admin'},
        )
        headers = {'Authorization': f'tma {admin_data}'}
        mock_meta_info = {
            'id': 'act_111111',
            'name': 'Refreshed Account',
            'account_status': 1,
            'status_label': 'Активен',
            'timezone_name': 'UTC',
            'currency': 'USD',
        }

        transport = httpx.ASGITransport(app=self.app)
        with patch.object(api_routes_module.meta_client, 'get_account_info', new=AsyncMock(return_value=mock_meta_info)):
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                response = await client.post(
                    '/api/accounts/batch-add',
                    json={
                        'accounts': [{'account_id': 'act_111111', 'name': 'Refreshed'}],
                        'access_token': 'replacement_token',
                    },
                    headers=headers,
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['success_count'], 1)
        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == 'act_111111'))
            ).scalar_one()
            self.assertEqual(account.owner_user_id, original_owner_id)

    async def test_targeted_workspace_invite_rejects_mismatched_email(self):
        artem_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000111, 'first_name': 'Artem', 'username': 'artem'},
        )
        artem_headers = {'Authorization': f'tma {artem_data}'}

        now_dt = datetime.now(timezone.utc)
        async with self.test_session_maker() as session:
            imposter = User(
                telegram_id='777000888',
                username='imposter',
                email='imposter@evil.com',
                email_verified_at=now_dt,
                full_name='Imposter User',
                password_hash=hash_password('imposter-password'),
                role='buyer',
                is_approved=True,
            )
            recipient = User(
                telegram_id='777000777',
                username='recipient',
                email='legit@company.com',
                email_verified_at=now_dt,
                full_name='Legit User',
                password_hash=hash_password('legit-password'),
                role='buyer',
                is_approved=True,
            )
            session.add_all([imposter, recipient])
            ws = (await session.execute(select(Workspace).where(Workspace.slug == 'buyerly'))).scalar_one()
            ws_id = ws.id
            await session.commit()

        imposter_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000888, 'first_name': 'Imposter', 'username': 'imposter'},
        )
        imposter_headers = {'Authorization': f'tma {imposter_data}'}

        recipient_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000777, 'first_name': 'Legit', 'username': 'recipient'},
        )
        recipient_headers = {'Authorization': f'tma {recipient_data}'}

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            create_res = await client.post(
                f'/api/workspaces/{ws_id}/invites',
                json={
                    'email': 'legit@company.com',
                    'role': 'buyer',
                    'expires_in_days': 7,
                    'max_uses': 1,
                },
                headers=artem_headers,
            )
            self.assertEqual(create_res.status_code, 200)
            token = create_res.json()['token']

            # Imposter tries to accept targeted invite -> 403 Forbidden
            imposter_accept = await client.post(f'/api/invites/{token}/accept', headers=imposter_headers)
            self.assertEqual(imposter_accept.status_code, 403)
            self.assertIn('предназначено для другого email-адреса', imposter_accept.json()['detail'])

            # Intended recipient accepts -> 200 OK
            recipient_accept = await client.post(f'/api/invites/{token}/accept', headers=recipient_headers)
            self.assertEqual(recipient_accept.status_code, 200)
            self.assertEqual(recipient_accept.json()['status'], 'ok')

    async def test_targeted_workspace_invite_requires_verified_email(self):
        artem_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000111, 'first_name': 'Artem', 'username': 'artem'},
        )
        artem_headers = {'Authorization': f'tma {artem_data}'}

        # Unverified recipient has matching email string but email_verified_at is None
        async with self.test_session_maker() as session:
            unverified_user = User(
                telegram_id='777000999',
                username='unverified_user',
                email='partner@agency.com',
                email_verified_at=None,
                full_name='Unverified Partner',
                password_hash=hash_password('partner-password'),
                role='buyer',
                is_approved=True,
            )
            no_email_user = User(
                telegram_id='777000555',
                username='no_email_user',
                email=None,
                email_verified_at=None,
                full_name='No Email User',
                password_hash=hash_password('noemail-password'),
                role='buyer',
                is_approved=True,
            )
            session.add_all([unverified_user, no_email_user])
            ws = (await session.execute(select(Workspace).where(Workspace.slug == 'buyerly'))).scalar_one()
            ws_id = ws.id
            await session.commit()

        unverified_headers = {
            'Authorization': f"tma {generate_valid_telegram_init_data(settings.BOT_TOKEN, {'id': 777000999, 'first_name': 'Unverified', 'username': 'unverified_user'})}"
        }
        no_email_headers = {
            'Authorization': f"tma {generate_valid_telegram_init_data(settings.BOT_TOKEN, {'id': 777000555, 'first_name': 'NoEmail', 'username': 'no_email_user'})}"
        }

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            create_res = await client.post(
                f'/api/workspaces/{ws_id}/invites',
                json={
                    'email': 'partner@agency.com',
                    'role': 'buyer',
                    'expires_in_days': 7,
                    'max_uses': 1,
                },
                headers=artem_headers,
            )
            self.assertEqual(create_res.status_code, 200)
            token = create_res.json()['token']

            # 1. User without email tries to accept -> 403 Forbidden (email is NOT auto-assigned)
            no_email_accept = await client.post(f'/api/invites/{token}/accept', headers=no_email_headers)
            self.assertEqual(no_email_accept.status_code, 403)
            self.assertIn('требуется подтверждённый адрес электронной почты', no_email_accept.json()['detail'])

            # Verify no_email_user still has NO email assigned in DB
            async with self.test_session_maker() as session:
                db_no_email = (await session.execute(select(User).where(User.username == 'no_email_user'))).scalar_one()
                self.assertIsNone(db_no_email.email)

            # 2. User with unverified matching email tries to accept -> 403 Forbidden
            unverified_accept = await client.post(f'/api/invites/{token}/accept', headers=unverified_headers)
            self.assertEqual(unverified_accept.status_code, 403)
            self.assertIn('требуется подтверждённый адрес электронной почты', unverified_accept.json()['detail'])

            # 3. Mark unverified user as verified in DB -> now acceptance succeeds
            async with self.test_session_maker() as session:
                db_user = (await session.execute(select(User).where(User.username == 'unverified_user'))).scalar_one()
                db_user.email_verified_at = datetime.now(timezone.utc)
                await session.commit()

            verified_accept = await client.post(f'/api/invites/{token}/accept', headers=unverified_headers)
            self.assertEqual(verified_accept.status_code, 200)
            self.assertEqual(verified_accept.json()['status'], 'ok')

    async def test_invite_audit_logging_no_raw_tokens(self):
        artem_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {'id': 777000111, 'first_name': 'Artem', 'username': 'artem'},
        )
        artem_headers = {'Authorization': f'tma {artem_data}'}

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            async with self.test_session_maker() as session:
                ws = (await session.execute(select(Workspace).where(Workspace.slug == 'buyerly'))).scalar_one()
                ws_id = ws.id

            # Create an invite
            create_res = await client.post(
                f'/api/workspaces/{ws_id}/invites',
                json={
                    'email': 'audit_test@example.com',
                    'role': 'buyer',
                    'expires_in_days': 7,
                    'max_uses': 1,
                },
                headers=artem_headers,
            )
            self.assertEqual(create_res.status_code, 200)
            invite_data = create_res.json()
            raw_token = invite_data['token']
            invite_id = invite_data['id']

            # Revoke the invite
            revoke_res = await client.delete(f'/api/workspaces/{ws_id}/invites/{invite_id}', headers=artem_headers)
            self.assertEqual(revoke_res.status_code, 200)

            # Check audit events in DB
            async with self.test_session_maker() as session:
                audit_events = (
                    await session.execute(
                        select(AuditEvent)
                        .where(AuditEvent.category == 'WORKSPACE_INVITE')
                        .order_by(AuditEvent.id.asc())
                    )
                ).scalars().all()

                self.assertGreaterEqual(len(audit_events), 2)
                event_types = [e.event_type for e in audit_events]
                self.assertIn('INVITE_CREATE', event_types)
                self.assertIn('INVITE_REVOKE', event_types)

                # Ensure NO raw token appears in message or details of any audit event
                for e in audit_events:
                    self.assertNotIn(raw_token, e.message)
                    details_str = json.dumps(e.details)
                    self.assertNotIn(raw_token, details_str)



