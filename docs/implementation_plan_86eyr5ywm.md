# Implementation plan: workspace-scoped rules

## Goal

Make presets, rule groups, group membership, account assignments, and worker
execution strictly belong to the active account workspace.

## Scope

1. Replace owner-or-legacy fallbacks in every rules endpoint with exact active
   `workspace_id` predicates and enforce write roles for reorder as well as CRUD.
2. Require presets in a group and presets assigned to an account to belong to
   the same workspace as their target.
3. Stamp rule snapshots with `workspace_id`; the worker rejects missing or
   mismatched snapshots before scheduling or contacting Meta.
4. Backfill legacy rule/group/example rows from a valid active membership,
   quarantine remaining NULL rows, remove cross-workspace group items, and
   disable unsafe account snapshots.
5. Add a PostgreSQL trigger as a database-level guard against cross-workspace
   `RuleGroupItem` inserts.
6. Scope example bootstrap markers per workspace so one owner can use multiple
   independent rule libraries.

## Rollout and rollback

- Exact-scope readers hide unresolved legacy rows instead of guessing tenant
  ownership.
- The migration is idempotent; unsafe snapshots are disabled, not deleted.
- Rollback removes the database trigger and collapses duplicate example markers
  before restoring the former owner-only uniqueness contract. Backfilled
  workspace IDs remain safe additional data.

## Verification

- PostgreSQL CI matrix: two workspaces for one owner, viewer versus write roles,
  CRUD/reorder/assign/detach/toggle/bulk access, legacy NULL rows, cross links,
  and worker filtering.
- Static checks locally; the test suite runs only in GitHub Actions.
- Production health must report the merged commit after deployment.
