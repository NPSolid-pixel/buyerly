#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/opt/buyerly/backups}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-buyerly-db}"
POSTGRES_DB="${POSTGRES_DB:-buyerly}"
POSTGRES_USER="${POSTGRES_USER:-buyerly}"

FILE_PATH=""
USE_LATEST_LOCAL=false
DOWNLOAD_LATEST_OFFSITE=false
CONFIRM_YES=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --file)
            FILE_PATH="$2"
            shift 2
            ;;
        --latest-local)
            USE_LATEST_LOCAL=true
            shift
            ;;
        --download-latest-offsite)
            DOWNLOAD_LATEST_OFFSITE=true
            shift
            ;;
        --target-container)
            POSTGRES_CONTAINER="$2"
            shift 2
            ;;
        --target-db)
            POSTGRES_DB="$2"
            shift 2
            ;;
        --target-user)
            POSTGRES_USER="$2"
            shift 2
            ;;
        --yes|--non-interactive|-y)
            CONFIRM_YES=true
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--file <path> | --latest-local | --download-latest-offsite] [--yes]"
            exit 1
            ;;
    esac
done

if [[ "${DOWNLOAD_LATEST_OFFSITE}" == "true" ]]; then
    mkdir -p "${BACKUP_DIR}"
    echo "[INFO] Downloading the latest backup from off-site S3 storage..."
    latest_download="${BACKUP_DIR}/latest_offsite_restore.sql.gz.enc"
    python3 "${SCRIPT_DIR}/offsite_sync.py" --download-latest --dest "${latest_download}"
    FILE_PATH="${latest_download}"
elif [[ "${USE_LATEST_LOCAL}" == "true" || -z "${FILE_PATH}" ]]; then
    if [[ -z "${FILE_PATH}" && "${USE_LATEST_LOCAL}" != "true" ]]; then
        echo "[INFO] No backup file specified, searching for newest local backup..."
    fi
    latest_found=$(ls -t "${BACKUP_DIR}"/buyerly_postgres_*.sql* 2>/dev/null | head -n 1 || true)
    if [[ -z "${latest_found}" ]]; then
        echo "[ERROR] No backup files found in ${BACKUP_DIR}."
        exit 1
    fi
    FILE_PATH="${latest_found}"
fi

if [[ ! -f "${FILE_PATH}" ]]; then
    echo "[ERROR] Backup file not found: ${FILE_PATH}"
    exit 1
fi

echo "=================================================="
echo " Buyerly PostgreSQL Database Restoration"
echo " Source file:      ${FILE_PATH}"
echo " Target container: ${POSTGRES_CONTAINER}"
echo " Target database:  ${POSTGRES_DB}"
echo " Target user:      ${POSTGRES_USER}"
echo "=================================================="

if [[ "${CONFIRM_YES}" != "true" ]]; then
    read -r -p "WARNING: Restoring will overwrite existing data in '${POSTGRES_DB}'. Continue? [y/N]: " answer
    if [[ "${answer}" != [yY] && "${answer}" != [yY][eE][sS] ]]; then
        echo "[INFO] Database restore cancelled by user."
        exit 0
    fi
fi

postgres_state=$(docker inspect -f '{{.State.Status}}' "${POSTGRES_CONTAINER}" 2>/dev/null || true)
if [[ "${postgres_state}" != "running" ]]; then
    echo "[ERROR] PostgreSQL container '${POSTGRES_CONTAINER}' is not running (state: '${postgres_state:-missing}')."
    exit 1
fi

echo "[INFO] Starting stream restoration..."
if [[ "${FILE_PATH}" =~ \.enc$ ]]; then
    if [[ -z "${BACKUP_ENCRYPTION_KEY:-}" ]]; then
        echo "[ERROR] BACKUP_ENCRYPTION_KEY environment variable is required to decrypt ${FILE_PATH}."
        exit 1
    fi
    echo "[INFO] Decrypting and restoring encrypted archive..."
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass "env:BACKUP_ENCRYPTION_KEY" -in "${FILE_PATH}" \
        | gzip -dc \
        | docker exec -i "${POSTGRES_CONTAINER}" psql \
            --username="${POSTGRES_USER}" \
            --dbname="${POSTGRES_DB}" \
            --set=ON_ERROR_STOP=1 \
            --quiet
elif [[ "${FILE_PATH}" =~ \.gz$ ]]; then
    echo "[INFO] Decompressing and restoring gzipped archive..."
    gzip -dc "${FILE_PATH}" \
        | docker exec -i "${POSTGRES_CONTAINER}" psql \
            --username="${POSTGRES_USER}" \
            --dbname="${POSTGRES_DB}" \
            --set=ON_ERROR_STOP=1 \
            --quiet
else
    echo "[INFO] Restoring plain SQL dump..."
    docker exec -i "${POSTGRES_CONTAINER}" psql \
        --username="${POSTGRES_USER}" \
        --dbname="${POSTGRES_DB}" \
        --set=ON_ERROR_STOP=1 \
        --quiet < "${FILE_PATH}"
fi

echo "[SUCCESS] PostgreSQL database '${POSTGRES_DB}' restored successfully from ${FILE_PATH}."
