from pathlib import Path
import unittest


class TestDeployContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (
            Path(__file__).parents[1] / "scripts" / "deploy.sh"
        ).read_text()

    def test_deployments_are_serialized(self):
        self.assertIn("DEPLOY_LOCK_FILE", self.script)
        self.assertIn("flock -w", self.script)

    def test_same_healthy_commit_is_not_recreated(self):
        self.assertIn("CURRENT_REPO_SHA", self.script)
        self.assertIn("buyerly-app:${EXPECTED_SHA}", self.script)
        self.assertIn("is already deployed and healthy", self.script)


if __name__ == "__main__":
    unittest.main()
