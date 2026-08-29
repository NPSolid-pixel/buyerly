#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/opt/buyerly/backups}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-buyerly-db}"
POSTGRES_USER="${POSTGRES_USER:-buyerly}"
DRILL_DB="${DRILL_DB:-buyerly_restore_drill}"
BACKUP_FILE="${1:-}"

# Fail-Safe Gate: Never allow running drill against production database
if [[ "${DRILL_DB}" == "buyerly" ]]; then
    echo "[FATAL] drill_restore.sh is strictly forbidden from using production database name 'buyerly'."
    exit 1
fi

echo "=================================================="
echo " 🛡 Buyerly Automated PostgreSQL Restore Drill"
echo " Container:      ${POSTGRES_CONTAINER}"
echo " Ephemeral DB:   ${DRILL_DB}"
echo "=================================================="

# Check PostgreSQL container
postgres_state=$(docker inspect -f '{{.State.Status}}' "${POSTGRES_CONTAINER}" 2>/dev/null || true)
if [[ "${postgres_state}" != "running" ]]; then
    echo "[ERROR] PostgreSQL container '${POSTGRES_CONTAINER}' is not running (state: '${postgres_state:-missing}')."
    exit 1
fi

# Locate backup file if not explicitly provided
if [[ -z "${BACKUP_FILE}" ]]; then
    BACKUP_FILE=$(ls -t "${BACKUP_DIR}"/buyerly_postgres_*.sql* 2>/dev/null | head -n 1 || true)
    if [[ -z "${BACKUP_FILE}" ]]; then
        echo "[ERROR] No backup file found for restore drill."
        exit 1
    fi
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "[ERROR] Specified backup file does not exist: ${BACKUP_FILE}"
    exit 1
fi

echo "[INFO] Using backup file: ${BACKUP_FILE}"

cleanup() {
    echo "[INFO] Cleaning up ephemeral test database '${DRILL_DB}'..."
    docker exec "${POSTGRES_CONTAINER}" psql \
        --username="${POSTGRES_USER}" \
        --dbname="postgres" \
        --command="DROP DATABASE IF EXISTS ${DRILL_DB} WITH (FORCE);" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[INFO] Creating ephemeral sandbox database '${DRILL_DB}'..."
docker exec "${POSTGRES_CONTAINER}" psql \
    --username="${POSTGRES_USER}" \
    --dbname="postgres" \
    --command="DROP DATABASE IF EXISTS ${DRILL_DB} WITH (FORCE);"
docker exec "${POSTGRES_CONTAINER}" psql \
    --username="${POSTGRES_USER}" \
    --dbname="postgres" \
    --command="CREATE DATABASE ${DRILL_DB};"

echo "[INFO] Executing restore into sandbox database..."
POSTGRES_CONTAINER="${POSTGRES_CONTAINER}" \
POSTGRES_DB="${DRILL_DB}" \
POSTGRES_USER="${POSTGRES_USER}" \
bash "${SCRIPT_DIR}/restore_db.sh" --file "${BACKUP_FILE}" --target-container "${POSTGRES_CONTAINER}" --target-db "${DRILL_DB}" --target-user "${POSTGRES_USER}" --yes

echo "[INFO] Running schema and integrity assertions..."

# 1. Check essential tables
required_tables=("accounts" "rule_presets" "rule_groups" "automation_runtime_states" "audit_events" "analytics_entity_daily_facts" "users" "workspaces" "alembic_version")
for table in "${required_tables[@]}"; do
    exists=$(docker exec "${POSTGRES_CONTAINER}" psql \
        --username="${POSTGRES_USER}" \
        --dbname="${DRILL_DB}" \
        --tuples-only \
        --no-align \
        --command="SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = '${table}';")
    if [[ "${exists}" != "1" ]]; then
        echo "[ERROR] Sanity check failed: required table '${table}' is missing after restore."
        exit 1
    fi
done

# 2. Check Alembic version presence
alembic_rev=$(docker exec "${POSTGRES_CONTAINER}" psql \
    --username="${POSTGRES_USER}" \
    --dbname="${DRILL_DB}" \
    --tuples-only \
    --no-align \
    --command="SELECT version_num FROM alembic_version LIMIT 1;")
if [[ -z "${alembic_rev}" ]]; then
    echo "[ERROR] Sanity check failed: alembic_version table is empty."
    exit 1
fi
echo "[INFO] Restored database Alembic revision: ${alembic_rev}"

# 3. Check JSONB query functionality
docker exec "${POSTGRES_CONTAINER}" psql \
    --username="${POSTGRES_USER}" \
    --dbname="${DRILL_DB}" \
    --command="SELECT count(*) FROM automation_runtime_states WHERE jsonb_typeof(payload) = 'object';" >/dev/null

echo "=================================================="
echo " [SUCCESS] Restore drill completed successfully!"
echo " Backup integrity and schema invariants verified."
echo "=================================================="
