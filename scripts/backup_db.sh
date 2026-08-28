#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/buyerly/backups}"
DATA_DIR="${DATA_DIR:-/opt/buyerly/data}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-buyerly-db}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
KEEP_BACKUPS="${KEEP_BACKUPS:-30}"

mkdir -p "${BACKUP_DIR}"

postgres_state=$(docker inspect -f '{{.State.Status}}' "${POSTGRES_CONTAINER}" 2>/dev/null || true)
if [[ "${postgres_state}" != "running" ]]; then
    echo "[INFO] PostgreSQL container '${POSTGRES_CONTAINER}' is not running (state: '${postgres_state:-missing}'). Skipping backup."
    exit 0
fi

backup_file="${BACKUP_DIR}/buyerly_postgres_${TIMESTAMP}.sql"
echo "[INFO] Creating PostgreSQL backup: ${backup_file}.gz"
docker exec "${POSTGRES_CONTAINER}" pg_dump \
    --username=buyerly \
    --dbname=buyerly \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges > "${backup_file}"
test -s "${backup_file}"
gzip -f "${backup_file}"
gzip -t "${backup_file}.gz"
backup_completed_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
docker exec "${POSTGRES_CONTAINER}" psql \
    --username=buyerly \
    --dbname=buyerly \
    --set=ON_ERROR_STOP=1 \
    --command="INSERT INTO automation_runtime_states (state_key, payload, updated_at) VALUES ('monitoring', jsonb_build_object('last_backup_at', '${backup_completed_at}'), NOW()) ON CONFLICT (state_key) DO UPDATE SET payload = COALESCE(automation_runtime_states.payload, '{}'::jsonb) || EXCLUDED.payload, updated_at = NOW();"
pattern="buyerly_postgres_*.sql.gz"

ls -tp "${BACKUP_DIR}"/${pattern} 2>/dev/null \
    | grep -v '/$' \
    | tail -n +$((KEEP_BACKUPS + 1)) \
    | xargs -r rm -f --
echo "[SUCCESS] Database backup created and verified."
