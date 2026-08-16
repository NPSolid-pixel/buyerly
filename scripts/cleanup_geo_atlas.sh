#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Buyerly — Скрипт полной очистки старого проекта geo-atlas с сервера
# ==============================================================================

echo "========================================================"
echo "🧹 [$(date +'%Y-%m-%d %H:%M:%S')] Purging legacy 'geo-atlas' from server"
echo "========================================================"

# 1. Остановка и удаление systemd служб и таймеров
echo "🛑 [1/3] Disabling & removing systemd services/timers..."
systemctl stop geo-atlas.service geo-atlas-deploy.timer geo-atlas-deploy.service 2>/dev/null || true
systemctl disable geo-atlas.service geo-atlas-deploy.timer geo-atlas-deploy.service 2>/dev/null || true
rm -f /etc/systemd/system/geo-atlas* 2>/dev/null || true
rm -f /etc/systemd/system/multi-user.target.wants/geo-atlas* 2>/dev/null || true
rm -f /etc/systemd/system/timers.target.wants/geo-atlas* 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
systemctl reset-failed 2>/dev/null || true

# 2. Удаление Docker-контейнеров и образов
echo "🐳 [2/3] Removing Docker containers & images..."
docker stop geo-atlas geo-atlas-app 2>/dev/null || true
docker rm -f geo-atlas geo-atlas-app 2>/dev/null || true

# 3. Удаление директорий проекта
echo "🗑 [3/3] Removing leftover project directories..."
rm -rf /opt/geo-atlas /root/geo-atlas /var/www/geo-atlas /tmp/geo-atlas*

echo "✅ Legacy geo-atlas has been completely removed from the server."
