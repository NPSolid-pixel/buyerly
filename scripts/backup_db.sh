#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/opt/buyerly/backups}"
DATA_DIR="${DATA_DIR:-/opt/buyerly/data}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-buyerly-db}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
KEEP_BACKUPS="${KEEP_BACKUPS:-30}"
OFFSITE_RETENTION_DAYS="${OFFSITE_RETENTION_DAYS:-60}"
BACKUP_LOCK_FILE="${BACKUP_LOCK_FILE:-/tmp/buyerly-backup.lock}"

# Concurrency lock to prevent race conditions
exec 9>"${BACKUP_LOCK_FILE}"
if ! flock -n 9; then
    echo "[WARNING] Another backup process is currently running. Skipping."
    exit 0
fi

mkdir -p "${BACKUP_DIR}"

postgres_state=$(docker inspect -f '{{.State.Status}}' "${POSTGRES_CONTAINER}" 2>/dev/null || true)
if [[ "${postgres_state}" != "running" ]]; then
    echo "[INFO] PostgreSQL container '${POSTGRES_CONTAINER}' is not running (state: '${postgres_state:-missing}'). Skipping backup."
    exit 0
fi

if [[ -n "${BACKUP_ENCRYPTION_KEY:-}" ]]; then
    target_file="${BACKUP_DIR}/buyerly_postgres_${TIMESTAMP}.sql.gz.enc"
    echo "[INFO] Creating encrypted PostgreSQL backup: ${target_file}"
    docker exec "${POSTGRES_CONTAINER}" pg_dump \
        --username=buyerly \
        --dbname=buyerly \
        --clean \
        --if-exists \
        --no-owner \
        --no-privileges \
        | gzip -c \
        | openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -pass "env:BACKUP_ENCRYPTION_KEY" > "${target_file}"
else
    target_file="${BACKUP_DIR}/buyerly_postgres_${TIMESTAMP}.sql.gz"
    echo "[INFO] Creating PostgreSQL backup: ${target_file}"
    docker exec "${POSTGRES_CONTAINER}" pg_dump \
        --username=buyerly \
        --dbname=buyerly \
        --clean \
        --if-exists \
        --no-owner \
        --no-privileges \
        | gzip -c > "${target_file}"
    gzip -t "${target_file}"
fi

test -s "${target_file}"
backup_completed_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
if docker exec "${POSTGRES_CONTAINER}" psql \
        --username=buyerly \
        --dbname=buyerly \
        --set=ON_ERROR_STOP=1 \
        --command="INSERT INTO automation_runtime_states (state_key, payload, updated_at) VALUES ('monitoring', jsonb_build_object('last_backup_at', '${backup_completed_at}'), NOW()) ON CONFLICT (state_key) DO UPDATE SET payload = COALESCE(automation_runtime_states.payload, '{}'::jsonb) || EXCLUDED.payload, updated_at = NOW();"; then
    echo "[INFO] Published verified backup timestamp to runtime health state."
else
    # A verified dump must remain usable even when the pre-migration schema
    # cannot yet accept optional reliability telemetry. The next successful
    # backup republishes the timestamp after migrations have caught up.
    echo "[WARNING] Backup is valid, but runtime timestamp publication failed."
fi

# Local backup rotation
pattern="buyerly_postgres_*.sql.gz*"
ls -tp "${BACKUP_DIR}"/${pattern} 2>/dev/null \
    | grep -v '/$' \
    | tail -n +$((KEEP_BACKUPS + 1)) \
    | xargs -r rm -f --

# Off-site S3 synchronization if configured
if [[ -n "${S3_BUCKET:-}" && -n "${S3_ACCESS_KEY_ID:-}" && -n "${S3_SECRET_ACCESS_KEY:-}" ]]; then
    echo "[INFO] Triggering off-site S3 backup sync..."
    if python3 "${SCRIPT_DIR}/offsite_sync.py" --upload "${target_file}" --prune --retention-days "${OFFSITE_RETENTION_DAYS}"; then
        echo "[SUCCESS] Off-site S3 backup sync completed."
    else
        echo "[WARNING] Off-site S3 sync failed, but local backup is verified and intact."
    fi
fi

echo "[SUCCESS] Database backup created and verified: ${target_file}"
