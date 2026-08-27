# Implementation plan: native JSONB contract

## Goal

Store structured values in PostgreSQL JSONB columns as native arrays/objects,
while keeping readers compatible with legacy top-level JSON strings during the
rollout.

## Scope

1. Replace confirmed double-encoded writes in Meta OAuth, rule presets,
   monitoring runtime state, rule execution state, and undo state.
2. Keep readers tolerant of both native values and legacy JSON strings until
   the migration has completed everywhere.
3. Add an idempotent data migration for every structured JSONB column. Valid
   legacy strings are decoded only when their top-level type matches the column
   contract; malformed or wrong-type strings remain untouched and are counted
   for operator visibility.
4. Run the same migration from the transitional schema initializer and from a
   versioned Alembic revision.
5. Cover native writes, mixed-format reads, migration idempotency, malformed
   data detection, and Alembic head in GitHub Actions.

## Rollout and rollback

- Readers remain dual-format for one release, so code can roll back without
  re-encoding migrated rows.
- The data conversion is idempotent and does not delete malformed values.
- If the rollout must be reverted, deploy the previous application version;
  it already accepts native objects in the relevant readers. A database restore
  is needed only if operators explicitly require the old double-encoded storage
  representation.

## Verification

- Static compile and diff checks locally; no local test suite.
- Full PostgreSQL unit/integration suite in GitHub Actions.
- Production health endpoints must report the deployed commit after the main
  workflow succeeds.
