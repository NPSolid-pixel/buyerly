import unittest

from cryptography.fernet import Fernet

from core.config import settings
from core.meta_tokens import MetaTokenError, decrypt_meta_token, encrypt_meta_token


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


if __name__ == "__main__":
    unittest.main()
