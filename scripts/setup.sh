#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PG_PORT="${JENTIC_PG_PORT:-5432}"

echo "==> Starting Docker services..."
docker compose -f docker/local-setup/docker-compose.yaml up -d

echo "==> Waiting for database to become healthy..."
MAX_WAIT=60
elapsed=0
# Probe over TCP (-h 127.0.0.1), not the unix socket, and with a real query
# rather than pg_isready: on first boot the Postgres image runs a temporary
# internal server for init scripts with listen_addresses='' — it serves the
# unix socket (so a socket SELECT 1 can succeed!) but never TCP, then shuts
# down before the real server starts ("the database system is shutting
# down" mid-setup). Only the final server accepts TCP connections.
until docker compose -f docker/local-setup/docker-compose.yaml exec -T db \
    psql -h 127.0.0.1 -U postgres -d jentic -tAc 'SELECT 1' >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$MAX_WAIT" ]; then
        echo "ERROR: db did not become ready within ${MAX_WAIT}s"
        exit 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done
echo "    db is ready"

echo "==> Ensuring schemas exist..."
for schema in registry control admin; do
    docker compose -f docker/local-setup/docker-compose.yaml exec -T db \
        psql -h 127.0.0.1 -U postgres -d jentic -c \
        "CREATE SCHEMA IF NOT EXISTS ${schema};" >/dev/null
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
