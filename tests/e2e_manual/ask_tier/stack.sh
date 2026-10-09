#!/usr/bin/env bash
# Isolated local stack for the require-approval (ask tier) e2e loop.
#
#   stack.sh up        build the CLI, start Postgres, the upstream, the app and a standalone broker
#   stack.sh down      stop everything this script started (the Postgres container too)
#   stack.sh restart   SIGKILL then restart the app and broker (the worker-crash scenario)
#   stack.sh stop|start  stop / start the app and broker only (the migration scenario)
#
# The app is the combined deployment (admin + control + registry + broker +
# MCP mount + job worker); the standalone broker (JENTIC__APPS=broker) runs
# its own worker and is what the agents call. Ports (override via env): app
# 55521, broker 55522, upstream 55523, Postgres 55520 (container pg-ask-tier).
# State lives in $E2E_DIR (default /tmp/ask-e2e). APPROVAL_TTL_S shortens the
# approval window (default 24 h) for the UI spec's expiry case.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
E2E_DIR="${E2E_DIR:-/tmp/ask-e2e}"
PG_PORT="${PG_PORT:-55520}"
PG_CONTAINER="${PG_CONTAINER:-pg-ask-tier}"
APP_PORT="${APP_PORT:-55521}"
BROKER_PORT="${BROKER_PORT:-55522}"
UPSTREAM_PORT="${UPSTREAM_PORT:-55523}"
DB="${E2E_DB:-jentic_ask}"
APPROVAL_TTL_S="${APPROVAL_TTL_S:-86400}"
mkdir -p "$E2E_DIR"

psql_c() { docker exec -i "$PG_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

ensure_pg() {
  if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$PG_CONTAINER" -e POSTGRES_DB=jentic -e POSTGRES_USER=postgres \
      -e POSTGRES_PASSWORD=postgres -p "$PG_PORT:5432" \
      -v "$ROOT/docker/local-setup/init-schemas.sql:/docker-entrypoint-initdb.d/init-schemas.sql:ro" \
      postgres:16 >/dev/null
  fi
  local ok=0
  for _ in $(seq 1 60); do
    if psql_c -d jentic -tAc 'SELECT 1' >/dev/null 2>&1; then ok=$((ok + 1)); else ok=0; fi
    [ "$ok" -ge 3 ] && return 0
    sleep 1
  done
  echo "postgres not ready" >&2
  exit 1
}

fresh_db() {
  psql_c -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
  psql_c -d postgres -c "CREATE DATABASE $DB" >/dev/null
  # Roles exist after the container's first init; only the schema part re-runs.
  grep -v '^CREATE ROLE' "$ROOT/docker/local-setup/init-schemas.sql" | psql_c -d "$DB" >/dev/null
}

ensure_keys() {
  [ -f "$E2E_DIR/id_signing.pem" ] && return 0
  # The ES256 ID-token signing key, generated per run directory (never committed).
  openssl ecparam -name prime256v1 -genkey -noout -out "$E2E_DIR/id_signing.pem" >/dev/null 2>&1
}

write_config() {
  local cfg="$1"
  cat >"$cfg" <<CFG
databases:
  registry: {host: localhost, port: $PG_PORT, name: $DB, user: registry_user, password: registry_pass, schema_name: registry}
  control: {host: localhost, port: $PG_PORT, name: $DB, user: control_user, password: control_pass, schema_name: control}
  admin: {host: localhost, port: $PG_PORT, name: $DB, user: admin_user, password: admin_pass, schema_name: admin}
runtime: {debug: true, log_level: INFO}
logging: {file_enabled: false}
server:
  host: 127.0.0.1
  port: $APP_PORT
  reload: false
  public_base_url: "http://127.0.0.1:$APP_PORT"
  mcp:
    enabled: true
    broker_url: "http://127.0.0.1:$BROKER_PORT"
credentials:
  encryption:
    active_id: v1
    entries:
      - id: v1
        material: "vF7VWq2NJLr+uGDBFy9boIXfSJJIzqnTSF7iDDMKR5U="  # pragma: allowlist secret
  connect:
    state_secret: "ask-e2e-state-secret-0123456789abcdef"  # pragma: allowlist secret
# Pinned (not the ephemeral dev secrets) so tokens survive the crash-restart scenario.
admin:
  auth:
    jwt_secret: "ask-e2e-jwt-secret-0123456789abcdef0123456789"  # pragma: allowlist secret
  invite:
    pepper: "ask-e2e-invite-pepper-0123456789abcdef"  # pragma: allowlist secret
control:
  connect:
    manual_flows_enabled: true
broker:
  egress:
    allowed_private_subnets: ["127.0.0.0/8"]
ingest:
  egress:
    allowed_private_subnets: ["127.0.0.0/8"]
execution_approvals:
  max_pending_per_agent: 3
  ttl_seconds: $APPROVAL_TTL_S
worker:
  # Short enough that the worker-crash scenario reclaims its job quickly.
  visibility_timeout_s: 20
auth:
  id_signing:
    - kid: "ask-e2e-key"
      private_key_pem: |
$(sed 's/^/        /' "$E2E_DIR/id_signing.pem")
observability:
  metrics: {exporter: none}
  tracing: {exporter: none}
CFG
}

start_bg() {
  local name="$1"
  shift
  # Detached from the caller's stdout/stderr so a piped `stack.sh up | tail` returns.
  (cd "$ROOT" && exec nohup "$@" </dev/null >>"$E2E_DIR/$name.log" 2>&1) </dev/null >/dev/null 2>&1 &
  echo $! >"$E2E_DIR/$name.pid"
}

wait_http() {
  local url="$1" name="$2"
  for _ in $(seq 1 90); do
    curl -fsS "$url" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "$name did not become ready ($url); log:" >&2
  tail -50 "$E2E_DIR/$name.log" >&2
  exit 1
}

stop_named() {
  for name in "$@"; do
    if [ -f "$E2E_DIR/$name.pid" ]; then
      kill "$(cat "$E2E_DIR/$name.pid")" 2>/dev/null || true
      rm -f "$E2E_DIR/$name.pid"
    fi
  done
}

reap_ports() {
  # uv run spawns a python child; reap anything still bound to our ports.
  for port in "$@"; do
    for pid in $(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null); do kill "$pid" 2>/dev/null || true; done
  done
}

start_services() {
  local cfg="$E2E_DIR/config.yaml"
  start_bg app env JENTIC_CONFIG_FILE="$cfg" uv run python -m jentic_one
  start_bg broker env JENTIC_CONFIG_FILE="$cfg" JENTIC__APPS=broker JENTIC__SERVER__PORT="$BROKER_PORT" \
    JENTIC__BROKER__JOBS_API_BASE_URL="http://127.0.0.1:$APP_PORT" uv run python -m jentic_one
  wait_http "http://127.0.0.1:$APP_PORT/admin/health" app
  wait_http "http://127.0.0.1:$BROKER_PORT/health" broker
}

down() {
  stop_named broker app upstream
  reap_ports "$APP_PORT" "$BROKER_PORT" "$UPSTREAM_PORT"
}

up() {
  down
  (cd "$ROOT/cli" && GOWORK=off go build -o "$E2E_DIR/jentic" ./cmd/jentic) >"$E2E_DIR/build.log" 2>&1 \
    || { tail -30 "$E2E_DIR/build.log"; exit 1; }
  # The review_url opens the web UI, which the app serves from ui/dist.
  if [ ! -f "$ROOT/ui/dist/index.html" ] || [ "${E2E_BUILD_UI:-0}" = "1" ]; then
    (cd "$ROOT/ui" && npm run build) >"$E2E_DIR/ui-build.log" 2>&1 || { tail -30 "$E2E_DIR/ui-build.log"; exit 1; }
    # vite empties ui/dist, including the tracked placeholder.
    (cd "$ROOT" && git checkout -- ui/dist/.gitkeep 2>/dev/null) || true
  fi
  rm -rf "$E2E_DIR"/home-* "$E2E_DIR"/app.log "$E2E_DIR"/broker.log "$E2E_DIR"/upstream.log
  ensure_pg
  ensure_keys
  fresh_db
  write_config "$E2E_DIR/config.yaml"
  (cd "$ROOT" && JENTIC_CONFIG_FILE="$E2E_DIR/config.yaml" uv run python -m jentic_one.migrations.run \
    >"$E2E_DIR/migrate.log" 2>&1) || { tail -40 "$E2E_DIR/migrate.log"; exit 1; }
  start_bg upstream env PORT="$UPSTREAM_PORT" ASK_UPSTREAM_URL="http://127.0.0.1:$UPSTREAM_PORT" \
    uv run python -m tests.e2e_manual.ask_tier.upstream
  wait_http "http://127.0.0.1:$UPSTREAM_PORT/specs/ask.json" upstream
  start_services
  echo "stack up: app :$APP_PORT broker :$BROKER_PORT upstream :$UPSTREAM_PORT db $DB (pg :$PG_PORT)"
}

case "${1:-}" in
  up) up ;;
  down)
    down
    if [ "${KEEP_PG:-0}" != "1" ]; then docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true; fi
    ;;
  restart)
    # A hard stop (SIGKILL) so an in-flight job is left RUNNING, as a crashed worker leaves it.
    for name in app broker; do
      [ -f "$E2E_DIR/$name.pid" ] && kill -9 "$(cat "$E2E_DIR/$name.pid")" 2>/dev/null || true
    done
    for port in "$APP_PORT" "$BROKER_PORT"; do
      for pid in $(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
    done
    sleep 1
    start_services
    ;;
  stop)
    stop_named broker app
    reap_ports "$APP_PORT" "$BROKER_PORT"
    ;;
  start) start_services ;;
  *) echo "usage: $0 up | down | restart | stop | start" >&2; exit 2 ;;
esac
