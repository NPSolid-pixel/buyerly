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
KEEP_BACKUPS=30

mkdir -p "${BACKUP_DIR}"

if [[ -f "${DB_FILE}" ]]; then
    echo "[INFO] Creating database backup: ${BACKUP_FILE}"
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "${DB_FILE}" ".backup '${BACKUP_FILE}'"
    else
        cp "${DB_FILE}" "${BACKUP_FILE}"
    fi
    gzip -f "${BACKUP_FILE}"
    echo "[INFO] Backup created and compressed: ${BACKUP_FILE}.gz"

    # Удаление старых бэкапов (оставляем последние $KEEP_BACKUPS штук)
    echo "[INFO] Cleaning up old backups (keeping last ${KEEP_BACKUPS})..."
    ls -tp "${BACKUP_DIR}"/mediabuyer_*.db.gz 2>/dev/null | grep -v '/$' | tail -n +$((KEEP_BACKUPS + 1)) | xargs -I {} rm -f -- "{}" || true
    echo "[SUCCESS] Backup routine completed."
else
    echo "[WARN] Database file ${DB_FILE} not found. Skipping backup."
fi
