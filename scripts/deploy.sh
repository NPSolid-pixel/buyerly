#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Buyerly — Скрипт автоматического деплоя на Production VPS
# ==============================================================================

APP_DIR="${APP_DIR:-/opt/buyerly}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRANCH="${BRANCH:-main}"
CONTAINER_NAME="${CONTAINER_NAME:-buyerly-bot}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-120}"
EXPECTED_SHA="${EXPECTED_SHA:-}"

wait_for_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
    local health_status=""

    while (( SECONDS < deadline )); do
        health_status=$(docker inspect \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
            "${CONTAINER_NAME}" 2>/dev/null || true)

        if [[ "${health_status}" == "healthy" ]]; then
            return 0
        fi
        if [[ "${health_status}" == "exited" || "${health_status}" == "dead" ]]; then
            break
        fi
        sleep 3
    done

    echo "[ERROR] Container health check failed with status: ${health_status:-missing}"
    return 1
}

echo "========================================================"
echo "🚀 [$(date +'%Y-%m-%d %H:%M:%S')] Starting Buyerly Deployment"
echo "========================================================"

cd "${APP_DIR}"

# 1. Обязательное резервное копирование базы данных перед обновлением
if [[ ! -f "${SCRIPT_DIR}/backup_db.sh" ]]; then
    echo "❌ [1/4] Backup script not found. Deployment stopped."
    exit 1
fi
echo "📦 [1/4] Running mandatory database backup..."
bash "${SCRIPT_DIR}/backup_db.sh"

CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || true)
PREVIOUS_IMAGE=$(docker inspect --format '{{.Image}}' "${CONTAINER_NAME}" 2>/dev/null || true)

# 2. Получение свежего кода из репозитория
echo "📥 [2/4] Pulling latest changes from branch '${BRANCH}'..."
git fetch origin "${BRANCH}"
TARGET_SHA=$(git rev-parse "origin/${BRANCH}")
if [[ -n "${EXPECTED_SHA}" && "${TARGET_SHA}" != "${EXPECTED_SHA}" ]]; then
    echo "❌ Expected ${EXPECTED_SHA}, but origin/${BRANCH} points to ${TARGET_SHA}."
    exit 1
fi
git reset --hard "${TARGET_SHA}"

# 3. Собираем новый образ, пока предыдущий контейнер продолжает работать
echo "🐳 [3/4] Building image for ${TARGET_SHA}..."
export APP_VERSION="${TARGET_SHA}"
docker compose build --pull buyerly
docker compose up -d --no-deps --force-recreate buyerly

# 4. Проверка healthcheck и автоматический откат образа
echo "🔍 [4/4] Verifying container health..."
if wait_for_healthy; then
    echo "✅ [SUCCESS] Buyerly ${TARGET_SHA} is healthy."
    docker compose ps
else
    echo "❌ [ERROR] Buyerly ${TARGET_SHA} failed health verification."
    docker compose logs --tail=100 buyerly

    if [[ -n "${PREVIOUS_IMAGE}" && -n "${CURRENT_SHA}" ]]; then
        echo "↩️ Rolling back to previous image ${PREVIOUS_IMAGE} (${CURRENT_SHA})..."
        docker tag "${PREVIOUS_IMAGE}" "buyerly-app:${CURRENT_SHA}"
        export APP_VERSION="${CURRENT_SHA}"
        docker compose up -d --no-deps --force-recreate buyerly
        if wait_for_healthy; then
            echo "✅ Rollback to ${CURRENT_SHA} completed successfully."
        else
            echo "❌ Rollback also failed. Manual intervention is required."
        fi
    else
        echo "❌ No previous image is available for automatic rollback."
    fi
    exit 1
fi

echo "========================================================"
echo "🎉 Deployment completed successfully at $(date +'%Y-%m-%d %H:%M:%S')"
echo "========================================================"
