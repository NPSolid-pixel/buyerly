# Implementation plan — account health and SLO/SLI (`86eyr60pw`)

## Goal

Give operators and workspace users one safe, durable view of Buyerly reliability and every Meta account's latest successful check or failure, with an explicit owner and response path for each SLO.

## Scope

1. Add a workspace-scoped, one-row-per-account health model and Alembic migration.
2. Record successful and failed worker checks, classify the source as `user`, `meta`, or `system`, redact secrets, and emit transition-only audit events.
3. Expose workspace-isolated overview and per-account health API responses, including worker lag, action error rate, quota, token, freshness, and backup-age signals.
4. Add the health overview to Settings and the latest health state to account details.
5. Document SLO targets, thresholds, owners, alert routes, and incident response expectations.
6. Add cloud test coverage for migration/model contracts, health classification, workspace isolation, API payloads, and frontend integration.

## Verification and release

- Run only local static validation (`py_compile`, JavaScript syntax check where available, migration/import checks, and diff review).
- Push the isolated branch and require green GitHub Actions.
- Merge only after green PR checks, then wait for automatic production deployment and verify exact deployed SHA via health endpoints.
- Update ClickUp with implementation, CI, PR, deployment, and production evidence.
