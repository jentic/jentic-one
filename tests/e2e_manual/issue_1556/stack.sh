#!/usr/bin/env bash
# Isolated local stack for the issue #1556 agent-lane e2e loop.
#
#   stack.sh up   [on|off]   start Postgres, upstream, fake AS, app, broker
#   stack.sh down            stop everything this script started
#
# "on" (default) runs with control.connect.manual_flows_enabled: true;
# "off" runs the gate-OFF regression pass against its own database.
# Ports (override via env): app 55600, broker 55601, upstream 55602,
# fake AS 55603, one port per upstream API from 55620, Postgres 55460 (container pg-e2e-lanes). State lives in
# $E2E_DIR (default /tmp/e2e-lanes).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
E2E_DIR="${E2E_DIR:-/tmp/e2e-lanes}"
PG_PORT="${PG_PORT:-55460}"
PG_CONTAINER="${PG_CONTAINER:-pg-e2e-lanes}"
APP_PORT="${APP_PORT:-55600}"
BROKER_PORT="${BROKER_PORT:-55601}"
UPSTREAM_PORT="${UPSTREAM_PORT:-55602}"
FAKE_AS_PORT="${FAKE_AS_PORT:-55603}"
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

# A fresh database per pass: dropped and re-created with the init script.
fresh_db() {
  local db="$1"
  psql_c -d postgres -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" >/dev/null
  psql_c -d postgres -c "CREATE DATABASE $db" >/dev/null
  # Roles already exist after the first init; only the schema part re-runs.
  grep -v '^CREATE ROLE' "$ROOT/docker/local-setup/init-schemas.sql" | psql_c -d "$db" >/dev/null
}

write_config() {
  local gate="$1" db="$2" cfg="$3"
  local ttl_line=""
  cat >"$cfg" <<EOF
databases:
  registry: {host: localhost, port: $PG_PORT, name: $db, user: registry_user, password: registry_pass, schema_name: registry}
  control: {host: localhost, port: $PG_PORT, name: $db, user: control_user, password: control_pass, schema_name: control}
  admin: {host: localhost, port: $PG_PORT, name: $db, user: admin_user, password: admin_pass, schema_name: admin}
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
    state_secret: "e2e-lanes-state-secret-0123456789abcdef"  # pragma: allowlist secret
  providers:
    direct_oauth2:
      kind: direct_oauth2
vendors:
  entries:
    e2evendor:
      vendor: e2e-vendor.test/api
      display_name: E2E Vendor
      flows:
        - kind: authorization_code
          client_id: vendor-client
          client_secret: vendor-secret  # pragma: allowlist secret
          authorize_url: "https://127.0.0.1:$FAKE_AS_PORT/authorize"
          token_url: "https://127.0.0.1:$FAKE_AS_PORT/token"
      scopes:
        - {name: read, classification: read, default: true, description: Read}
        - {name: write, classification: write, default: false, description: Write}
control:
  connect:
    manual_flows_enabled: $gate
broker:
  egress:
    allowed_private_subnets: ["127.0.0.0/8"]
ingest:
  egress:
    allowed_private_subnets: ["127.0.0.0/8"]
catalog:
  manifest_url: "http://127.0.0.1:$UPSTREAM_PORT/apis.json"
  manifest_max_age_seconds: 5
auth:
  id_signing:
    - kid: "e2e-lanes-key"
      private_key_pem: |
$(sed 's/^/        /' "$E2E_DIR/tls/id_signing.pem")
observability:
  metrics: {exporter: none}
  tracing: {exporter: none}
EOF
}

# A throwaway CA + a 127.0.0.1 server cert for the fake AS (https only: the
# shared-app registration API refuses http endpoints). The app and broker
# trust it through SSL_CERT_FILE (certifi's bundle plus this CA).
ensure_tls() {
  local tls="$E2E_DIR/tls"
  [ -f "$tls/bundle.pem" ] && [ -f "$tls/id_signing.pem" ] && return 0
  mkdir -p "$tls"
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=e2e-lanes CA" \
    -keyout "$tls/ca.key" -out "$tls/ca.pem" >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes -subj "/CN=127.0.0.1" \
    -keyout "$tls/server.key" -out "$tls/server.csr" >/dev/null 2>&1
  printf 'subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n' >"$tls/ext.cnf"
  openssl x509 -req -in "$tls/server.csr" -CA "$tls/ca.pem" -CAkey "$tls/ca.key" \
    -CAcreateserial -days 30 -extfile "$tls/ext.cnf" -out "$tls/server.pem" >/dev/null 2>&1
  (cd "$ROOT" && cat "$(uv run python -c 'import certifi; print(certifi.where())')" "$tls/ca.pem") \
    >"$tls/bundle.pem"
  # The ES256 ID-token signing key, generated per checkout (never committed).
  openssl ecparam -name prime256v1 -genkey -noout -out "$tls/id_signing.pem" >/dev/null 2>&1
}

start_bg() {
  local name="$1"
  shift
  (cd "$ROOT" && nohup "$@" </dev/null >"$E2E_DIR/$name.log" 2>&1 & echo $! >"$E2E_DIR/$name.pid")
}

wait_http() {
  local url="$1" name="$2"
  for _ in $(seq 1 90); do
    # shellcheck disable=SC2086 # $url may carry extra curl flags
    curl -fsS $url >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "$name did not become ready ($url); log:" >&2
  tail -50 "$E2E_DIR/$name.log" >&2
  exit 1
}

down() {
  for name in broker app fake_as upstream; do
    if [ -f "$E2E_DIR/$name.pid" ]; then
      kill "$(cat "$E2E_DIR/$name.pid")" 2>/dev/null || true
      rm -f "$E2E_DIR/$name.pid"
    fi
  done
  # uv run spawns a python child; reap anything still bound to our ports.
  for port in "$APP_PORT" "$BROKER_PORT" "$UPSTREAM_PORT" "$FAKE_AS_PORT" $(seq 55620 55640); do
    for pid in $(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null); do kill "$pid" 2>/dev/null || true; done
  done
}

up() {
  local gate_mode="${1:-on}" gate db
  if [ "$gate_mode" = "off" ]; then gate=false db=jentic_lanes_off; else gate=true db=jentic_lanes_on; fi
  down
  ensure_pg
  ensure_tls
  fresh_db "$db"
  local cfg="$E2E_DIR/config-$gate_mode.yaml"
  write_config "$gate" "$db" "$cfg"
  echo "$cfg" >"$E2E_DIR/current-config"
  (cd "$ROOT" && JENTIC_CONFIG_FILE="$cfg" uv run python -m jentic_one.migrations.run >"$E2E_DIR/migrate.log" 2>&1) \
    || { tail -40 "$E2E_DIR/migrate.log"; exit 1; }
  start_bg upstream env PORT="$UPSTREAM_PORT" FAKE_AS_URL="https://127.0.0.1:$FAKE_AS_PORT" \
    uv run python -m tests.e2e_manual.issue_1556.upstream
  start_bg fake_as env HOST=127.0.0.1 PORT="$FAKE_AS_PORT" SSL_CERTFILE="$E2E_DIR/tls/server.pem" \
    SSL_KEYFILE="$E2E_DIR/tls/server.key" uv run python -m tests.harness.fake_oauth_as
  start_bg app env JENTIC_CONFIG_FILE="$cfg" SSL_CERT_FILE="$E2E_DIR/tls/bundle.pem" uv run python -m tests.e2e_manual.issue_1556.run_app
  start_bg broker env JENTIC_CONFIG_FILE="$cfg" SSL_CERT_FILE="$E2E_DIR/tls/bundle.pem" JENTIC__APPS=broker JENTIC__SERVER__PORT="$BROKER_PORT" \
    uv run python -m tests.e2e_manual.issue_1556.run_app
  wait_http "http://127.0.0.1:$UPSTREAM_PORT/healthz" upstream
  wait_http "https://127.0.0.1:$FAKE_AS_PORT/healthz --cacert $E2E_DIR/tls/ca.pem" fake_as
  wait_http "http://127.0.0.1:$APP_PORT/admin/health" app
  wait_http "http://127.0.0.1:$BROKER_PORT/health" broker
  echo "stack up (gate $gate_mode): app :$APP_PORT broker :$BROKER_PORT upstream :$UPSTREAM_PORT fake AS :$FAKE_AS_PORT db $db"
}

case "${1:-}" in
  up) up "${2:-on}" ;;
  down) down ;;
  *) echo "usage: $0 up [on|off] | down" >&2; exit 2 ;;
esac
