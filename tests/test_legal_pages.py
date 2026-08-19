from pathlib import Path
import unittest

import httpx

from api.server import create_app
from core.config import settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestLegalPageFiles(unittest.TestCase):
    def test_required_meta_documents_are_public_build_assets(self):
        dockerfile = (PROJECT_ROOT / "webapp" / "Dockerfile").read_text(encoding="utf-8")
        nginx = (PROJECT_ROOT / "webapp" / "nginx.conf").read_text(encoding="utf-8")
        index = (PROJECT_ROOT / "webapp" / "index.html").read_text(encoding="utf-8")

        self.assertIn("privacy.html terms.html data-deletion.html", dockerfile)
        self.assertIn("^/(privacy|terms|data-deletion)/?$", nginx)
        for route in ("/privacy", "/terms", "/data-deletion"):
            self.assertIn(f'href="{route}"', index)
        self.assertIn("Individual Entrepreneur Artem Petruchenko", index)
        self.assertIn("305879234", index)
        self.assertIn("contact@buyerly.app", index)

    def test_documents_identify_purpose_contact_and_deletion_process(self):
        documents = {
            "privacy.html": ("Privacy Policy", "Information we do not collect", "Meta Platform data"),
            "terms.html": ("Terms of Service", "Authority to connect assets", "Responsible automation"),
            "data-deletion.html": ("Data Deletion Instructions", "Send a deletion request", "What we delete"),
        }

        for filename, required_text in documents.items():
            content = (PROJECT_ROOT / "webapp" / filename).read_text(encoding="utf-8")
            for text in required_text:
                self.assertIn(text, content)
            self.assertIn("contact@buyerly.app", content)
            self.assertNotIn("fonts.googleapis.com", content)


class TestLegalPageRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_documents_are_available_without_authentication(self):
        previous = settings.SERVE_STATIC
        settings.SERVE_STATIC = True
        try:
            app = create_app()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                for route, heading in (
                    ("/privacy", "Privacy Policy"),
                    ("/terms", "Terms of Service"),
                    ("/data-deletion", "Data Deletion Instructions"),
                ):
                    response = await client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("text/html", response.headers["content-type"])
                    self.assertEqual(response.headers["cache-control"], "public, max-age=300")
                    self.assertIn(heading, response.text)
        finally:
            settings.SERVE_STATIC = previous


if __name__ == "__main__":
    unittest.main()
