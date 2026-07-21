#!/usr/bin/env bash
# Idempotent local bring-up for Jentic One (issue #652).
#
# Safe to run repeatedly and after a machine reboot. It performs only the
# preflight that a `make start-app` needs — it does NOT start the app itself
# (the `dev` make target does that once this returns). Each step is a no-op
# when its target is already satisfied:
#
#   1. Verify Docker is running (fail fast with an actionable message).
#   2. Start the DB fixtures + apply migrations *only if* Postgres is not
#      already reachable (delegates to the idempotent scripts/setup.sh).
#   3. Build the UI bundle *only if* ui/dist has no built index.html.
#
# Nothing here is destructive: it never runs `down -v` or resets volumes, so it
# is safe against a shared local Postgres.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Dev config. `make dev` exports this too, but set a default so the script is
# self-contained when run directly — a raw `uv run python -m jentic_one` /
# alembic invocation otherwise fails with a missing `databases` field.
export JENTIC_CONFIG_FILE="${JENTIC_CONFIG_FILE:-config/local.yaml}"

COMPOSE_FILE="docker/local-setup/docker-compose.yaml"

# ── 1. Docker ────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: 'docker' not found on PATH. Install Docker Desktop (macOS) or" >&2
    echo "       the Docker engine (Linux) and try again." >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is installed but not running." >&2
    case "$(uname -s)" in
        Darwin) echo "       Start Docker Desktop (e.g. 'open -a Docker'), wait for it to" >&2
                echo "       report ready, then re-run 'make dev'." >&2 ;;
        *)      echo "       Start the Docker daemon (e.g. 'sudo systemctl start docker')," >&2
                echo "       then re-run 'make dev'." >&2 ;;
    esac
    exit 1
fi

# ── 2. Database fixtures + migrations (only if DB unreachable) ───────────────
db_reachable() {
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U postgres -d jentic -tAc 'SELECT 1' >/dev/null 2>&1
}

if db_reachable; then
    echo "==> Postgres already reachable — skipping fixtures start."
    echo "==> Ensuring migrations are up to date…"
    migration_failed=0
    for name in registry control admin; do
        if ! uv run alembic -n "$name" upgrade head; then
            echo "    ERROR: $name migration failed"
            migration_failed=1
        fi
    done
    if [ "$migration_failed" -ne 0 ]; then
        echo "ERROR: One or more migrations failed. See output above." >&2
        exit 1
    fi
else
    echo "==> Postgres not reachable — starting fixtures…"
    ./scripts/setup.sh
fi

# ── 3. UI bundle (only if not already built) ─────────────────────────────────
if [ -f "ui/dist/index.html" ]; then
    echo "==> ui/dist already built — skipping UI build."
elif command -v node >/dev/null 2>&1; then
    echo "==> ui/dist not built — building the UI bundle…"
    make ui-build
else
    echo "WARNING: node not found and ui/dist is empty — the SPA at /app will" >&2
    echo "         not be served. Install Node.js and run 'make ui-build' to" >&2
    echo "         enable the UI (the backend API still runs without it)." >&2
fi

echo ""
echo "==> Preflight complete. Starting the app…"
