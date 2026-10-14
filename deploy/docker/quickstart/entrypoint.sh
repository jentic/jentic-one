#!/usr/bin/env bash
# Quickstart entrypoint: migrate, then run the broker and the control plane in
# one container. Any arguments are exec'd instead (e.g. a one-off
# `python -m jentic_one create-admin …` via `docker run --rm -it … <cmd>`).
set -euo pipefail

if [[ $# -gt 0 ]]; then
  exec "$@"
fi

echo "jentic-quickstart: running migrations"
python -m jentic_one.migrations.run

# The broker must run as the sole surface of its process, so it gets its own
# process on its own port; the control plane keeps the image's default apps.
echo "jentic-quickstart: starting broker on :${JENTIC_QUICKSTART_BROKER_PORT}"
JENTIC__APPS=broker JENTIC__SERVER__PORT="${JENTIC_QUICKSTART_BROKER_PORT}" \
  python -m jentic_one &
broker_pid=$!

echo "jentic-quickstart: starting control plane on :8000"
python -m jentic_one &
app_pid=$!

# Forward stop signals to both, and take the container down if either exits —
# a half-running stack would otherwise look alive to `docker ps`.
# A requested stop (`docker stop`) exits 0; an unexpected process exit
# propagates its status.
stopping=0
shutdown() { kill -TERM "$broker_pid" "$app_pid" 2>/dev/null || true; }
trap 'stopping=1; shutdown' TERM INT

status=0
wait -n "$broker_pid" "$app_pid" || status=$?
shutdown
wait "$broker_pid" "$app_pid" 2>/dev/null || true
[[ "$stopping" == "1" ]] && exit 0
exit "$status"
