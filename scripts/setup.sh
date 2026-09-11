#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PG_PORT="${JENTIC_PG_PORT:-5432}"

COMPOSE_FILE="docker/local-setup/docker-compose.yaml"

# Run psql inside the db container against the jentic database.
psql_db() {
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U postgres -d jentic "$@"
}

echo "==> Starting Docker services..."
docker compose -f "$COMPOSE_FILE" up -d

echo "==> Waiting for database to become healthy..."
MAX_WAIT="${JENTIC_DB_MAX_WAIT:-60}"
# On first boot the Postgres image runs a temporary internal server for init
# scripts, then shuts it down and starts the real one. That temporary server
# answers real queries on the container's unix socket, so a single successful
# `SELECT 1` can land inside the restart window — and the next psql dies with
# "FATAL: the database system is shutting down" (or "is starting up", or
# connection refused). Treat all of those as not-yet-ready: require several
# consecutive successful probes, and only fail once the deadline passes.
REQUIRED_OK=3
SECONDS=0
ok=0
while [ "$ok" -lt "$REQUIRED_OK" ]; do
    if psql_db -tAc 'SELECT 1' >/dev/null 2>&1; then
        ok=$((ok + 1))
    else
        ok=0
        if [ "$SECONDS" -ge "$MAX_WAIT" ]; then
            echo "ERROR: db did not become ready within ${MAX_WAIT}s; last probe:"
            psql_db -tAc 'SELECT 1' >/dev/null || true
            exit 1
        fi
    fi
    sleep 1
done
echo "    db is ready (${REQUIRED_OK} consecutive probes over ${SECONDS}s)"

# Run a psql statement, retrying transient startup/shutdown/connection errors
# until the shared MAX_WAIT deadline (SECONDS keeps counting from the wait
# above). On timeout, re-run unsuppressed so the real error reaches the log.
retry_psql() {
    local what="$1"
    shift
    until psql_db "$@" >/dev/null 2>&1; do
        if [ "$SECONDS" -ge "$MAX_WAIT" ]; then
            echo "ERROR: ${what} still failing after ${MAX_WAIT}s; last attempt:"
            psql_db "$@" >/dev/null || true
            exit 1
        fi
        sleep 1
    done
}

echo "==> Ensuring schemas exist..."
for schema in registry control admin; do
    retry_psql "CREATE SCHEMA ${schema}" -c "CREATE SCHEMA IF NOT EXISTS ${schema};"
    echo "    schema '$schema' ensured"
done

echo "==> Running migrations..."
migration_failed=0
for name in registry control admin; do
    if ! uv run alembic -n "$name" upgrade head; then
        echo "    ERROR: $name migration failed"
        migration_failed=1
    fi
done

if [ "$migration_failed" -ne 0 ]; then
    echo ""
    echo "ERROR: One or more migrations failed. See output above."
    exit 1
fi

echo ""
echo "==> Setup complete. Database endpoint:"
echo "    localhost:${PG_PORT}/jentic (schemas: registry, control, admin)"
echo ""
echo "    User: postgres / Password: postgres (default)"
if [ "$PG_PORT" != "5432" ]; then
    echo ""
    echo "    NOTE: Postgres is published on ${PG_PORT} (not the default 5432)."
    echo "    Point the app at it, e.g.:"
    echo "      JENTIC__DATABASES__REGISTRY__PORT=${PG_PORT} \\"
    echo "      JENTIC__DATABASES__CONTROL__PORT=${PG_PORT} \\"
    echo "      JENTIC__DATABASES__ADMIN__PORT=${PG_PORT} make start-app"
fi
