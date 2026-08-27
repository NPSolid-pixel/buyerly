import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def settings_field_names() -> set[str]:
    tree = ast.parse((ROOT / "core" / "config.py").read_text())
    settings_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    return {
        node.target.id
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id.isupper()
    }


def env_example_names() -> list[str]:
    return re.findall(
        r"^([A-Z][A-Z0-9_]*)=",
        (ROOT / ".env.example").read_text(),
        flags=re.MULTILINE,
    )


class TestConfigurationContract(unittest.TestCase):
    def test_env_example_covers_runtime_settings_without_stale_fields(self):
        runtime_names = settings_field_names()
        example_names = env_example_names()

        self.assertEqual(len(example_names), len(set(example_names)))
        self.assertEqual(runtime_names - set(example_names), set())
        self.assertEqual(set(example_names) - runtime_names, {"POSTGRES_PASSWORD"})
        self.assertFalse(any(name.startswith("SMTP_") for name in runtime_names))

    def test_production_docs_name_required_and_managed_configuration(self):
        deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text()
        documented_names = settings_field_names() | {"POSTGRES_PASSWORD"}
        for name in documented_names:
            self.assertRegex(
                deployment,
                re.compile(rf"(?:`{re.escape(name)}`|^{re.escape(name)}=)", re.MULTILINE),
            )

    def test_email_docs_match_resend_runtime_and_do_not_claim_otp_logging(self):
        email_docs = (ROOT / "docs" / "WORKSPACES_AUTH_AND_INVITES.md").read_text()
        self.assertNotIn("file:///", email_docs)
        self.assertIn("SMTP transport не поддерживается", email_docs)
        self.assertIn("тело письма и OTP-код не журналируются", email_docs)
        self.assertNotIn("OTP-код выводятся в стандартный лог", email_docs)
