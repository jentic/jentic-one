# syntax=docker/dockerfile:1
#
# The loopback bridge of `jentic run --container` (experimental). Not
# published; build it locally:
#
#   docker build -f deploy/docker/run-bridge.Dockerfile -t jentic-run-bridge:local deploy/docker
#
# One image, two roles, both run unprivileged with a read-only root:
#
#   relay <host> <port>...   listens on /run/jentic-relay/<port>.sock and
#                            dials <host>:<port> (the host's Jentic)
#   loopback <port>...       owns the agent's network namespace: listens on
#                            127.0.0.1:<port> and dials the relay's socket
#
# ready-relay / ready-loopback <port>... exit 0 once every listener is up.
# A role exits non-zero as soon as one of its forwarders exits, so the CLI
# notices and ends the session instead of leaving a port free to take.
FROM alpine:3.20

RUN apk add --no-cache socat \
 && mkdir -p /run/jentic-relay \
 && chown 65534:65534 /run/jentic-relay \
 && chmod 0700 /run/jentic-relay

COPY --chmod=0755 <<'SCRIPT' /usr/local/bin/jentic-bridge
#!/bin/sh
set -eu
dir=/run/jentic-relay

valid_port() {
  case "$1" in ''|*[!0-9]*) return 1;; esac
  [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

mode=${1:-}
[ $# -gt 0 ] && shift
case "$mode" in
relay|loopback|ready-relay|ready-loopback) ;;
*) echo "usage: jentic-bridge relay <host> <port>... | loopback <port>... | ready-relay|ready-loopback <port>..." >&2; exit 2;;
esac

host=
if [ "$mode" = relay ]; then
  host=${1:?relay needs a host}; shift
fi
[ $# -gt 0 ] || { echo "jentic-bridge: no ports" >&2; exit 2; }
for port in "$@"; do valid_port "$port" || { echo "jentic-bridge: bad port $port" >&2; exit 2; }; done

case "$mode" in
ready-relay)
  for port in "$@"; do [ -S "$dir/$port.sock" ] || exit 1; done
  exit 0;;
ready-loopback)
  for port in "$@"; do
    hex=$(printf '0100007F:%04X' "$port")
    awk -v want="$hex" 'NR > 1 && $2 == want && $4 == "0A" { found = 1 } END { exit !found }' /proc/net/tcp || exit 1
  done
  exit 0;;
esac

pids=
for port in "$@"; do
  if [ "$mode" = relay ]; then
    socat "UNIX-LISTEN:$dir/$port.sock,fork,unlink-early,mode=0600" "TCP:$host:$port" &
  else
    socat "TCP-LISTEN:$port,bind=127.0.0.1,fork,reuseaddr" "UNIX-CONNECT:$dir/$port.sock" &
  fi
  pids="$pids $!"
done
trap 'kill $pids 2>/dev/null; exit 0' INT TERM
while :; do
  for pid in $pids; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "jentic-bridge: a forwarder exited; stopping" >&2
      kill $pids 2>/dev/null || true
      exit 1
    fi
  done
  sleep 1
done
SCRIPT

USER 65534:65534
ENTRYPOINT ["/usr/local/bin/jentic-bridge"]
