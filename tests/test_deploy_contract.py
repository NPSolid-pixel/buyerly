from pathlib import Path
import unittest


class TestDeployContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).parents[1]
        cls.script = (project_root / "scripts" / "deploy.sh").read_text()
        cls.compose = (project_root / "docker-compose.yml").read_text()
        cls.worker_service = (project_root / "services" / "worker.py").read_text()
        cls.monitoring_worker = (project_root / "scheduler" / "worker.py").read_text()
        cls.notifier = (project_root / "bot" / "notifier.py").read_text()

    def test_deployments_are_serialized(self):
        self.assertIn("DEPLOY_LOCK_FILE", self.script)
        self.assertIn("flock -w", self.script)

    def test_same_healthy_commit_is_not_recreated(self):
        self.assertIn("CURRENT_REPO_SHA", self.script)
        self.assertIn("buyerly-app:${EXPECTED_SHA}", self.script)
        self.assertIn("is already deployed and healthy", self.script)

    def test_production_roles_are_separate_services(self):
        for service in ("db:", "api:", "web:", "bot:", "worker:", "migrate:"):
            self.assertIn(f"  {service}", self.compose)
        self.assertIn("postgres:16-alpine", self.compose)
        self.assertIn('command: ["python", "-m", "services.api"]', self.compose)
        self.assertIn('command: ["python", "-m", "services.bot"]', self.compose)
        self.assertIn('command: ["python", "-m", "services.worker"]', self.compose)

    def test_cutover_has_migration_healthcheck_and_rollback(self):
        self.assertIn("docker compose run --rm migrate", self.script)
        self.assertIn("wait_for_container buyerly-api", self.script)
        self.assertIn("wait_for_container buyerly-telegram-bot", self.script)
        self.assertIn("wait_for_container buyerly-worker", self.script)
        self.assertIn("wait_for_container buyerly-web", self.script)
        self.assertIn("rollback", self.script)

    def test_production_mini_app_has_https_url(self):
        self.assertIn(
            "WEBAPP_URL: ${WEBAPP_URL:-https://smattrades.com}",
            self.compose,
        )

    def test_account_day_boundary_has_an_independent_minute_job(self):
        self.assertIn('id="account_day_boundary_job"', self.worker_service)
        self.assertIn("run_day_boundary_cycle", self.worker_service)

    def test_old_spend_started_notification_cannot_return(self):
        executable_contract = self.monitoring_worker + self.notifier
        self.assertNotIn('event_type="DAY_START"', executable_contract)
        self.assertNotIn("start_spend", executable_contract)
        self.assertNotIn("starts_notified", executable_contract)


if __name__ == "__main__":
    unittest.main()
