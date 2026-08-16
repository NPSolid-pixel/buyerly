#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/buyerly/backups}"
DATA_DIR="${DATA_DIR:-/opt/buyerly/data}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-buyerly-db}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
KEEP_BACKUPS="${KEEP_BACKUPS:-30}"

mkdir -p "${BACKUP_DIR}"

postgres_state=$(docker inspect --format '{{.State.Status}}' "${POSTGRES_CONTAINER}" 2>/dev/null || true)
if [[ "${postgres_state}" == "running" ]]; then
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
    pattern="buyerly_postgres_*.sql.gz"
else
    db_file="${DATA_DIR}/mediabuyer.db"
    backup_file="${BACKUP_DIR}/mediabuyer_${TIMESTAMP}.db"
    if [[ ! -f "${db_file}" ]]; then
        echo "[ERROR] Neither a running PostgreSQL service nor ${db_file} was found."
        exit 1
    fi

    echo "[INFO] Creating SQLite backup: ${backup_file}.gz"
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "${db_file}" ".backup '${backup_file}'"
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "${db_file}" "${backup_file}" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1], timeout=30)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
    else
        echo "[ERROR] Neither sqlite3 nor python3 is available for a consistent backup."
        exit 1
    fi
    gzip -f "${backup_file}"
    gzip -t "${backup_file}.gz"
    pattern="mediabuyer_*.db.gz"
fi

ls -tp "${BACKUP_DIR}"/${pattern} 2>/dev/null \
    | grep -v '/$' \
    | tail -n +$((KEEP_BACKUPS + 1)) \
    | xargs -r rm -f --
echo "[SUCCESS] Database backup created and verified."
