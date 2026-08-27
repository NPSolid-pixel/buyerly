# Implementation plan: restore frontend API scenarios

## Goal

Remove the three user-visible 404 paths while preserving workspace isolation,
role checks, auditability, and safe automation behavior.

## Scope

1. Add `DELETE /api/meta/connections/{connection_id}` with exact workspace
   scope, write-role and ownership checks. Detach linked accounts, disable their
   automation and polling, delete the encrypted connection, and record one
   immutable audit event.
2. Remove the unsupported manual worker-run control from the rule record page.
   A direct API-side worker execution would bypass the worker service's
   single-instance scheduler and is not safe to expose until a durable queue is
   introduced.
3. Change bulk preset deletion to canonical `DELETE /api/presets/{id}` calls
   and use independent settled results so one failure does not hide successful
   deletions or stop the remaining requests.
4. Record individual preset deletions in `AuditEvent`, including the number of
   affected account snapshots.

## Verification

- API tests cover owner success, Viewer denial, foreign workspace denial,
  linked-account safety, and audit records.
- Frontend contract tests require the canonical endpoint and partial-result
  behavior, and reject the unsupported worker route and legacy preset URL.
- All behavioral tests run in GitHub Actions; local validation is limited to
  static compilation and diff checks.

## Rollback

The endpoint and frontend changes can be reverted without data migration. A
deleted Meta connection is intentionally irreversible; linked accounts remain
in Buyerly, disabled and ready to be reconnected through the normal OAuth flow.
