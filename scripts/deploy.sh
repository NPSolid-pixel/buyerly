#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/buyerly}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRANCH="${BRANCH:-main}"
EXPECTED_SHA="${EXPECTED_SHA:-}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-180}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/buyerly-deploy.lock}"
DEPLOY_LOCK_TIMEOUT_SECONDS="${DEPLOY_LOCK_TIMEOUT_SECONDS:-180}"

wait_for_container() {
    local container_name="$1"
    local expected_status="${2:-healthy}"
    local deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
    local status=""
    while (( SECONDS < deadline )); do
        status=$(docker inspect \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
            "${container_name}" 2>/dev/null || true)
        if [[ "${status}" == "${expected_status}" ]]; then
            return 0
        fi
        if [[ "${status}" == "exited" || "${status}" == "dead" ]]; then
            break
        fi
        sleep 3
    done
    echo "[ERROR] ${container_name} did not reach ${expected_status}; status=${status:-missing}"
    return 1
}

ensure_postgres_password() {
    if grep -q '^POSTGRES_PASSWORD=' .env 2>/dev/null; then
        return
    fi
    local generated_password
    if command -v openssl >/dev/null 2>&1; then
        generated_password=$(openssl rand -hex 32)
    else
        generated_password=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
    fi
    printf '\nPOSTGRES_PASSWORD=%s\n' "${generated_password}" >> .env
    chmod 600 .env
    echo "[INFO] Generated the local PostgreSQL credential."
}

ensure_email_settings() {
    if [[ -f .env ]]; then
        if ! grep -q '^RESEND_API_KEY=' .env 2>/dev/null; then
            printf '\nRESEND_API_KEY=\n' >> .env
        fi
        if ! grep -q '^EMAIL_FROM=' .env 2>/dev/null; then
            printf 'EMAIL_FROM="Buyerly <team@buyerly.app>"\n' >> .env
        fi
    fi
}

rollback() {
    echo "[ROLLBACK] Stopping the failed service set..."
    docker compose stop web api bot worker 2>/dev/null || true

    if [[ "${HAD_LEGACY}" == "true" && -n "${PREVIOUS_LEGACY_IMAGE}" ]]; then
        docker tag "${PREVIOUS_LEGACY_IMAGE}" "buyerly-app:${CURRENT_SHA}"
        export APP_VERSION="${CURRENT_SHA}"
        docker compose --profile legacy up -d --no-deps buyerly
        wait_for_container buyerly-bot
        echo "[ROLLBACK] Previous monolith restored."
        return
    fi

    if [[ -n "${PREVIOUS_APP_IMAGE}" && -n "${PREVIOUS_WEB_IMAGE}" ]]; then
        docker tag "${PREVIOUS_APP_IMAGE}" "buyerly-app:${CURRENT_SHA}"
        docker tag "${PREVIOUS_WEB_IMAGE}" "buyerly-web:${CURRENT_SHA}"
        export APP_VERSION="${CURRENT_SHA}"
        docker compose up -d --no-deps api bot worker
        wait_for_container buyerly-api
        wait_for_container buyerly-telegram-bot
        wait_for_container buyerly-worker
        docker compose up -d --no-deps web
        wait_for_container buyerly-web
        echo "[ROLLBACK] Previous service images restored."
        return
    fi

    echo "[ROLLBACK] No previous healthy image set is available."
    return 1
}

cd "${APP_DIR}"
exec 9>"${DEPLOY_LOCK_FILE}"
if ! flock -w "${DEPLOY_LOCK_TIMEOUT_SECONDS}" 9; then
    echo "[ERROR] Another Buyerly deployment is still running."
    exit 1
fi

ensure_postgres_password
ensure_email_settings

if [[ -n "${EXPECTED_SHA}" ]]; then
    CURRENT_REPO_SHA=$(git rev-parse HEAD 2>/dev/null || true)
    DEPLOYED_API_IMAGE=$(docker inspect --format '{{.Config.Image}}' buyerly-api 2>/dev/null || true)
    DEPLOYED_WEB_IMAGE=$(docker inspect --format '{{.Config.Image}}' buyerly-web 2>/dev/null || true)
    DEPLOYED_BOT_IMAGE=$(docker inspect --format '{{.Config.Image}}' buyerly-telegram-bot 2>/dev/null || true)
    DEPLOYED_WORKER_IMAGE=$(docker inspect --format '{{.Config.Image}}' buyerly-worker 2>/dev/null || true)
    API_HEALTH=$(docker inspect --format '{{.State.Health.Status}}' buyerly-api 2>/dev/null || true)
    WEB_HEALTH=$(docker inspect --format '{{.State.Health.Status}}' buyerly-web 2>/dev/null || true)
    BOT_HEALTH=$(docker inspect --format '{{.State.Health.Status}}' buyerly-telegram-bot 2>/dev/null || true)
    WORKER_HEALTH=$(docker inspect --format '{{.State.Health.Status}}' buyerly-worker 2>/dev/null || true)
    DB_HEALTH=$(docker inspect --format '{{.State.Health.Status}}' buyerly-db 2>/dev/null || true)
    if [[ "${CURRENT_REPO_SHA}" == "${EXPECTED_SHA}" \
          && "${DEPLOYED_API_IMAGE}" == "buyerly-app:${EXPECTED_SHA}" \
          && "${DEPLOYED_WEB_IMAGE}" == "buyerly-web:${EXPECTED_SHA}" \
          && "${DEPLOYED_BOT_IMAGE}" == "buyerly-app:${EXPECTED_SHA}" \
          && "${DEPLOYED_WORKER_IMAGE}" == "buyerly-app:${EXPECTED_SHA}" \
          && "${API_HEALTH}" == "healthy" \
          && "${WEB_HEALTH}" == "healthy" \
          && "${BOT_HEALTH}" == "healthy" \
          && "${WORKER_HEALTH}" == "healthy" \
          && "${DB_HEALTH}" == "healthy" ]]; then
        echo "[SUCCESS] Buyerly ${EXPECTED_SHA} is already deployed and healthy."
        exit 0
    fi
fi

echo "[1/6] Creating a mandatory database backup..."
bash "${SCRIPT_DIR}/backup_db.sh"

CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || true)
PREVIOUS_APP_IMAGE=$(docker inspect --format '{{.Image}}' buyerly-api 2>/dev/null || true)
PREVIOUS_WEB_IMAGE=$(docker inspect --format '{{.Image}}' buyerly-web 2>/dev/null || true)
PREVIOUS_LEGACY_IMAGE=$(docker inspect --format '{{.Image}}' buyerly-bot 2>/dev/null || true)
HAD_LEGACY=false
if [[ -n "${PREVIOUS_LEGACY_IMAGE}" ]]; then
    HAD_LEGACY=true
fi

echo "[2/6] Synchronizing ${BRANCH}..."
git fetch origin "${BRANCH}"
TARGET_SHA=$(git rev-parse "origin/${BRANCH}")
if [[ -n "${EXPECTED_SHA}" && "${TARGET_SHA}" != "${EXPECTED_SHA}" ]]; then
    echo "[ERROR] origin/${BRANCH} is ${TARGET_SHA}, expected ${EXPECTED_SHA}."
    exit 1
fi
git reset --hard "${TARGET_SHA}"
export APP_VERSION="${TARGET_SHA}"
ensure_postgres_password
ensure_email_settings

echo "[3/6] Building versioned API and web images..."
docker compose build --pull api web

echo "[4/6] Preparing PostgreSQL database..."
docker compose up -d db
if ! wait_for_container buyerly-db; then
    docker compose logs --tail=120 db
    exit 1
fi
if ! docker compose run --rm migrate; then
    docker compose logs --tail=120 migrate db
    rollback
    exit 1
fi

echo "[5/6] Switching traffic to the separated services..."
docker compose up -d --no-deps api bot worker
if ! wait_for_container buyerly-api; then
    docker compose logs --tail=120 api migrate db
    rollback
    exit 1
fi
if ! wait_for_container buyerly-telegram-bot || ! wait_for_container buyerly-worker; then
    docker compose logs --tail=120 bot worker
    rollback
    exit 1
fi
docker compose up -d --no-deps web

echo "[6/6] Verifying the public service boundary..."
if ! wait_for_container buyerly-web; then
    docker compose logs --tail=120 web api
    rollback
    exit 1
fi
curl -fsS http://127.0.0.1:8080/health/ready >/dev/null

if [[ "${HAD_LEGACY}" == "true" ]]; then
    docker rm buyerly-bot >/dev/null
fi

echo "[SUCCESS] Buyerly ${TARGET_SHA} deployed as web/api/bot/worker/db."
docker compose ps
