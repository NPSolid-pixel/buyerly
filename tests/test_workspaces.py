import json
import time
import unittest
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
    TelegramUser,
    Workspace,
    WorkspaceMember,
    RulePreset,
)
from tests.test_api import generate_valid_telegram_init_data


class TestWorkspaces(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        api_routes_module._summary_cache.clear()
        self.test_engine = create_async_engine('sqlite+aiosqlite:///:memory:', echo=False)
        self.test_session_maker = async_sessionmaker(self.test_engine, class_=AsyncSession, expire_on_commit=False)

        async with self.test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        api_routes_module.async_session_maker = self.test_session_maker
        api_auth_module.async_session_maker = self.test_session_maker
        api_server_module.async_session_maker = self.test_session_maker

        settings.BOT_TOKEN = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'
        settings.ADMIN_CHAT_ID = '8634201356'

        async with self.test_session_maker() as session:
            artem = TelegramUser(
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
                owner_id='777000111',
                owner_user_id=artem.id,
                timezone_name='UTC',
                currency='USD',
            )
            session.add(acc1)

            rule1 = RulePreset(
                workspace_id=ws1.id,
                owner_id='777000111',
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
                    owner_id='777000111',
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
