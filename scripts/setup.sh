#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PG_PORT="${JENTIC_PG_PORT:-5432}"

# The migration runner below reads its connection settings from
# JENTIC_CONFIG_FILE (config/local.yaml pins 5432). When JENTIC_PG_PORT moves
# the published port, point every surface at it unless the caller already set
# an explicit per-surface override.
if [ -n "${JENTIC_PG_PORT:-}" ]; then
    export JENTIC__DATABASES__REGISTRY__PORT="${JENTIC__DATABASES__REGISTRY__PORT:-$PG_PORT}"
    export JENTIC__DATABASES__CONTROL__PORT="${JENTIC__DATABASES__CONTROL__PORT:-$PG_PORT}"
    export JENTIC__DATABASES__ADMIN__PORT="${JENTIC__DATABASES__ADMIN__PORT:-$PG_PORT}"
fi

COMPOSE_FILE="docker/local-setup/docker-compose.yaml"
INIT_SQL="docker/local-setup/init-schemas.sql"

# JENTIC_PG_EXTERNAL=1: Postgres is already running (e.g. a CI service
# container on localhost:$PG_PORT, same image and superuser as the compose
# file). Skip compose and talk to it over TCP instead of `docker compose exec`.
EXTERNAL="${JENTIC_PG_EXTERNAL:-0}"

# Run psql against the jentic database (inside the db container by default).
psql_db() {
    if [ "$EXTERNAL" = "1" ]; then
        PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" \
            psql -h "${JENTIC_PG_HOST:-localhost}" -p "$PG_PORT" -U postgres -d jentic "$@"
    else
        docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U postgres -d jentic "$@"
    fi
}

if [ "$EXTERNAL" = "1" ]; then
    echo "==> Using the already-running Postgres on ${JENTIC_PG_HOST:-localhost}:${PG_PORT}"
else
    echo "==> Starting Docker services..."
    docker compose -f "$COMPOSE_FILE" up -d
fi

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
# The compose fixture applies $INIT_SQL (per-surface roles + grants) through
# the image's first-boot entrypoint. An external Postgres has no such hook, so
# apply it here, once: CREATE ROLE is not idempotent, so a server that already
# has the roles is left alone.
if [ "$EXTERNAL" = "1" ]; then
    if [ "$(psql_db -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'registry_user'")" != "1" ]; then
        psql_db -v ON_ERROR_STOP=1 -q -f - < "$INIT_SQL"
        echo "    applied ${INIT_SQL}"
    fi
fi
for schema in registry control admin; do
    retry_psql "CREATE SCHEMA ${schema}" -c "CREATE SCHEMA IF NOT EXISTS ${schema};"
    echo "    schema '$schema' ensured"
done

echo "==> Running migrations..."
# The full runner, not per-database `alembic upgrade`: it orders the databases
# and runs the cross-database upgrade steps (e.g. the service-account
# retirement the admin drop revision requires).
if ! uv run python -m jentic_one.migrations.run; then
    echo ""
    echo "ERROR: Migrations failed. See output above."
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
