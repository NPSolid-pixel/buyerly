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
        cls.dockerfile = (project_root / "Dockerfile").read_text()
        cls.codeowners = (project_root / ".github" / "CODEOWNERS").read_text()

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

    def test_deploy_waits_for_scheduler_cycle_and_rejects_owner_failures(self):
        self.assertIn("buyerly-worker-day-boundary-cycle-complete", self.script)
        self.assertIn("Failed to persist audit event", self.script)
        self.assertIn("NotNullViolation", self.script)

    def test_production_mini_app_has_https_url(self):
        self.assertIn(
            "WEBAPP_URL: ${WEBAPP_URL:-https://buyerly.app}",
            self.compose,
        )

    def test_user_uploads_are_durable_and_served_by_web(self):
        nginx = (Path(__file__).parents[1] / "webapp" / "nginx.conf").read_text()
        self.assertIn("buyerly-uploads:/app/webapp/uploads", self.compose)
        self.assertIn(
            "buyerly-uploads:/usr/share/nginx/html/uploads:ro",
            self.compose,
        )
        self.assertIn("preserve_legacy_uploads", self.script)
        self.assertIn("location /uploads/", nginx)
        self.assertIn('X-Content-Type-Options "nosniff"', nginx)

    def test_account_day_boundary_has_an_independent_minute_job(self):
        self.assertIn('id="account_day_boundary_job"', self.worker_service)
        self.assertIn("run_day_boundary_cycle", self.worker_service)

    def test_old_spend_started_notification_cannot_return(self):
        executable_contract = self.monitoring_worker + self.notifier
        self.assertNotIn('event_type="DAY_START"', executable_contract)
        self.assertNotIn("start_spend", executable_contract)
        self.assertNotIn("starts_notified", executable_contract)

    def test_no_hardcoded_secrets_in_deploy_script(self):
        import re
        self.assertIsNone(re.search(r"\bre_[A-Za-z0-9_]{10,}", self.script))
        self.assertIn("ensure_email_settings", self.script)

    def test_production_repository_owner_and_origin_are_fail_closed(self):
        self.assertIn("EXPECTED_GIT_REPOSITORY", self.script)
        self.assertIn("normalize_repository_ownership", self.script)
        self.assertIn("chown -R", self.script)
        self.assertIn("sudo -n", self.script)
        self.assertIn("git remote get-url origin", self.script)
        self.assertIn("NORMALIZED_ORIGIN", self.script)
        self.assertIn("@hiurano", self.codeowners)

    def test_legacy_monolith_cannot_return(self):
        self.assertNotIn("--profile legacy", self.script)
        self.assertNotIn("PREVIOUS_LEGACY_IMAGE", self.script)
        self.assertNotIn("buyerly-bot", self.script)

    def test_runtime_image_uses_explicit_production_sources(self):
        self.assertNotIn("COPY . .", self.dockerfile)
        for runtime_dir in (
            "alembic",
            "api",
            "bot",
            "core",
            "database",
            "meta_api",
            "rules",
            "scheduler",
            "services",
        ):
            self.assertIn(f"COPY {runtime_dir} ./{runtime_dir}", self.dockerfile)

    def test_repository_has_no_workstation_or_captured_design_artifacts(self):
        root = Path(__file__).parents[1]
        forbidden = (
            "main.py",
            "batch_transcribe.py",
            "transcribe.py",
            "scratch_active.py",
            "scratch_check.py",
            "client_responses.txt",
            "app.attio-structure-login-workspaces",
            "webapp/attio-reference.html",
            "webapp/prototype.html",
        )
        for relative_path in forbidden:
            self.assertFalse((root / relative_path).exists(), relative_path)



if __name__ == "__main__":
    unittest.main()
