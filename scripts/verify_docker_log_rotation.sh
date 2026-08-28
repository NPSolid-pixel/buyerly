#!/usr/bin/env bash
set -euo pipefail

EXPECTED_DRIVER="${EXPECTED_LOG_DRIVER:-json-file}"
EXPECTED_MAX_SIZE="${EXPECTED_LOG_MAX_SIZE:-20m}"
EXPECTED_MAX_FILE="${EXPECTED_LOG_MAX_FILE:-5}"
EXPECTED_COMPRESS="${EXPECTED_LOG_COMPRESS:-true}"

containers=(
    buyerly-db
    buyerly-redis
    buyerly-api
    buyerly-web
    buyerly-telegram-bot
    buyerly-worker
)

for container_name in "${containers[@]}"; do
    driver=$(docker inspect --format '{{.HostConfig.LogConfig.Type}}' "${container_name}")
    max_size=$(docker inspect --format '{{index .HostConfig.LogConfig.Config "max-size"}}' "${container_name}")
    max_file=$(docker inspect --format '{{index .HostConfig.LogConfig.Config "max-file"}}' "${container_name}")
    compress=$(docker inspect --format '{{index .HostConfig.LogConfig.Config "compress"}}' "${container_name}")
    if [[ "${driver}" != "${EXPECTED_DRIVER}" \
          || "${max_size}" != "${EXPECTED_MAX_SIZE}" \
          || "${max_file}" != "${EXPECTED_MAX_FILE}" \
          || "${compress}" != "${EXPECTED_COMPRESS}" ]]; then
        echo "[ERROR] ${container_name} log rotation is ${driver}/${max_size}/${max_file}/${compress}."
        exit 1
    fi
    echo "[OK] ${container_name} log rotation is bounded."
done

echo "[SUCCESS] Docker log rotation matches the production contract."
