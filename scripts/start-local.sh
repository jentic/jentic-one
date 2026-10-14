#!/usr/bin/env bash
# Run the combined app (:8000) and the broker (:8100) together for local testing.
#
# Each server runs in its own process group, so stopping one stops its uvicorn
# reloader and workers too. When either server exits, or on Ctrl-C, both groups
# get SIGTERM, then SIGKILL after a short grace period: uvicorn's graceful
# shutdown waits for open connections (an MCP client keeps one open), so a
# plain TERM can otherwise leave a server holding its port after this script
# has gone.
#
# Usage: scripts/start-local.sh   (normally via `make start-local`)
# Env:   START_LOCAL_APP_PORT / START_LOCAL_BROKER_PORT  ports (default 8000 / 8100)
#        START_LOCAL_GRACE_SECONDS  seconds to wait after SIGTERM (default 5)
set -u

APP_PORT="${START_LOCAL_APP_PORT:-8000}"
BROKER_PORT="${START_LOCAL_BROKER_PORT:-8100}"
GRACE="${START_LOCAL_GRACE_SECONDS:-5}"

port_holder() {
	lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR == 2 { print $1 " (pid " $2 ")" }'
}

busy=0
for port in "$APP_PORT" "$BROKER_PORT"; do
	holder="$(port_holder "$port")"
	if [ -n "$holder" ]; then
		echo "start-local: port $port is in use by $holder; stop it first." >&2
		busy=1
	fi
done
[ "$busy" -eq 0 ] || exit 1

# Job control gives each background job its own process group. It is on only
# while the servers start: left on, every foreground command below (the wait
# loop's sleep) would take the terminal, and Ctrl-C would reach that command
# instead of this script.
set -m
JENTIC__SERVER__PORT="$APP_PORT" uv run python -m jentic_one &
app_pid=$!
JENTIC__APPS=broker JENTIC__SERVER__PORT="$BROKER_PORT" \
	JENTIC__BROKER__JOBS_API_BASE_URL="http://127.0.0.1:$APP_PORT" \
	uv run python -m jentic_one &
broker_pid=$!
set +m

group_alive() {
	kill -0 -- "-$1" 2>/dev/null
}

stopping=0
stop_all() {
	[ "$stopping" -eq 0 ] || return
	stopping=1
	trap '' INT TERM HUP
	for pid in "$app_pid" "$broker_pid"; do
		kill -TERM -- "-$pid" 2>/dev/null
	done
	waited=0
	while [ "$waited" -lt "$GRACE" ]; do
		if ! group_alive "$app_pid" && ! group_alive "$broker_pid"; then
			return
		fi
		sleep 1
		waited=$((waited + 1))
	done
	for pid in "$app_pid" "$broker_pid"; do
		if group_alive "$pid"; then
			echo "start-local: process group $pid did not stop in ${GRACE}s; killing it." >&2
			kill -KILL -- "-$pid" 2>/dev/null
		fi
	done
}

trap 'stop_all; exit 130' INT
trap 'stop_all; exit 143' TERM
trap 'stop_all; exit 129' HUP
trap 'stop_all' EXIT

# Stop both as soon as either exits (e.g. a port taken after the check above).
while kill -0 "$app_pid" 2>/dev/null && kill -0 "$broker_pid" 2>/dev/null; do
	sleep 1
done
if ! kill -0 "$app_pid" 2>/dev/null; then
	echo "start-local: the combined app exited; stopping the broker." >&2
else
	echo "start-local: the broker exited; stopping the combined app." >&2
fi
exit 1
