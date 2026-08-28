# 86eyr60pp — release smoke, synthetic checks, and incident runbooks

## Scope

- Add a mandatory, read-only post-deploy smoke gate for the production API,
  authentication boundary, workspace isolation, Meta configuration, summary
  storage, worker heartbeat, and Alembic/schema state.
- Persist one machine-readable result for every attempted release.
- Roll back the application release when any critical smoke check fails.
- Document first-response and recovery procedures for the critical services.

## Delivery

1. Run public live/readiness and exact-version checks through the local reverse
   proxy after cutover.
2. Confirm protected auth, summary, workspace, and Meta endpoints reject a
   request without a session.
3. Inspect versioned runtime images and worker heartbeat/day-boundary markers.
4. Run a read-only in-container contract check for Alembic head, model/schema
   parity, production Meta configuration, cross-workspace relational drift,
   and workspace-scoped summary snapshots.
5. Atomically save the complete result under `logs/smoke/` and print a safe
   summary into the GitHub Actions deployment log.
6. Invoke the existing rollback path on any critical failure and add incident
   runbooks for worker, database, Meta, token, and disk failures.

## Safety and rollback

- Smoke checks issue only GET requests and SELECT queries.
- The Meta check validates configuration presence without returning values and
  never calls the Marketing API or mutates campaign/ad-set budgets.
- Result files contain check names, timing, status, and bounded error text; no
  cookies, tokens, request headers, database URLs, or secrets are persisted.
- A failed smoke runs before artifact cleanup and triggers the same proven
  rollback function as health or migration failures.

## Verification

- Static Python/shell compilation and repository diff checks run locally.
- GitHub Actions runs the full unit/integration suite for the branch and PR.
- Production completion requires a green main deploy, a persisted successful
  smoke result, and exact `/health/live` and `/health/ready` release versions.
