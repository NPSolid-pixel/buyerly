import hashlib
import unittest

from database.db import hash_password, password_needs_rehash, verify_password


class TestPasswordHashing(unittest.TestCase):
    def test_new_hashes_are_salted_and_verifiable(self):
        first = hash_password("correct horse battery staple")
        second = hash_password("correct horse battery staple")

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("pbkdf2_sha256$"))
        self.assertTrue(verify_password("correct horse battery staple", first))
        self.assertFalse(verify_password("wrong password", first))
        self.assertFalse(password_needs_rehash(first))

    def test_legacy_sha256_is_accepted_but_requires_upgrade(self):
        legacy = hashlib.sha256(b"legacy password").hexdigest()

        self.assertTrue(verify_password("legacy password", legacy))
        self.assertFalse(verify_password("wrong password", legacy))
        self.assertTrue(password_needs_rehash(legacy))

    def test_empty_and_malformed_hashes_never_authenticate(self):
        self.assertFalse(verify_password("anything", ""))
        self.assertFalse(verify_password("anything", "pbkdf2_sha256$invalid"))
        self.assertFalse(
            verify_password("anything", "pbkdf2_sha256$999999999$AA$AA")
        )


if __name__ == "__main__":
    unittest.main()
