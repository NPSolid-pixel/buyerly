import unittest
from pathlib import Path

from api.server import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestDocumentationContract(unittest.TestCase):
    def test_api_reference_lists_every_public_http_operation(self):
        api_reference = (PROJECT_ROOT / "docs" / "API.md").read_text(encoding="utf-8")
        schema = create_app().openapi()

        missing = []
        for path, operations in schema["paths"].items():
            if not path.startswith("/api/"):
                continue
            for method in operations:
                if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                signature = f"{method.upper()} {path}"
                if signature not in api_reference:
                    missing.append(signature)

        self.assertEqual(missing, [], f"API.md is missing operations: {missing}")

    def test_readme_links_the_supported_document_set(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for path in (
            "docs/DESIGN_SYSTEM.md",
            "docs/ARCHITECTURE.md",
            "docs/API.md",
            "docs/DEPLOYMENT.md",
            "docs/DECISIONS.md",
            "docs/PRODUCT_BACKLOG.md",
            "docs/FACEBOOK_AUTHORIZATION_PLAN.md",
            "docs/REMAINING_PRODUCT_WORK.md",
        ):
            self.assertIn(path, readme)

    def test_ux_baseline_covers_routes_states_and_pilots(self):
        baseline = (
            PROJECT_ROOT / "docs" / "UX_BASELINE_2026-08-29.md"
        ).read_text(encoding="utf-8")

        for contract in (
            "/sign-in",
            "/facebook-accounts",
            "/accounts",
            "/rules",
            "/summary",
            "/logs",
            "/settings",
            "Loading",
            "Empty",
            "Populated",
            "Error",
            "Partial",
            "Long content",
            "Permission denied",
            "### Today",
            "### Automations",
            "### Connections",
        ):
            self.assertIn(contract, baseline)

    def test_design_system_documents_tokens_components_and_migration(self):
        design_system = (PROJECT_ROOT / "docs" / "DESIGN_SYSTEM.md").read_text(
            encoding="utf-8"
        )
        for contract in (
            "## Tokens",
            "## Components",
            "## Pilot screens",
            "## Migration map",
            "Button",
            "IconButton",
            "EmptyState",
            "Skeleton",
            "### Today",
            "### Automations",
            "### Connections",
        ):
            self.assertIn(contract, design_system)


if __name__ == "__main__":
    unittest.main()
