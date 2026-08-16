#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Buyerly — Резервное копирование базы данных SQLite
# ==============================================================================

BACKUP_DIR="${BACKUP_DIR:-/opt/buyerly/backups}"
DATA_DIR="${DATA_DIR:-/opt/buyerly/data}"
DB_FILE="${DATA_DIR}/mediabuyer.db"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/mediabuyer_${TIMESTAMP}.db"
KEEP_BACKUPS="${KEEP_BACKUPS:-30}"

mkdir -p "${BACKUP_DIR}"

if [[ ! -f "${DB_FILE}" ]]; then
    echo "[ERROR] Database file ${DB_FILE} not found. Deployment must stop."
    exit 1
fi

echo "[INFO] Creating database backup: ${BACKUP_FILE}"
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "${DB_FILE}" ".backup '${BACKUP_FILE}'"
elif command -v python3 >/dev/null 2>&1; then
    python3 - "${DB_FILE}" "${BACKUP_FILE}" <<'PY'
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

gzip -f "${BACKUP_FILE}"
gzip -t "${BACKUP_FILE}.gz"
echo "[INFO] Backup created, compressed, and verified: ${BACKUP_FILE}.gz"

# Удаление старых бэкапов (оставляем последние $KEEP_BACKUPS штук)
echo "[INFO] Cleaning up old backups (keeping last ${KEEP_BACKUPS})..."
ls -tp "${BACKUP_DIR}"/mediabuyer_*.db.gz 2>/dev/null | grep -v '/$' | tail -n +$((KEEP_BACKUPS + 1)) | xargs -I {} rm -f -- "{}" || true
echo "[SUCCESS] Backup routine completed."
