#!/usr/bin/env bash
# Orchestrate a container upgrade test:
#   old-up -> seed -> stop -> migrate (candidate) -> new-up -> verify
#
# Environment / arguments (also wired through `make test-upgrade`):
#   OLD         Released source version tag in ghcr.io/jentic/jentic-one-app
#               (default: the n-1 source resolved from git tags by tests/upgrade/matrix.py)
#   OLD_DIGEST  Optional sha256 manifest digest; when set the source image is pulled by digest
#   BACKEND     Database backend (default: postgres)
#   CANDIDATE   Pre-built candidate image tag; when unset, builds jentic-one/app:candidate
#   OUT_DIR     Directory for logs and seed-manifest.json (default: build/upgrade-<OLD>-<BACKEND>)
#
# Exit codes:
#   0   upgrade path passed
#   1   upgrade path failed: candidate migration, migration check, idempotent
#       re-run, candidate startup on the migrated data, or the post-upgrade verifier
#   2   usage error (bad arguments)
#   75  inconclusive: the test could not reach the upgrade step (source image
#       pull, source release startup, seeding against the source API, or the
#       candidate image is missing); says nothing about the migration path

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
PROJECT_NAME="jentic-upgrade-test"
SOURCE_IMAGE_REPO="ghcr.io/jentic/jentic-one-app"

EX_UPGRADE_FAILED=1
EX_USAGE=2
EX_INCONCLUSIVE=75

PHASE="setup"

fail_upgrade() {
  echo "ERROR: upgrade path failed: $*" >&2
  exit "${EX_UPGRADE_FAILED}"
}

inconclusive() {
  echo "INCONCLUSIVE: $*" >&2
  exit "${EX_INCONCLUSIVE}"
}

matrix_cli() {
  (cd "${REPO_ROOT}" && uv run --quiet python -m tests.upgrade.matrix "$@")
}

OLD="${OLD:-}"
OLD_DIGEST="${OLD_DIGEST:-}"
BACKEND="${BACKEND:-postgres}"
CANDIDATE="${CANDIDATE:-}"

if [[ -z "${OLD}" ]]; then
  OLD="$(matrix_cli --default-source)" || {
    echo "ERROR: OLD is unset and no default source could be resolved from git tags" >&2
    exit "${EX_USAGE}"
  }
  echo "==> Using default source release ${OLD}"
fi

OUT_DIR="${OUT_DIR:-${REPO_ROOT}/build/upgrade-${OLD}-${BACKEND}}"

export UPGRADE_APP_PORT="${UPGRADE_APP_PORT:-18000}"
export UPGRADE_BROKER_PORT="${UPGRADE_BROKER_PORT:-18100}"
export UPGRADE_UPSTREAM_PORT="${UPGRADE_UPSTREAM_PORT:-18200}"

if [[ "${BACKEND}" != "postgres" ]]; then
  echo "ERROR: unsupported BACKEND='${BACKEND}' (Phase 1 supports 'postgres' only)" >&2
  exit "${EX_USAGE}"
fi

if [[ -n "${OLD_DIGEST}" && ! "${OLD_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: OLD_DIGEST must be 'sha256:<64-hex>' (got '${OLD_DIGEST}')" >&2
  exit "${EX_USAGE}"
fi

SEEDER="$(matrix_cli --seeder-for "${OLD}")" || {
  echo "ERROR: no seeder in tests/upgrade/seed/ applies to source OLD='${OLD}'" >&2
  exit "${EX_USAGE}"
}

rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}"

compose() {
  docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" "$@"
}

cleanup() {
  local exit_code=$?
  if [[ ${exit_code} -ne 0 ]]; then
    echo "==> Upgrade test exited ${exit_code} during phase '${PHASE}'; capturing logs to ${OUT_DIR}" >&2
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

# ---------------------------------------------------------------------------
# Pre-upgrade phases. A failure here means the test never reached the upgrade
# step, so it is inconclusive rather than an upgrade-path failure.
# ---------------------------------------------------------------------------
PHASE="pull-source"
if [[ -n "${OLD_DIGEST}" ]]; then
  export OLD_IMAGE="${SOURCE_IMAGE_REPO}@${OLD_DIGEST}"
else
  export OLD_IMAGE="${SOURCE_IMAGE_REPO}:${OLD}"
fi
pull_with_retry "${OLD_IMAGE}" || inconclusive "could not pull source image ${OLD_IMAGE}"

PHASE="candidate-image"
if [[ -n "${CANDIDATE}" ]]; then
  CANDIDATE_IMAGE="${CANDIDATE}"
  if ! docker image inspect "${CANDIDATE_IMAGE}" >/dev/null 2>&1; then
    inconclusive "CANDIDATE image '${CANDIDATE_IMAGE}' not found locally"
  fi
  echo "==> Using provided candidate image ${CANDIDATE_IMAGE}"
else
  CANDIDATE_IMAGE="jentic-one/app:candidate"
  echo "==> Building candidate image ${CANDIDATE_IMAGE} from checkout"
  docker build -f "${REPO_ROOT}/deploy/docker/app.multiarch.Dockerfile" \
    -t "${CANDIDATE_IMAGE}" "${REPO_ROOT}" >"${OUT_DIR}/docker-build.log" 2>&1 \
    || inconclusive "candidate image build failed (see ${OUT_DIR}/docker-build.log)"
fi

export NEW_IMAGE="${CANDIDATE_IMAGE}"
export SMOKE_UPSTREAM_IMAGE="${NEW_IMAGE}"

echo "==> Resetting isolated compose stack (${PROJECT_NAME})"
compose down -v --remove-orphans >/dev/null 2>&1 || true

PHASE="start-dependencies"
echo "==> [1/6] Starting postgres and smoke-upstream"
APP_IMAGE="${OLD_IMAGE}" compose up -d --wait postgres smoke-upstream \
  || inconclusive "postgres or smoke-upstream did not start"

PHASE="start-source"
echo "==> [2/6] Migrating and starting source release (${OLD_IMAGE})"
APP_IMAGE="${OLD_IMAGE}" compose run --rm --no-deps migrate >"${OUT_DIR}/old-migrate.log" 2>&1 \
  || inconclusive "source release ${OLD} failed to migrate a fresh database (see ${OUT_DIR}/old-migrate.log)"
APP_IMAGE="${OLD_IMAGE}" compose up -d --wait app broker \
  || inconclusive "source release ${OLD} app/broker did not start"
wait_for_health "old app" "http://127.0.0.1:${UPGRADE_APP_PORT}/health" \
  || inconclusive "source release ${OLD} app did not become healthy"
wait_for_health "old broker" "http://127.0.0.1:${UPGRADE_BROKER_PORT}/health" \
  || inconclusive "source release ${OLD} broker did not become healthy"

PHASE="seed"
echo "==> [3/6] Seeding pre-upgrade state on ${OLD} with $(basename "${SEEDER}")"
(
  cd "${REPO_ROOT}"
  uv run python "${SEEDER}" \
    --app-url "http://127.0.0.1:${UPGRADE_APP_PORT}" \
    --broker-url "http://127.0.0.1:${UPGRADE_BROKER_PORT}" \
    --upstream-url "http://smoke-upstream:8080" \
    --manifest "${OUT_DIR}/seed-manifest.json" \
    --source-version "${OLD}"
) || inconclusive "seeding failed against the ${OLD} API; add a seeder for this source under tests/upgrade/seed/"
compose logs --no-color app >"${OUT_DIR}/old-app.log" 2>&1 || true
compose logs --no-color broker >"${OUT_DIR}/old-broker.log" 2>&1 || true

PHASE="stop-source"
echo "==> [4/6] Stopping old app and broker containers"
compose rm -sf app broker >/dev/null || inconclusive "could not stop the source release containers"

# ---------------------------------------------------------------------------
# Upgrade phases. Any failure from here on is an upgrade-path failure.
# ---------------------------------------------------------------------------
PHASE="candidate-migrate"
echo "==> [5/6] Running candidate migrations (${CANDIDATE_IMAGE})"
APP_IMAGE="${CANDIDATE_IMAGE}" compose run --rm --no-deps migrate >"${OUT_DIR}/migrate.log" 2>&1 \
  || fail_upgrade "candidate migration failed (see ${OUT_DIR}/migrate.log)"

PHASE="candidate-migrate-check"
APP_IMAGE="${CANDIDATE_IMAGE}" compose run --rm --no-deps migrate \
  python -m jentic_one.migrations.run --check >"${OUT_DIR}/migrate-check.log" 2>&1 \
  || fail_upgrade "candidate migrations --check exited non-zero (see ${OUT_DIR}/migrate-check.log)"
grep -q "OVERALL current" "${OUT_DIR}/migrate-check.log" \
  || fail_upgrade "expected 'OVERALL current' in ${OUT_DIR}/migrate-check.log"

PHASE="candidate-migrate-idempotent"
APP_IMAGE="${CANDIDATE_IMAGE}" compose run --rm --no-deps migrate >"${OUT_DIR}/migrate-idempotent.log" 2>&1 \
  || fail_upgrade "second (idempotency) candidate migration run failed (see ${OUT_DIR}/migrate-idempotent.log)"

PHASE="candidate-start"
echo "==> [6/6] Starting candidate app and broker and running post-upgrade verifier"
APP_IMAGE="${CANDIDATE_IMAGE}" compose up -d --wait app broker \
  || fail_upgrade "candidate app/broker did not start on the migrated data"
wait_for_health "candidate app" "http://127.0.0.1:${UPGRADE_APP_PORT}/health" \
  || fail_upgrade "candidate app did not become healthy on the migrated data"
wait_for_health "candidate broker" "http://127.0.0.1:${UPGRADE_BROKER_PORT}/health" \
  || fail_upgrade "candidate broker did not become healthy on the migrated data"

compose logs --no-color app >"${OUT_DIR}/new-app.log" 2>&1 || true
compose logs --no-color broker >"${OUT_DIR}/new-broker.log" 2>&1 || true

PHASE="verify"
(
  cd "${REPO_ROOT}"
  UPGRADE_MANIFEST="${OUT_DIR}/seed-manifest.json" \
  UPGRADE_OUT_DIR="${OUT_DIR}" \
  uv run pytest tests/upgrade/verify/test_post_upgrade.py -q
) || fail_upgrade "post-upgrade verifier failed"

compose logs --no-color app >"${OUT_DIR}/new-app.log" 2>&1 || true
compose logs --no-color broker >"${OUT_DIR}/new-broker.log" 2>&1 || true

echo "==> Upgrade test passed (${OLD} -> ${CANDIDATE_IMAGE} on ${BACKEND}; logs in ${OUT_DIR})"
