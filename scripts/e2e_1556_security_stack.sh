#!/usr/bin/env bash
# Isolated stack for the connect-flow security regression suite (tests/e2e/).
#
#   scripts/e2e_1556_security_stack.sh up     # Postgres + migrations + app + broker + upstreams
#   scripts/e2e_1556_security_stack.sh down   # stop everything (container removed)
#
# Everything runs on its own ports so other local stacks keep working:
#   Postgres  pg-e2e-sec           :55480
#   app       control/registry/... :55800 (combined app, as `make start-app`)
#   broker    JENTIC__APPS=broker  :55801 (as `make start-local`)
#   upstream  smoke_upstream       :55802 (the legitimate API host)
#   recorder  tests.harness.request_recorder :55803 (records every request + headers)
#
# State (config, logs, recorder log, pids) lives under $E2E_SEC_DIR (default
# /tmp/e2e-sec), outside the repo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${E2E_SEC_DIR:-/tmp/e2e-sec}"
PG_NAME="${E2E_SEC_PG_NAME:-pg-e2e-sec}"
PG_PORT="${E2E_SEC_PG_PORT:-55480}"
APP_PORT="${E2E_SEC_APP_PORT:-55800}"
BROKER_PORT="${E2E_SEC_BROKER_PORT:-55801}"
UPSTREAM_PORT="${E2E_SEC_UPSTREAM_PORT:-55802}"
RECORDER_PORT="${E2E_SEC_RECORDER_PORT:-55803}"

write_config() {
  mkdir -p "$DIR/logs"
  cat >"$DIR/config.yaml" <<YAML
databases:
  registry: {host: localhost, port: $PG_PORT, name: jentic, user: registry_user, password: registry_pass, schema_name: registry}
  control: {host: localhost, port: $PG_PORT, name: jentic, user: control_user, password: control_pass, schema_name: control}
  admin: {host: localhost, port: $PG_PORT, name: jentic, user: admin_user, password: admin_pass, schema_name: admin}
runtime:
  debug: true
  log_level: DEBUG
logging:
  file_enabled: true
  file_dir: $DIR/logs
server:
  host: 127.0.0.1
  port: $APP_PORT
  reload: false
credentials:
  encryption:
    active_id: v1
    entries:
      - id: v1
        material: "vF7VWq2NJLr+uGDBFy9boIXfSJJIzqnTSF7iDDMKR5U="  # pragma: allowlist secret
auth:
  id_signing:
    - kid: "local-dev-key"
      private_key_pem: |
        -----BEGIN EC PRIVATE KEY-----
        MHcCAQEEIBG7o+PPPIdPqMK4RwNWnj+UaW8fZFzxw7oZD5XFqW5CoAoGCCqGSM49
        AwEHoUQDQgAElriD/rpklmqTXbUOa9uLHAB2l+qr+DoeDmmykYLGblbxs+a1qvxB
        369JIs2Ej4zMfkjBTGES38wMDs1J+PJG6g==
        -----END EC PRIVATE KEY-----
control:
  connect:
    manual_flows_enabled: true
broker:
  egress:
    allowed_private_subnets: ["127.0.0.0/8"]
ingest:
  egress:
    allowed_private_subnets: ["127.0.0.0/8"]
observability:
  metrics:
    exporter: prometheus
  tracing:
    exporter: none
YAML
}

wait_http() {
  local url="$1" what="$2"
  for _ in $(seq 1 90); do
    if curl -fsS "$url" >/dev/null 2>&1; then echo "  $what ready"; return 0; fi
    sleep 1
  done
  echo "$what did not become ready ($url)" >&2
  return 1
}

start_bg() {
  local name="$1"; shift
  cd "$ROOT"
  nohup "$@" </dev/null >"$DIR/logs/$name.out" 2>&1 &
  echo $! >"$DIR/$name.pid"
}

up() {
  write_config
  if ! docker ps --format '{{.Names}}' | grep -qx "$PG_NAME"; then
    docker rm -f "$PG_NAME" >/dev/null 2>&1 || true
    docker run -d --name "$PG_NAME" -e POSTGRES_DB=jentic -e POSTGRES_USER=postgres \
      -e POSTGRES_PASSWORD=postgres -p "$PG_PORT:5432" \
      -v "$ROOT/docker/local-setup/init-schemas.sql:/docker-entrypoint-initdb.d/init-schemas.sql:ro" \
      postgres:16 >/dev/null
  fi
  local ok=0
  for _ in $(seq 1 60); do
    if docker exec "$PG_NAME" psql -U postgres -d jentic -tAc 'SELECT 1' >/dev/null 2>&1; then
      ok=$((ok + 1)); [ "$ok" -ge 3 ] && break
    else ok=0; fi
    sleep 1
  done
  export JENTIC_CONFIG_FILE="$DIR/config.yaml"
  (cd "$ROOT" && uv run python -m jentic_one.migrations.run >"$DIR/logs/migrate.out" 2>&1)
  echo "  migrations applied"

  start_bg upstream env PORT="$UPSTREAM_PORT" HOST=127.0.0.1 \
    SMOKE_UPSTREAM_PUBLIC_URL="http://127.0.0.1:$UPSTREAM_PORT" \
    uv run python -m tests.harness.smoke_upstream
  start_bg recorder uv run python -m tests.harness.request_recorder \
    --port "$RECORDER_PORT" --log "$DIR/recorder.jsonl"
  start_bg app env JENTIC_CONFIG_FILE="$DIR/config.yaml" uv run python -m jentic_one
  start_bg broker env JENTIC_CONFIG_FILE="$DIR/config.yaml" JENTIC__APPS=broker \
    JENTIC__SERVER__PORT="$BROKER_PORT" JENTIC__LOGGING__FILE_NAME=broker.log \
    uv run python -m jentic_one

  wait_http "http://127.0.0.1:$UPSTREAM_PORT/health" upstream
  wait_http "http://127.0.0.1:$RECORDER_PORT/__recorder/health" recorder
  wait_http "http://127.0.0.1:$APP_PORT/admin/health" app
  wait_http "http://127.0.0.1:$BROKER_PORT/health" broker
}

down() {
  for name in broker app recorder upstream; do
    if [ -f "$DIR/$name.pid" ]; then
      pid="$(cat "$DIR/$name.pid")"
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
      rm -f "$DIR/$name.pid"
    fi
  done
  for port in "$APP_PORT" "$BROKER_PORT" "$UPSTREAM_PORT" "$RECORDER_PORT"; do
    lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null | xargs kill -TERM 2>/dev/null || true
  done
  docker rm -f "$PG_NAME" >/dev/null 2>&1 || true
  echo "  stopped"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  config) write_config; echo "$DIR/config.yaml" ;;
  *) echo "usage: $0 up|down|config" >&2; exit 2 ;;
esac
