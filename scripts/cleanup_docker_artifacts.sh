#!/usr/bin/env bash
set -euo pipefail

KEEP_RELEASES="${KEEP_RELEASES:-2}"
BUILD_CACHE_UNTIL="${BUILD_CACHE_UNTIL:-168h}"
DANGLING_IMAGE_UNTIL="${DANGLING_IMAGE_UNTIL:-168h}"
DRY_RUN="${DRY_RUN:-false}"

if [[ ! "${KEEP_RELEASES}" =~ ^[2-9][0-9]*$ ]]; then
    echo "[ERROR] KEEP_RELEASES must be an integer greater than or equal to 2."
    exit 1
fi
if [[ "${DRY_RUN}" != "true" && "${DRY_RUN}" != "false" ]]; then
    echo "[ERROR] DRY_RUN must be true or false."
    exit 1
fi

records_file=$(mktemp)
protected_file=$(mktemp)
active_ids_file=$(mktemp)
trap 'rm -f "${records_file}" "${protected_file}" "${active_ids_file}"' EXIT

docker ps -aq \
    | while IFS= read -r container_id; do
        [[ -n "${container_id}" ]] || continue
        docker inspect --format '{{.Image}}' "${container_id}" 2>/dev/null || true
    done \
    | sort -u >"${active_ids_file}"

docker image ls buyerly-app --format '{{.Tag}}' \
    | grep -E '^[0-9a-f]{40}$' \
    | while IFS= read -r release_sha; do
        if docker image inspect "buyerly-web:${release_sha}" >/dev/null 2>&1; then
            created_at=$(docker image inspect --format '{{.Created}}' "buyerly-app:${release_sha}")
            printf '%s %s\n' "${created_at}" "${release_sha}"
        fi
    done \
    | sort -r >"${records_file}" || true

head -n "${KEEP_RELEASES}" "${records_file}" \
    | awk '{print $2}' \
    | sort -u >"${protected_file}"

remove_release_tag() {
    local repository="$1"
    local release_sha="$2"
    local image_ref="${repository}:${release_sha}"
    local image_id=""

    image_id=$(docker image inspect --format '{{.Id}}' "${image_ref}" 2>/dev/null || true)
    [[ -n "${image_id}" ]] || return 0

    if grep -Fxq "${release_sha}" "${protected_file}"; then
        echo "[KEEP] ${image_ref} is one of the newest ${KEEP_RELEASES} complete releases."
        return 0
    fi
    if grep -Fxq "${image_id}" "${active_ids_file}"; then
        echo "[KEEP] ${image_ref} is referenced by an existing container."
        return 0
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
        echo "[DRY-RUN] docker image rm ${image_ref}"
    else
        docker image rm "${image_ref}"
    fi
}

for repository in buyerly-app buyerly-web; do
    while IFS= read -r release_sha; do
        [[ -n "${release_sha}" ]] || continue
        remove_release_tag "${repository}" "${release_sha}"
    done < <(docker image ls "${repository}" --format '{{.Tag}}' | grep -E '^[0-9a-f]{40}$' || true)
done

if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] docker image prune --force --filter until=${DANGLING_IMAGE_UNTIL}"
    echo "[DRY-RUN] docker builder prune --force --filter until=${BUILD_CACHE_UNTIL}"
else
    docker image prune --force --filter "until=${DANGLING_IMAGE_UNTIL}"
    docker builder prune --force --filter "until=${BUILD_CACHE_UNTIL}"
fi

echo "[SUCCESS] Docker artifact retention completed; volumes were not inspected or pruned."
