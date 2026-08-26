import logging
import unittest

from core.logging_config import RedactingFormatter, redact_secrets


class TestSecretRedaction(unittest.TestCase):
    def test_redacts_meta_access_token_in_url(self):
        message = (
            "GET https://graph.facebook.com/v20.0/act_1"
            "?fields=id&access_token=EAAB-test-secret&limit=100"
        )

        result = redact_secrets(message)

        self.assertNotIn("EAAB-test-secret", result)
        self.assertIn("access_token=[REDACTED]", result)
        self.assertIn("limit=100", result)

    def test_redacts_meta_appsecret_proof_in_url(self):
        proof = "a" * 64
        message = (
            "GET https://graph.facebook.com/v26.0/act_1"
            f"?access_token=token&appsecret_proof={proof}&limit=100"
        )

        result = redact_secrets(message)

        self.assertNotIn(proof, result)
        self.assertIn("appsecret_proof=[REDACTED]", result)
        self.assertIn("limit=100", result)

    def test_redacts_bearer_telegram_and_github_tokens(self):
        telegram_token = "1234567890:AAExampleTelegramSecret_123456789"
        github_token = "github_pat_example12345678901234567890"
        message = (
            f"Authorization: Bearer session-token.123 "
            f"bot={telegram_token} github={github_token}"
        )

        result = redact_secrets(message)

        self.assertNotIn("session-token.123", result)
        self.assertNotIn(telegram_token, result)
        self.assertNotIn(github_token, result)
        self.assertIn("Bearer [REDACTED]", result)
        self.assertIn("1234567890:[REDACTED]", result)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", result)

    def test_redacts_resend_api_keys_and_passwords(self):
        resend_key = "re_test12345678901234567890"
        message = f"resend_key={resend_key} connect url=postgresql://buyerly:password=secret_pw123@host:5432"

        result = redact_secrets(message)

        self.assertNotIn(resend_key, result)
        self.assertNotIn("secret_pw123", result)
        self.assertIn("[REDACTED_RESEND_KEY]", result)
        self.assertIn("password=[REDACTED]", result)

    def test_formatter_redacts_exception_text(self):
        formatter = RedactingFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed: access_token=secret-value",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)

        self.assertEqual(
            formatted,
            "ERROR request failed: access_token=[REDACTED]",
        )


if __name__ == "__main__":
    unittest.main()
