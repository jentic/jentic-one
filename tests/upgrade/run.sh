#!/usr/bin/env bash
# Orchestrate a container upgrade test:
#   old-up -> seed -> stop -> migrate (candidate) -> new-up -> verify
#
# Environment / arguments (also wired through `make test-upgrade`):
#   OLD        Released source version tag in ghcr.io/jentic/jentic-one-app (default: 0.44.0)
#   BACKEND    Database backend (default: postgres)
#   CANDIDATE  Pre-built candidate image tag; when unset, builds jentic-one/app:candidate
#   OUT_DIR    Directory for logs and seed-manifest.json (default: build/upgrade-<OLD>-<BACKEND>)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
PROJECT_NAME="jentic-upgrade-test"

OLD="${OLD:-0.44.0}"
BACKEND="${BACKEND:-postgres}"
CANDIDATE="${CANDIDATE:-}"
OUT_DIR="${OUT_DIR:-${REPO_ROOT}/build/upgrade-${OLD}-${BACKEND}}"

export UPGRADE_APP_PORT="${UPGRADE_APP_PORT:-18000}"
export UPGRADE_BROKER_PORT="${UPGRADE_BROKER_PORT:-18100}"
export UPGRADE_UPSTREAM_PORT="${UPGRADE_UPSTREAM_PORT:-18200}"

if [[ "${BACKEND}" != "postgres" ]]; then
  echo "ERROR: unsupported BACKEND='${BACKEND}' (Phase 1 supports 'postgres' only)" >&2
  exit 2
fi

case "${OLD}" in
  0.44.*)
    SEEDER="${SCRIPT_DIR}/seed/seed_v0_44.py"
    ;;
  *)
    echo "ERROR: no seeder registered for source version OLD='${OLD}'" >&2
    exit 2
    ;;
esac

rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}"

compose() {
  docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" "$@"
}

cleanup() {
  local exit_code=$?
  if [[ ${exit_code} -ne 0 ]]; then
    echo "==> Upgrade test failed (exit ${exit_code}); capturing logs to ${OUT_DIR}" >&2
    compose logs --no-color >"${OUT_DIR}/compose.log" 2>&1 || true
    for svc in postgres smoke-upstream app broker; do
      compose logs --no-color "${svc}" >"${OUT_DIR}/${svc}-failure.log" 2>&1 || true
    done
    for log_file in "${OUT_DIR}/old-migrate.log" "${OUT_DIR}/migrate.log" "${OUT_DIR}/migrate-check.log"; do
      if [[ -f "${log_file}" ]]; then
        echo "--- $(basename "${log_file}") ---" >&2
        cat "${log_file}" >&2 || true
      fi
    done
  fi
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "${exit_code}"
}
trap cleanup EXIT

pull_with_retry() {
  local image="$1"
  local attempt
  if docker image inspect "${image}" >/dev/null 2>&1; then
    echo "==> Using cached source image ${image}"
    return 0
  fi
  for attempt in 1 2 3; do
    echo "==> Pulling ${image} (attempt ${attempt}/3)"
    if docker pull "${image}"; then
      return 0
    fi
    sleep $((attempt * 2))
  done
  echo "ERROR: failed to pull ${image} after 3 attempts" >&2
  return 1
}

wait_for_health() {
  local name="$1"
  local url="$2"
  local attempt
  for attempt in $(seq 1 60); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: ${name} did not become healthy at ${url}" >&2
  return 1
}

export OLD_IMAGE="ghcr.io/jentic/jentic-one-app:${OLD}"
pull_with_retry "${OLD_IMAGE}"

if [[ -n "${CANDIDATE}" ]]; then
  CANDIDATE_IMAGE="${CANDIDATE}"
  if ! docker image inspect "${CANDIDATE_IMAGE}" >/dev/null 2>&1; then
    echo "ERROR: CANDIDATE image '${CANDIDATE_IMAGE}' not found locally" >&2
    exit 2
  fi
  echo "==> Using provided candidate image ${CANDIDATE_IMAGE}"
else
  CANDIDATE_IMAGE="jentic-one/app:candidate"
  echo "==> Building candidate image ${CANDIDATE_IMAGE} from checkout"
  docker build -f "${REPO_ROOT}/deploy/docker/app.multiarch.Dockerfile" \
    -t "${CANDIDATE_IMAGE}" "${REPO_ROOT}" >"${OUT_DIR}/docker-build.log" 2>&1
fi

export NEW_IMAGE="${CANDIDATE_IMAGE}"
export SMOKE_UPSTREAM_IMAGE="${NEW_IMAGE}"

if [[ -n "$(git -C "${REPO_ROOT}" tag -l 'v*' 2>/dev/null || true)" ]]; then
  (cd "${REPO_ROOT}" && uv run python -m tests.upgrade.matrix --verify-git-tags)
else
  (cd "${REPO_ROOT}" && uv run python -m tests.upgrade.matrix)
fi

echo "==> Resetting isolated compose stack (${PROJECT_NAME})"
compose down -v --remove-orphans >/dev/null 2>&1 || true

echo "==> [1/6] Starting postgres and smoke-upstream"
APP_IMAGE="${OLD_IMAGE}" compose up -d --wait postgres smoke-upstream

echo "==> [2/6] Migrating and starting source release (${OLD_IMAGE})"
if ! APP_IMAGE="${OLD_IMAGE}" compose run --rm --no-deps migrate >"${OUT_DIR}/old-migrate.log" 2>&1; then
  echo "ERROR: source migration failed for ${OLD_IMAGE}" >&2
  exit 1
fi
APP_IMAGE="${OLD_IMAGE}" compose up -d --wait app broker
wait_for_health "old app" "http://127.0.0.1:${UPGRADE_APP_PORT}/health"
wait_for_health "old broker" "http://127.0.0.1:${UPGRADE_BROKER_PORT}/health"

echo "==> [3/6] Seeding pre-upgrade state on ${OLD}"
(
  cd "${REPO_ROOT}"
  uv run python "${SEEDER}" \
    --app-url "http://127.0.0.1:${UPGRADE_APP_PORT}" \
    --broker-url "http://127.0.0.1:${UPGRADE_BROKER_PORT}" \
    --upstream-url "http://smoke-upstream:8080" \
    --manifest "${OUT_DIR}/seed-manifest.json" \
    --source-version "${OLD}"
)
compose logs --no-color app >"${OUT_DIR}/old-app.log" 2>&1 || true
compose logs --no-color broker >"${OUT_DIR}/old-broker.log" 2>&1 || true

echo "==> [4/6] Stopping old app and broker containers"
compose rm -sf app broker >/dev/null

echo "==> [5/6] Running candidate migrations (${CANDIDATE_IMAGE})"
if ! APP_IMAGE="${CANDIDATE_IMAGE}" compose run --rm --no-deps migrate >"${OUT_DIR}/migrate.log" 2>&1; then
  echo "ERROR: candidate migration failed (see ${OUT_DIR}/migrate.log)" >&2
  exit 1
fi

if ! APP_IMAGE="${CANDIDATE_IMAGE}" compose run --rm --no-deps migrate \
  python -m jentic_one.migrations.run --check >"${OUT_DIR}/migrate-check.log" 2>&1; then
  echo "ERROR: candidate migrations --check exited non-zero (see ${OUT_DIR}/migrate-check.log)" >&2
  exit 1
fi
if ! grep -q "OVERALL current" "${OUT_DIR}/migrate-check.log"; then
  echo "ERROR: expected 'OVERALL current' in ${OUT_DIR}/migrate-check.log" >&2
  exit 1
fi

if ! APP_IMAGE="${CANDIDATE_IMAGE}" compose run --rm --no-deps migrate >"${OUT_DIR}/migrate-idempotent.log" 2>&1; then
  echo "ERROR: second (idempotency) candidate migration run failed (see ${OUT_DIR}/migrate-idempotent.log)" >&2
  exit 1
fi

echo "==> [6/6] Starting candidate app and broker and running post-upgrade verifier"
APP_IMAGE="${CANDIDATE_IMAGE}" compose up -d --wait app broker
wait_for_health "candidate app" "http://127.0.0.1:${UPGRADE_APP_PORT}/health"
wait_for_health "candidate broker" "http://127.0.0.1:${UPGRADE_BROKER_PORT}/health"

compose logs --no-color app >"${OUT_DIR}/new-app.log" 2>&1
compose logs --no-color broker >"${OUT_DIR}/new-broker.log" 2>&1

(
  cd "${REPO_ROOT}"
  UPGRADE_MANIFEST="${OUT_DIR}/seed-manifest.json" \
  UPGRADE_OUT_DIR="${OUT_DIR}" \
  uv run pytest tests/upgrade/verify/test_post_upgrade.py -q
)

compose logs --no-color app >"${OUT_DIR}/new-app.log" 2>&1
compose logs --no-color broker >"${OUT_DIR}/new-broker.log" 2>&1

echo "==> Upgrade test passed (${OLD} -> ${CANDIDATE_IMAGE} on ${BACKEND}; logs in ${OUT_DIR})"
