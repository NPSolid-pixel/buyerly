# Buyerly reliability SLO and account health

This document is the operating contract for Buyerly production. All windows use UTC. Account-health records and API responses must never contain access tokens, OAuth codes, cookies, passwords, or encryption keys.

## Service objectives

| SLI | Target | Warning | Critical | Owner | Evidence |
|---|---:|---:|---:|---|---|
| API availability | 99.9% over 30 days | below 99.95% | below 99.9% | Platform owner | external `/health/live` and `/health/ready` synthetic checks |
| API latency | p95 below 500 ms over 24 hours | 400 ms | 500 ms | Backend owner | synthetic timing and API telemetry |
| Worker cycle lag | cycle completes within 3 min of schedule | 180 s | 360 s | Automation owner | `automation_runtime_states.finished_at` |
| Rule-action error rate | below 2% over 24 hours | 2% | 5% | Automation owner | workspace `RULE_ACTION` audit events |
| Meta quota | below the configured soft limit | 60% | 80% | Meta integration owner | latest Meta usage headers stored in worker runtime |
| Token health | all active connections usable | expiry within 7 days or reconnect required | expired/missing scopes | Workspace admin | workspace Meta connection status |
| Account data freshness | successful Meta read within 20 min | 20 min | 45 min | Account owner | `account_health.last_success_at` |
| Verified backup age | newer than 26 hours | 26 h | 48 h | Platform owner | deploy/backup verifier runtime signal |

The API availability and latency fields returned by `/api/health/overview` are targets. Their measured values come from the external production synthetic monitor; they must not be fabricated from in-process requests. The verified backup script publishes `last_backup_at`; the API derives `backup_age_hours` from that timestamp. A missing timestamp is treated as unknown rather than healthy.

## Account health states

- `healthy`: the latest scheduled Meta read succeeded; failure counter is reset.
- `degraded`: one or two consecutive non-user failures occurred; automation remains observable while the owner investigates.
- `critical`: three consecutive failures occurred, or immediate user action is required for a token, permission, payment, or disabled-account problem.
- `unknown`: no worker health check has completed since the health model was introduced.

Every failure is attributed to one domain:

- `user`: token, permission, scope, reconnect, payment, or disabled account;
- `meta`: Meta/Graph outage, rate limiting, or quota pressure;
- `system`: Buyerly code, database, network, configuration, or workspace-isolation failure.

Only a status/cause transition creates an `ACCOUNT_HEALTH` audit event and Telegram notification. Repeated identical failures update the durable counter without alert spam. Recovery also creates one audit event and one owner/admin notification.

## Alert routing and response

Account transitions route to the account owner's Telegram ID, falling back to the configured admin chat, and are always retained in the workspace Audit Log independently of notification delivery. Release, API, database, worker-wide, disk, and backup alerts route through GitHub Actions/deploy output to the platform owner, who follows the relevant incident runbook.

Response expectations:

- critical: acknowledge within 15 minutes, stop unsafe automation if data is stale, and start an incident timeline;
- warning/degraded: triage within one business hour and assign an owner;
- recovery: verify one subsequent healthy cycle before closing the incident.

## Safe API surfaces

- `GET /api/health/overview` returns only the signed-in user's active workspace, aggregate SLI values, alert routes, and safe per-account health.
- `GET /api/accounts/{account_id}/health` requires the account to belong to that same active workspace.
- `GET /api/accounts` embeds the latest safe health object for the account details view.

Error messages are redacted and truncated before storage. The health schema intentionally has no token, cookie, authorization header, or raw Meta response fields.
