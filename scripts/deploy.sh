#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/buyerly}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRANCH="${BRANCH:-main}"
EXPECTED_SHA="${EXPECTED_SHA:-}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-180}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/buyerly-deploy.lock}"
DEPLOY_LOCK_TIMEOUT_SECONDS="${DEPLOY_LOCK_TIMEOUT_SECONDS:-180}"
EXPECTED_GIT_REPOSITORY="${EXPECTED_GIT_REPOSITORY:-hiurano/buyerly}"

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

wait_for_container_file() {
    local container_name="$1"
    local file_path="$2"
    local deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
    while (( SECONDS < deadline )); do
        if docker exec "${container_name}" test -f "${file_path}" 2>/dev/null; then
            return 0
        fi
        sleep 3
    done
    echo "[ERROR] ${container_name} did not complete the required scheduler cycle."
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
        if [[ -n "${RESEND_API_KEY:-}" ]]; then
            if grep -q '^RESEND_API_KEY=' .env 2>/dev/null; then
                sed -i "s|^RESEND_API_KEY=.*|RESEND_API_KEY=${RESEND_API_KEY}|" .env
            else
                printf '\nRESEND_API_KEY=%s\n' "${RESEND_API_KEY}" >> .env
            fi
        fi
        if ! grep -q '^EMAIL_FROM=' .env 2>/dev/null; then
            printf 'EMAIL_FROM="Buyerly <team@buyerly.app>"\n' >> .env
        fi
    fi
}

ensure_meta_token_encryption_key() {
    local configured_key=""
    local primary_key=""
    if [[ -f .env ]]; then
        configured_key=$(sed -n 's/^META_TOKEN_ENCRYPTION_KEY=//p' .env | tail -n 1)
    fi
    configured_key="${configured_key#\"}"
    configured_key="${configured_key%\"}"
    configured_key="${configured_key#\'}"
    configured_key="${configured_key%\'}"

    if [[ -z "${configured_key}" ]]; then
        configured_key=$(python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"))')
        if grep -q '^META_TOKEN_ENCRYPTION_KEY=' .env 2>/dev/null; then
            sed -i "s|^META_TOKEN_ENCRYPTION_KEY=.*|META_TOKEN_ENCRYPTION_KEY=${configured_key}|" .env
        else
            printf '\nMETA_TOKEN_ENCRYPTION_KEY=%s\n' "${configured_key}" >> .env
        fi
        chmod 600 .env
        echo "[INFO] Generated the Meta token encryption credential."
    fi

    primary_key="${configured_key%%,*}"
    if ! META_KEY_CANDIDATE="${primary_key}" python3 - <<'PY'
import base64
import os
import sys

try:
    decoded = base64.b64decode(
        os.environ["META_KEY_CANDIDATE"].encode("ascii"),
        altchars=b"-_",
        validate=True,
    )
except (KeyError, UnicodeEncodeError, ValueError):
    sys.exit(1)
sys.exit(0 if len(decoded) == 32 else 1)
PY
    then
        echo "[ERROR] META_TOKEN_ENCRYPTION_KEY has an invalid primary Fernet key."
        return 1
    fi
}

preserve_legacy_uploads() {
    local uploads_volume="buyerly-uploads"
    local legacy_upload_dir=""

    if docker volume inspect "${uploads_volume}" >/dev/null 2>&1; then
        return
    fi
    docker volume create "${uploads_volume}" >/dev/null

    if ! docker inspect buyerly-api >/dev/null 2>&1; then
        return
    fi

    legacy_upload_dir=$(mktemp -d)
    if docker cp buyerly-api:/app/webapp/uploads/. "${legacy_upload_dir}/" 2>/dev/null; then
        docker run --rm \
            -v "${uploads_volume}:/uploads" \
            -v "${legacy_upload_dir}:/legacy:ro" \
            "buyerly-app:${TARGET_SHA}" \
            sh -c 'cp -a /legacy/. /uploads/'
        echo "[INFO] Preserved legacy user uploads in the durable volume."
    fi
    rm -rf "${legacy_upload_dir}"
}

normalize_repository_ownership() {
    local owner_uid deploy_uid deploy_gid
    owner_uid=$(stat -c '%u' "${APP_DIR}")
    deploy_uid=$(id -u)
    deploy_gid=$(id -g)
    if [[ "${owner_uid}" == "${deploy_uid}" ]]; then
        return
    fi

    echo "[INFO] Normalizing ${APP_DIR} ownership from uid ${owner_uid} to deploy uid ${deploy_uid}."
    if [[ "${deploy_uid}" == "0" ]]; then
        chown -R "${deploy_uid}:${deploy_gid}" "${APP_DIR}"
    elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo -n chown -R "${deploy_uid}:${deploy_gid}" "${APP_DIR}"
    else
        echo "[ERROR] Cannot normalize ${APP_DIR} ownership without root or passwordless sudo."
        return 1
    fi

    owner_uid=$(stat -c '%u' "${APP_DIR}")
    if [[ "${owner_uid}" != "${deploy_uid}" ]]; then
        echo "[ERROR] ${APP_DIR} ownership normalization did not take effect."
        return 1
    fi
}

rollback() {
    echo "[ROLLBACK] Stopping the failed service set..."
    docker compose stop web api bot worker 2>/dev/null || true

    if [[ -n "${PREVIOUS_APP_IMAGE}" && -n "${PREVIOUS_WEB_IMAGE}" \
          && -n "${PREVIOUS_SHA}" ]]; then
        docker tag "${PREVIOUS_APP_IMAGE}" "buyerly-app:${PREVIOUS_SHA}"
        docker tag "${PREVIOUS_WEB_IMAGE}" "buyerly-web:${PREVIOUS_SHA}"
        export APP_VERSION="${PREVIOUS_SHA}"
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
normalize_repository_ownership

ORIGIN_URL=$(git remote get-url origin 2>/dev/null || true)
NORMALIZED_ORIGIN="${ORIGIN_URL%/}"
NORMALIZED_ORIGIN="${NORMALIZED_ORIGIN%.git}"
case "${NORMALIZED_ORIGIN}" in
    "git@github.com:${EXPECTED_GIT_REPOSITORY}"|"https://github.com/${EXPECTED_GIT_REPOSITORY}"|"ssh://git@github.com/${EXPECTED_GIT_REPOSITORY}")
        ;;
    *)
        echo "[ERROR] Unexpected production repository origin: ${ORIGIN_URL:-missing}"
        exit 1
        ;;
esac

exec 9>"${DEPLOY_LOCK_FILE}"
if ! flock -w "${DEPLOY_LOCK_TIMEOUT_SECONDS}" 9; then
    echo "[ERROR] Another Buyerly deployment is still running."
    exit 1
fi

ensure_postgres_password
ensure_email_settings
ensure_meta_token_encryption_key

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
    REDIS_HEALTH=$(docker inspect --format '{{.State.Health.Status}}' buyerly-redis 2>/dev/null || true)
    if [[ "${CURRENT_REPO_SHA}" == "${EXPECTED_SHA}" \
          && "${DEPLOYED_API_IMAGE}" == "buyerly-app:${EXPECTED_SHA}" \
          && "${DEPLOYED_WEB_IMAGE}" == "buyerly-web:${EXPECTED_SHA}" \
          && "${DEPLOYED_BOT_IMAGE}" == "buyerly-app:${EXPECTED_SHA}" \
          && "${DEPLOYED_WORKER_IMAGE}" == "buyerly-app:${EXPECTED_SHA}" \
          && "${API_HEALTH}" == "healthy" \
          && "${WEB_HEALTH}" == "healthy" \
          && "${BOT_HEALTH}" == "healthy" \
          && "${WORKER_HEALTH}" == "healthy" \
          && "${DB_HEALTH}" == "healthy" \
          && "${REDIS_HEALTH}" == "healthy" ]]; then
        echo "[SUCCESS] Buyerly ${EXPECTED_SHA} is already deployed and healthy."
        exit 0
    fi
fi

echo "[1/6] Creating a mandatory database backup..."
bash "${SCRIPT_DIR}/backup_db.sh"

PREVIOUS_APP_IMAGE=$(docker inspect --format '{{.Image}}' buyerly-api 2>/dev/null || true)
PREVIOUS_WEB_IMAGE=$(docker inspect --format '{{.Image}}' buyerly-web 2>/dev/null || true)
PREVIOUS_APP_TAG=$(docker inspect --format '{{.Config.Image}}' buyerly-api 2>/dev/null || true)
PREVIOUS_WEB_TAG=$(docker inspect --format '{{.Config.Image}}' buyerly-web 2>/dev/null || true)
PREVIOUS_SHA=""
if [[ "${PREVIOUS_APP_TAG}" =~ ^buyerly-app:([0-9a-f]{40})$ ]]; then
    PREVIOUS_SHA_CANDIDATE="${BASH_REMATCH[1]}"
    if [[ "${PREVIOUS_WEB_TAG}" == "buyerly-web:${PREVIOUS_SHA_CANDIDATE}" ]]; then
        PREVIOUS_SHA="${PREVIOUS_SHA_CANDIDATE}"
    fi
fi

echo "[2/6] Synchronizing ${BRANCH}..."
git fetch origin "${BRANCH}"
TARGET_SHA=$(git rev-parse "origin/${BRANCH}")
if [[ -n "${EXPECTED_SHA}" && "${TARGET_SHA}" != "${EXPECTED_SHA}" ]]; then
    echo "[ERROR] origin/${BRANCH} is ${TARGET_SHA}, expected ${EXPECTED_SHA}."
    exit 1
fi
git reset --hard "${TARGET_SHA}"
# `reset --hard` leaves untracked source files behind. That is unsafe for a
# Docker build because retired Python/Alembic files can still be copied into
# the image and executed. Runtime state is gitignored; remove only untracked,
# non-ignored repository files so the build context matches TARGET_SHA.
git clean -ffd -q
if [[ -n "$(git status --short --untracked-files=all)" ]]; then
    echo "[ERROR] Production source tree does not match ${TARGET_SHA}."
    exit 1
fi
export APP_VERSION="${TARGET_SHA}"
ensure_postgres_password
ensure_email_settings
ensure_meta_token_encryption_key

echo "[3/6] Building versioned API and web images..."
docker compose build --pull api web
preserve_legacy_uploads

echo "[4/6] Preparing PostgreSQL database..."
docker compose up -d db redis
if ! wait_for_container buyerly-db; then
    docker compose logs --tail=120 db
    exit 1
fi
if ! wait_for_container buyerly-redis; then
    docker compose logs --tail=120 redis
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
if ! wait_for_container_file buyerly-worker /tmp/buyerly-worker-day-boundary-cycle-complete; then
    docker compose logs --tail=160 worker
    rollback
    exit 1
fi
if docker compose logs --since=5m worker 2>&1 \
    | grep -Eiq 'Failed to persist audit event.*(owner_id|owner_user_id)|NotNullViolation.*(owner_id|owner_user_id)'; then
    echo "[ERROR] Worker audit ownership failure detected after a full scheduler cycle."
    docker compose logs --tail=160 worker
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

echo "[SUCCESS] Buyerly ${TARGET_SHA} deployed as web/api/bot/worker/db."
docker compose ps
