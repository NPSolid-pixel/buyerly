#!/usr/bin/env bash
set -euo pipefail

CRON_FILE="/etc/cron.d/buyerly-backup"
APP_DIR="${APP_DIR:-/opt/buyerly}"
LOG_FILE="/var/log/buyerly-backup.log"

echo "[INFO] Setting up Buyerly automated daily backup cron job..."

if [[ $EUID -ne 0 ]]; then
    echo "[WARNING] Not running as root. Attempting to install to user crontab..."
    cron_cmd="0 3 * * * cd ${APP_DIR} && bash scripts/backup_db.sh >> ${LOG_FILE} 2>&1"
    (crontab -l 2>/dev/null | grep -v 'backup_db.sh' || true; echo "${cron_cmd}") | crontab -
    echo "[SUCCESS] Daily backup job added to user crontab (runs daily at 03:00 UTC)."
    exit 0
fi

cat <<EOF > "${CRON_FILE}"
# Buyerly daily automated database backup and off-site sync
# Runs daily at 03:00 UTC
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin

0 3 * * * root cd ${APP_DIR} && bash scripts/backup_db.sh >> ${LOG_FILE} 2>&1
EOF

chmod 0644 "${CRON_FILE}"
echo "[SUCCESS] Daily backup cron installed at ${CRON_FILE} (runs daily at 03:00 UTC)."
