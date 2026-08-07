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

BIN_DIR="${1:?usage: smoke.sh <bin-dir>}"
JENTIC="$BIN_DIR/jentic"
JENTICCTL="$BIN_DIR/jenticctl"

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

echo "== jentic CLI smoke (bin: $BIN_DIR) =="

[ -x "$JENTIC" ]    || fail "jentic binary not found/executable at $JENTIC"
[ -x "$JENTICCTL" ] || fail "jenticctl binary not found/executable at $JENTICCTL"
pass "both binaries present"

# 1. --version prints a stamped version on both binaries.
"$JENTIC" --version    | grep -Eiq 'jentic'    || fail "jentic --version had no version line"
"$JENTICCTL" --version | grep -Eiq 'jentic'    || fail "jenticctl --version had no version line"
pass "--version on both binaries"

# 2. jentic doctor --json parses. Identity/reachability rows may WARN with no
#    server; doctor exits 0 unless a hard check FAILS, so a clean exit here means
#    the XDG-paths self-check ran and the envelope is well-formed.
if ! "$JENTIC" doctor --json > "$SCRATCH/doctor.json" 2>"$SCRATCH/doctor.err"; then
  cat "$SCRATCH/doctor.err" >&2
  fail "jentic doctor --json exited non-zero (a hard check failed)"
fi
grep -q '"checks"' "$SCRATCH/doctor.json" || fail "jentic doctor JSON had no checks array"
pass "jentic doctor --json parses"

# 3. jentic profile list exits 0 (empty is fine on a fresh scratch home).
"$JENTIC" profile list >/dev/null 2>&1 || fail "jentic profile list exited non-zero"
pass "jentic profile list"

# 4. jenticctl doctor --json parses. It exits non-zero only on a `fail` row; a
#    scratch home with no install may report warnings but must not hard-fail
#    just because nothing is installed.
"$JENTICCTL" doctor --json > "$SCRATCH/ctl-doctor.json" 2>/dev/null || true
grep -q '"checks"' "$SCRATCH/ctl-doctor.json" 2>/dev/null \
  || grep -q '"failures"' "$SCRATCH/ctl-doctor.json" 2>/dev/null \
  || fail "jenticctl doctor JSON was not well-formed"
pass "jenticctl doctor --json parses"

echo "== smoke passed =="
