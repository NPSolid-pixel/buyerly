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
from database.models import Account, MetaConnection, User
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
            connection = MetaConnection(
                owner_user_id=user.id,
                provider_user_id="rotation-provider-user",
                access_token_encrypted=encrypt_meta_token("oauth-secret"),
                status="active",
            )
            account = Account(
                account_id="act_rotation_manual",
                name="Rotation manual",
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


if __name__ == "__main__":
    unittest.main()
