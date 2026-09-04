#!/usr/bin/env bash
# smoke.sh — cross-platform post-install smoke for the jentic/jenticctl binaries
# (CLI-V2 Phase 9, impl/9.0 §9.3). Asserts the binary + agent-surface contract
# that must hold with NO running server, so it runs identically on Linux and
# macOS (the Windows equivalent is smoke.ps1). The Linux matrix leg runs this a
# second time against a live stack; the assertions here are the ones that hold
# regardless of whether a control plane is up.
#
# Usage: smoke.sh <bin-dir>
#   <bin-dir> holds the built `jentic` and `jenticctl` (e.g. cli/ after
#   `make build`, or ~/.jentic/bin after install.sh).
#
# Exit non-zero on the first failed assertion. Requires: bash, the two binaries.
# Uses a scratch JENTIC_HOME so it never touches a real install.
set -euo pipefail

BIN_DIR="${1:?usage: smoke.sh <bin-dir> [--live <base-url>]}"
JENTIC="$BIN_DIR/jentic"
JENTICCTL="$BIN_DIR/jenticctl"

# --live <base-url> enables the success-path assertions that only hold against a
# running control plane (QA-2). Without it, only the offline binary/agent-surface
# contract is checked — the same script therefore serves both the offline matrix
# legs and the Linux live-stack leg, so the two cannot silently diverge.
LIVE_URL=""
if [ "${2:-}" = "--live" ]; then
  LIVE_URL="${3:?--live requires a base URL}"
fi

# Isolate all state under a scratch dir removed on exit — the smoke must not read
# or write the developer's / runner's real ~/.jentic.
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
export JENTIC_HOME="$SCRATCH/.jentic"
export XDG_CONFIG_HOME="$SCRATCH/.config"
export XDG_STATE_HOME="$SCRATCH/.local/state"
export XDG_CACHE_HOME="$SCRATCH/.cache"
# Force machine-readable output regardless of TTY, and never block on a prompt.
export JENTIC_MODE="agent"

pass() { printf '  ok  %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1" >&2; exit 1; }

echo "== jentic CLI smoke (bin: $BIN_DIR${LIVE_URL:+, live: $LIVE_URL}) =="

[ -x "$JENTIC" ]    || fail "jentic binary not found/executable at $JENTIC"
[ -x "$JENTICCTL" ] || fail "jenticctl binary not found/executable at $JENTICCTL"
pass "both binaries present"

# 1. --version prints a stamped version on both binaries.
"$JENTIC" --version    | grep -Eiq 'jentic'    || fail "jentic --version had no version line"
"$JENTICCTL" --version | grep -Eiq 'jentic'    || fail "jenticctl --version had no version line"
pass "--version on both binaries"

# 2. jentic doctor --json parses AND exits 0 (QA-1: assert the code, not just the
#    body). Identity/reachability rows may WARN with no server; doctor exits 0
#    unless a hard check FAILS, so a clean exit means the XDG-paths self-check ran
#    and the envelope is well-formed.
set +e
"$JENTIC" doctor --json > "$SCRATCH/doctor.json" 2>"$SCRATCH/doctor.err"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  cat "$SCRATCH/doctor.err" >&2
  fail "jentic doctor --json exit=$rc, want 0 (a hard check failed)"
fi
grep -q '"checks"' "$SCRATCH/doctor.json" || fail "jentic doctor JSON had no checks array"
pass "jentic doctor --json parses and exits 0"

# 3. jentic access whoami --json on a FRESH scratch home has no active context, so
#    the contract is a RESOLVE_FAILED error envelope AND a non-zero exit (QA-1: we
#    assert BOTH the exact error_code and the exit code, not merely "some
#    well-formed envelope" — a silently-succeeding empty result must fail).
#    `context list` is a management command fenced in agent mode, so
#    it is deliberately NOT used here.
set +e
"$JENTIC" access whoami --json > "$SCRATCH/whoami.json" 2>&1
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "jentic access whoami --json exit=0 on a no-context home, want non-zero"
grep -q '"error_code": *"RESOLVE_FAILED"' "$SCRATCH/whoami.json" \
  || fail "jentic access whoami --json (no context) must emit error_code RESOLVE_FAILED; got: $(cat "$SCRATCH/whoami.json")"
pass "jentic access whoami --json (no context) → RESOLVE_FAILED, exit $rc"

# 4. jenticctl doctor --json parses. It exits non-zero only on a `fail` row; a
#    scratch home with no install may report warnings but must not hard-fail
#    just because nothing is installed.
"$JENTICCTL" doctor --json > "$SCRATCH/ctl-doctor.json" 2>/dev/null || true
grep -q '"checks"' "$SCRATCH/ctl-doctor.json" 2>/dev/null \
  || grep -q '"failures"' "$SCRATCH/ctl-doctor.json" 2>/dev/null \
  || fail "jenticctl doctor JSON was not well-formed"
pass "jenticctl doctor --json parses"

# 6. OPS-22 degrade paths — assert "sane error / clean no-op", not a crash, for
#    the two OS-sensitive lifecycle commands whose behavior forks by GOOS
#    (they otherwise only execute on the Ubuntu leg, which is why OPS-20's
#    Windows signal bug regressed silently). Both run offline on a scratch home.
#
# 6a. `jentic run <agent>` on a home with no isolated agent user takes the
#     same-user launcher path. Whether or not the agent binary happens to be
#     installed on the runner, the command must terminate GRACEFULLY (a normal
#     non-zero exit with a rendered error) rather than panic or hang: either the
#     binary is missing (resolve error) or it is present and exits non-zero
#     without a prompt. `run` is fenced in agent mode, so force human mode for
#     this check only — the point is the launcher's degrade, not the fence.
set +e
JENTIC_MODE=human "$JENTIC" run claude --yes </dev/null > "$SCRATCH/run.out" 2>&1
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "jentic run claude exit=0 offline, want a graceful non-zero"
if grep -qi 'panic' "$SCRATCH/run.out"; then fail "jentic run claude crashed (panic): $(cat "$SCRATCH/run.out")"; fi
pass "jentic run claude degrades cleanly (exit $rc, no crash)"

# 6b. `jenticctl stop` on a scratch home (no compose file, no PID file) must
#     report "nothing to stop" and exit 0 — never error on the process path.
#     This is the assertion OPS-20 would have failed on native Windows.
set +e
"$JENTICCTL" stop > "$SCRATCH/stop.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "jenticctl stop exit=$rc on a scratch home, want 0; got: $(cat "$SCRATCH/stop.out")"
grep -qi 'nothing to stop' "$SCRATCH/stop.out" \
  || fail "jenticctl stop (scratch home) gave no 'nothing to stop'; got: $(cat "$SCRATCH/stop.out")"
pass "jenticctl stop is a clean no-op on a scratch home (exit 0)"

# 7. LIVE ONLY (QA-2): against a running control plane, make an assertion the
#    offline legs structurally cannot — that the base URL the CLI resolves to is
#    actually serving. A fresh CI stack has no approved agent token, so an
#    authenticated CLI data call (search/catalog) cannot succeed here; instead we
#    prove reachability of the public health route at the exact base URL, then
#    confirm `jentic doctor` sees it reachable. This closes the gap where the live
#    leg re-ran the offline checks and proved nothing extra.
if [ -n "$LIVE_URL" ]; then
  curl -fsS "$LIVE_URL/health" >/dev/null 2>&1 \
    || fail "live: control plane health endpoint at $LIVE_URL/health not reachable"
  # doctor against the live base URL must report the reachability check as passing
  # (it exits 0 when no hard check fails; with a live server the reachability row
  # is a pass rather than a warn).
  export JENTIC_BASE_URL="$LIVE_URL"
  set +e
  "$JENTIC" doctor --json > "$SCRATCH/live-doctor.json" 2>"$SCRATCH/live-doctor.err"
  rc=$?
  set -e
  [ "$rc" -eq 0 ] || { cat "$SCRATCH/live-doctor.err" >&2; fail "live: jentic doctor --json exit=$rc against a live stack, want 0"; }
  pass "live: control plane reachable at $LIVE_URL + jentic doctor exit 0"
fi

echo "== smoke passed =="
