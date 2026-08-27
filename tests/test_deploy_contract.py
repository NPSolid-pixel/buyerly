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
        cls.workflow = (project_root / ".github" / "workflows" / "deploy.yml").read_text()
        cls.codeowners = (project_root / ".github" / "CODEOWNERS").read_text()
        cls.alembic_env = (project_root / "alembic" / "env.py").read_text()

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

    def test_migration_lock_does_not_mask_the_primary_database_error(self):
        self.assertIn("pg_try_advisory_xact_lock", self.alembic_env)
        self.assertNotIn("pg_advisory_unlock", self.alembic_env)
        self.assertNotIn("docker compose logs --tail=120 migrate db", self.script)

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

    def test_meta_token_key_is_generated_and_validated_without_logging_it(self):
        self.assertIn("ensure_meta_token_encryption_key", self.script)
        self.assertIn("META_KEY_CANDIDATE", self.script)
        self.assertIn("len(decoded) == 32", self.script)
        self.assertNotIn('echo "${configured_key}"', self.script)

    def test_rollback_restores_the_previous_image_version(self):
        self.assertIn("PREVIOUS_APP_TAG", self.script)
        self.assertIn("PREVIOUS_WEB_TAG", self.script)
        self.assertIn('export APP_VERSION="${PREVIOUS_SHA}"', self.script)
        self.assertNotIn('export APP_VERSION="${CURRENT_SHA}"', self.script)

    def test_remote_deploy_failure_exposes_only_safe_stage_diagnostics(self):
        self.assertIn("appleboy/ssh-action@v1.2.2", self.workflow)
        self.assertIn("capture_stdout: true", self.workflow)
        self.assertIn("BUYERLY_DEPLOY_RESULT=success", self.workflow)
        self.assertIn("BUYERLY_DEPLOY_RESULT=failure", self.workflow)
        self.assertIn("Production deploy failure", self.workflow)
        self.assertIn("[REDACTED_FERNET_TOKEN]", self.workflow)
        self.assertIn("[REDACTED_META_TOKEN]", self.workflow)
        self.assertIn('File "/app/', self.workflow)
        self.assertIn("Running (upgrade|stamp)", self.workflow)
        self.assertIn("grep -Ev '(parameters:|UPDATE accounts|INSERT INTO)'", self.workflow)
        self.assertNotIn('cat "${deploy_log}"', self.workflow)

    def test_production_repository_owner_and_origin_are_fail_closed(self):
        self.assertIn("EXPECTED_GIT_REPOSITORY", self.script)
        self.assertIn("normalize_repository_ownership", self.script)
        self.assertIn("chown -R", self.script)
        self.assertIn("sudo -n", self.script)
        self.assertIn("git remote get-url origin", self.script)
        self.assertIn("NORMALIZED_ORIGIN", self.script)
        self.assertIn("@hiurano", self.codeowners)

    def test_production_build_context_matches_the_exact_git_revision(self):
        reset_position = self.script.index('git reset --hard "${TARGET_SHA}"')
        clean_position = self.script.index("git clean -ffd -q")
        build_position = self.script.index("docker compose build --pull api web")
        self.assertLess(reset_position, clean_position)
        self.assertLess(clean_position, build_position)
        self.assertIn("git status --short --untracked-files=all", self.script)
        self.assertIn("Production source tree does not match", self.script)

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
