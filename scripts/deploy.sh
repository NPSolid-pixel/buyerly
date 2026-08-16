#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Buyerly — Скрипт автоматического деплоя на Production VPS
# ==============================================================================

APP_DIR="${APP_DIR:-/opt/buyerly}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRANCH="${BRANCH:-main}"

echo "========================================================"
echo "🚀 [$(date +'%Y-%m-%d %H:%M:%S')] Starting Buyerly Deployment"
echo "========================================================"

cd "${APP_DIR}"

# 1. Резервное копирование базы данных перед обновлением
if [[ -f "${SCRIPT_DIR}/backup_db.sh" ]]; then
    echo "📦 [1/4] Running database backup..."
    bash "${SCRIPT_DIR}/backup_db.sh" || echo "⚠️ Warning: Backup failed, continuing..."
else
    echo "⚠️ [1/4] Backup script not found, skipping..."
fi

# Очистка старых остатков geo-atlas (если есть)
if [[ -f "${SCRIPT_DIR}/cleanup_geo_atlas.sh" ]]; then
    bash "${SCRIPT_DIR}/cleanup_geo_atlas.sh" || true
fi

# 2. Получение свежего кода из репозитория
echo "📥 [2/4] Pulling latest changes from branch '${BRANCH}'..."
git fetch origin "${BRANCH}"
git reset --hard "origin/${BRANCH}"

# 3. Пересборка и запуск Docker-контейнеров
echo "🐳 [3/4] Building and restarting Docker containers..."
docker compose down || true
docker compose up -d --build

# 4. Проверка статуса сервиса
echo "🔍 [4/4] Verifying container health..."
sleep 3
if docker compose ps | grep -q "buyerly-bot.*Up"; then
    echo "✅ [SUCCESS] Buyerly container is running successfully!"
    docker compose ps
else
    echo "❌ [ERROR] Buyerly container failed to start! Checking logs:"
    docker compose logs --tail=50
    exit 1
fi

echo "========================================================"
echo "🎉 Deployment completed successfully at $(date +'%Y-%m-%d %H:%M:%S')"
echo "========================================================"
