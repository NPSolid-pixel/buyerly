# Buyerly incident runbooks

These procedures cover the first response to production alarms. Preserve the
failing release SHA, smoke result, and UTC timestamps before changing state.
Never paste secrets, tokens, cookies, database URLs, or raw `.env` content into
GitHub, ClickUp, or chat.

## Worker heartbeat or scheduler cycle

**Signal:** unhealthy `buyerly-worker`, heartbeat older than 45 seconds, or the
day-boundary marker is absent after deploy.

1. Confirm API and database readiness; a dependency outage can stall the worker.
2. Inspect bounded logs with `docker compose logs --tail=160 worker` and record
   only redacted exception type, timestamp, and affected account ID.
3. Restart only the worker with `docker compose up -d --no-deps worker`.
4. Require a fresh heartbeat and one complete day-boundary cycle before closing.
5. If the release introduced the failure, redeploy the previous known-good SHA.

## Database or migration

**Signal:** `/health/ready` is 503, migration container fails, or the smoke
result reports a revision/schema mismatch.

1. Stop the release; do not run manual `ALTER TABLE`, `create_all()`, or stamp an
   unknown schema.
2. Preserve the deploy log and verify the mandatory pre-deploy backup exists.
3. Inspect only the migration and PostgreSQL tail logs; redact parameters and
   data values.
4. Roll back application images. Schema recovery must use an audited Alembic
   revision or the documented database restore procedure.
5. Verify Alembic head, schema contract, `/health/ready`, and a successful smoke.

## Meta API outage or quota

**Signal:** increased Meta request failures/quota errors while Buyerly API and
database remain healthy.

1. Disable or pause mutating automation for affected accounts; do not retry in a
   tight loop.
2. Separate a Meta platform/quota response from Buyerly network or token errors.
3. Preserve Graph error code, request timestamp, account ID, and retry headers;
   never preserve access tokens.
4. Keep dashboards on the latest cached snapshot and communicate its age.
5. Resume with a small read-only validation, then re-enable automation gradually.

## Token expiry, revocation, or encryption key

**Signal:** `needs_reconnect`, missing scopes, token expiry, or decryption error.

1. Do not print or copy token values. Confirm the connection/workspace IDs and
   health status only.
2. For expiry/revocation, use the supported reconnect flow and verify identity
   mismatch protection before restoring automation.
3. For key rotation, prepend the new Fernet key, keep old decrypt-only keys,
   run `docker compose exec api python -m scripts.rotate_meta_tokens`, and remove
   old keys only after success.
4. A missing/invalid production key blocks migration and deploy; restore the
   correct secret through the secret manager rather than generating over data.

## Disk capacity

**Signal:** 75% warning or 90% critical threshold.

1. Run `DRY_RUN=true bash scripts/cleanup_docker_artifacts.sh` and review the
   explicit Buyerly image candidates.
2. Run the normal cleanup and `bash scripts/check_disk_usage.sh`.
3. Never use `docker system prune`, `docker image prune -a`, or volume pruning.
4. Confirm current containers and two complete rollback releases remain.
5. If usage remains critical, stop deploy and locate growth with `docker system
   df` and targeted `du`; expand storage or archive logs/backups deliberately.

## Release smoke failure and rollback gate

Every attempted release writes
`/opt/buyerly/logs/smoke/post-deploy-<full-sha>.json`. A failed critical check
returns a non-zero exit code and `scripts/deploy.sh` restores the previous app
and web images. The result is read-only and records zero Meta budget mutations.

After rollback, verify the previous SHA through `/health/live` and
`/health/ready`, confirm worker heartbeat, and keep the failed smoke JSON for the
incident timeline. Do not mark the release successful until a new SHA produces
a fully successful result.
