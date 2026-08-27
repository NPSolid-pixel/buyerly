# 86eyr5qbd — encrypted manual Meta tokens

## Scope

- Store manual System User tokens as Fernet ciphertext, just like OAuth tokens.
- Migrate every existing non-empty plaintext account token without data loss.
- Keep key rotation available for both token sources.

## Delivery

1. Add `accounts.access_token_encrypted` in Alembic revision
   `0013_manual_tokens` and clear migrated plaintext in the same transaction.
2. Encrypt every new Web API and Telegram manual import before persistence.
3. Resolve encrypted manual tokens first, retaining a read-only compatibility
   fallback for a row not yet reached by the migration.
4. Rotate OAuth connections and manual account ciphertext with
   `python -m scripts.rotate_meta_tokens` after prepending a new primary key.
5. Generate a missing production Fernet key before migration, validate an
   existing primary key without exposing it, and retain the previous image SHA
   for truthful rollback health reporting.

## Safety and rollback

- A migration that finds plaintext fails closed when the Fernet key is missing
  or invalid; the migration transaction leaves the prior schema and data intact.
- Downgrade restores plaintext before dropping the encrypted column, so schema
  rollback does not silently discard credentials.
- Neither application responses nor operational logs contain token values.

## Verification

- Static Python compilation and repository diff checks pass locally.
- GitHub Actions must validate encryption round trips, runtime resolution,
  MultiFernet rotation, Alembic upgrade/downgrade, API persistence, and the full
  regression suite before merge.
- Production readiness must report the exact merged commit after deployment.
