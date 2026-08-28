import unittest

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import settings
from core.meta_tokens import (
    MetaTokenError,
    decrypt_meta_token,
    encrypt_meta_token,
    rotate_meta_token,
    rotate_stored_meta_tokens,
    resolve_account_access_token,
)
from database.models import Account, MetaConnection, User, Workspace, WorkspaceMember
from tests.test_db_helper import create_test_engine, init_test_db


class TestMetaTokenEncryption(unittest.TestCase):
    def setUp(self):
        self.original_key = settings.META_TOKEN_ENCRYPTION_KEY
        settings.META_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")

    def tearDown(self):
        settings.META_TOKEN_ENCRYPTION_KEY = self.original_key

    def test_token_round_trip_is_encrypted(self):
        encrypted = encrypt_meta_token("EAAB-secret-test-token")

        self.assertNotIn("EAAB-secret-test-token", encrypted)
        self.assertEqual(decrypt_meta_token(encrypted), "EAAB-secret-test-token")

    def test_invalid_ciphertext_fails_closed(self):
        with self.assertRaises(MetaTokenError):
            decrypt_meta_token("not-a-fernet-token")

    def test_rotation_decrypts_old_tokens_and_encrypts_with_new_key(self):
        old_key = settings.META_TOKEN_ENCRYPTION_KEY
        old_ciphertext = encrypt_meta_token("old-token")
        new_key = Fernet.generate_key().decode("ascii")
        settings.META_TOKEN_ENCRYPTION_KEY = f"{new_key},{old_key}"

        new_ciphertext = encrypt_meta_token("new-token")

        self.assertEqual(decrypt_meta_token(old_ciphertext), "old-token")
        self.assertEqual(decrypt_meta_token(new_ciphertext), "new-token")
        self.assertEqual(
            Fernet(new_key.encode("ascii")).decrypt(new_ciphertext.encode("ascii")),
            b"new-token",
        )

    def test_rotate_ciphertext_uses_primary_key(self):
        old_key = settings.META_TOKEN_ENCRYPTION_KEY
        old_ciphertext = encrypt_meta_token("rotate-me")
        new_key = Fernet.generate_key().decode("ascii")
        settings.META_TOKEN_ENCRYPTION_KEY = f"{new_key},{old_key}"

        rotated = rotate_meta_token(old_ciphertext)

        self.assertNotEqual(rotated, old_ciphertext)
        self.assertEqual(
            Fernet(new_key.encode("ascii")).decrypt(rotated.encode("ascii")),
            b"rotate-me",
        )


class TestStoredMetaTokens(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_key = settings.META_TOKEN_ENCRYPTION_KEY
        self.old_key = Fernet.generate_key().decode("ascii")
        settings.META_TOKEN_ENCRYPTION_KEY = self.old_key
        self.engine = create_test_engine()
        self.session_maker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        await init_test_db(self.engine)

    async def asyncTearDown(self):
        settings.META_TOKEN_ENCRYPTION_KEY = self.original_key
        await self.engine.dispose()

    async def test_resolves_encrypted_manual_token(self):
        raw_token = "EAAB-manual-secret"
        async with self.session_maker() as session:
            account = Account(
                account_id="act_manual_encrypted",
                name="Encrypted manual",
                access_token="",
                access_token_encrypted=encrypt_meta_token(raw_token),
            )
            session.add(account)
            await session.commit()

            self.assertEqual(
                await resolve_account_access_token(session, account),
                raw_token,
            )

    async def test_rotates_oauth_and_manual_tokens(self):
        async with self.session_maker() as session:
            user = User(username="token-rotation-user")
            session.add(user)
            await session.flush()
            ws = Workspace(
                name="Rotation WS",
                slug="rotation-ws",
                owner_user_id=user.id,
            )
            session.add(ws)
            await session.flush()
            connection = MetaConnection(
                workspace_id=ws.id,
                owner_user_id=user.id,
                provider_user_id="rotation-provider-user",
                access_token_encrypted=encrypt_meta_token("oauth-secret"),
                status="active",
            )
            account = Account(
                account_id="act_rotation_manual",
                name="Rotation manual",
                workspace_id=ws.id,
                owner_user_id=user.id,
                access_token="",
                access_token_encrypted=encrypt_meta_token("manual-secret"),
            )
            session.add_all([connection, account])
            await session.commit()

        new_key = Fernet.generate_key().decode("ascii")
        settings.META_TOKEN_ENCRYPTION_KEY = f"{new_key},{self.old_key}"
        async with self.session_maker() as session:
            stats = await rotate_stored_meta_tokens(session)
            self.assertEqual(
                stats,
                {"connections_rotated": 1, "accounts_rotated": 1},
            )

        async with self.session_maker() as session:
            connection = (
                await session.execute(select(MetaConnection))
            ).scalar_one()
            account = (await session.execute(select(Account))).scalar_one()
            primary = Fernet(new_key.encode("ascii"))
            self.assertEqual(
                primary.decrypt(connection.access_token_encrypted.encode("ascii")),
                b"oauth-secret",
            )
            self.assertEqual(
                primary.decrypt(account.access_token_encrypted.encode("ascii")),
                b"manual-secret",
            )

    async def test_resolve_account_token_workspace_mismatch_fails(self):
        async with self.session_maker() as session:
            user = User(username="ws-user-1")
            session.add(user)
            await session.flush()
            ws1 = Workspace(name="WS 1", slug="ws-1", owner_user_id=user.id)
            ws2 = Workspace(name="WS 2", slug="ws-2", owner_user_id=user.id)
            session.add_all([ws1, ws2])
            await session.flush()

            conn = MetaConnection(
                workspace_id=ws1.id,
                owner_user_id=user.id,
                provider_user_id="prov-ws-1",
                access_token_encrypted=encrypt_meta_token("token-ws-1"),
                status="active",
            )
            session.add(conn)
            await session.flush()

            account_mismatch = Account(
                account_id="act_ws_mismatch",
                name="Mismatch Account",
                workspace_id=ws2.id,
                owner_user_id=user.id,
                meta_connection_id=conn.id,
                access_token="",
            )
            session.add(account_mismatch)
            await session.commit()

            with self.assertRaises(MetaTokenError) as ctx:
                await resolve_account_access_token(session, account_mismatch)
            self.assertIn("workspace mismatch", str(ctx.exception))

    async def test_resolve_account_token_same_workspace_shared_connection_succeeds(self):
        async with self.session_maker() as session:
            buyer1 = User(username="buyer-1")
            buyer2 = User(username="buyer-2")
            session.add_all([buyer1, buyer2])
            await session.flush()

            team_ws = Workspace(name="Team WS", slug="team-ws", owner_user_id=buyer1.id)
            session.add(team_ws)
            await session.flush()

            conn = MetaConnection(
                workspace_id=team_ws.id,
                owner_user_id=buyer1.id,
                provider_user_id="prov-team-1",
                access_token_encrypted=encrypt_meta_token("shared-team-token"),
                status="active",
            )
            session.add(conn)
            await session.flush()

            # Account owned by buyer2 in the same workspace using buyer1's connection
            account_shared = Account(
                account_id="act_shared_team",
                name="Team Account",
                workspace_id=team_ws.id,
                owner_user_id=buyer2.id,
                meta_connection_id=conn.id,
                access_token="",
            )
            session.add(account_shared)
            await session.commit()

            token = await resolve_account_access_token(session, account_shared)
            self.assertEqual(token, "shared-team-token")


if __name__ == "__main__":
    unittest.main()
