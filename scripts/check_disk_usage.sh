#!/usr/bin/env bash
set -euo pipefail

CHECK_PATH="${CHECK_PATH:-/opt/buyerly}"
DISK_WARNING_PERCENT="${DISK_WARNING_PERCENT:-75}"
DISK_CRITICAL_PERCENT="${DISK_CRITICAL_PERCENT:-90}"

if [[ ! "${DISK_WARNING_PERCENT}" =~ ^[0-9]+$ \
      || ! "${DISK_CRITICAL_PERCENT}" =~ ^[0-9]+$ \
      || "${DISK_WARNING_PERCENT}" -ge "${DISK_CRITICAL_PERCENT}" \
      || "${DISK_CRITICAL_PERCENT}" -gt 100 ]]; then
    echo "[ERROR] Disk thresholds must satisfy 0 <= warning < critical <= 100."
    exit 1
fi

usage_percent=$(df -P "${CHECK_PATH}" | awk 'NR == 2 {gsub(/%/, "", $5); print $5}')
if [[ ! "${usage_percent}" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] Cannot determine disk usage for ${CHECK_PATH}."
    exit 1
fi

if (( usage_percent >= DISK_CRITICAL_PERCENT )); then
    echo "[CRITICAL] Disk usage is ${usage_percent}% (threshold ${DISK_CRITICAL_PERCENT}%)."
    exit 2
fi
if (( usage_percent >= DISK_WARNING_PERCENT )); then
    echo "[WARNING] Disk usage is ${usage_percent}% (threshold ${DISK_WARNING_PERCENT}%)."
    exit 0
fi

echo "[OK] Disk usage is ${usage_percent}% (warning at ${DISK_WARNING_PERCENT}%)."
