#!/usr/bin/env bash
#
# Tests for tools/install.sh.
#
# Dependency-free (no bats/shellcheck): a plain bash harness with tiny assert
# helpers. Exits non-zero if any check fails, printing TAP-ish `ok`/`not ok`
# lines. Two tiers:
#
#   1. Unit  — sources install.sh (via its BASH_SOURCE guard, so `main` is not
#              run) and asserts on pure helper functions.
#   2. Contract — invokes install.sh through the shells the README promises
#              (`sh`, `dash`, `bash`) to prove the re-exec guard works and the
#              script no longer dies on bash-only syntax. Kept hermetic with a
#              minimal PATH so it fails fast at the prereq check — no network,
#              no build, no writes to the real ~/.jentic.
#
# Usage:
#   bash tests/tools/install_test.sh
#
# Run it with bash (its shebang). It internally drives the installer through
# sh/dash/bash to exercise every re-exec path, so there's no need to invoke the
# harness itself under other shells (it uses bash-only features like pipefail).

set -euo pipefail

# Resolve the installer path relative to this test file so it runs from any cwd.
# This test lives in tests/tools/; the installer is at tools/install.sh.
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
INSTALL_SH="$TESTS_DIR/../../tools/install.sh"

FAIL=0
TEST_NUM=0

pass() { TEST_NUM=$((TEST_NUM + 1)); printf 'ok %d - %s\n' "$TEST_NUM" "$1"; }
fail() { TEST_NUM=$((TEST_NUM + 1)); FAIL=1; printf 'not ok %d - %s\n' "$TEST_NUM" "$1"; }

assert_eq() { # <desc> <expected> <actual>
  if [ "$2" = "$3" ]; then pass "$1"; else
    fail "$1"; printf '    expected: %s\n    actual:   %s\n' "$2" "$3" >&2
  fi
}

assert_contains() { # <desc> <haystack> <needle>
  case "$2" in
    *"$3"*) pass "$1" ;;
    *) fail "$1"; printf '    expected to contain: %s\n    in: %s\n' "$3" "$2" >&2 ;;
  esac
}

assert_not_contains() { # <desc> <haystack> <needle>
  case "$2" in
    *"$3"*) fail "$1"; printf '    expected NOT to contain: %s\n    in: %s\n' "$3" "$2" >&2 ;;
    *) pass "$1" ;;
  esac
}

# ---------------------------------------------------------------------------
# Unit tier: source the installer so its functions are defined but main() does
# not run. The BASH_SOURCE guard at the bottom of install.sh handles this. We
# are already under real (non-POSIX) bash here, so the re-exec guard is a no-op.
# ---------------------------------------------------------------------------

# shellcheck source=/dev/null
. "$INSTALL_SH"

# --- _need_bash_reexec truth table ---
# Real bash, not POSIX: no re-exec needed.
if _need_bash_reexec; then
  fail "_need_bash_reexec is false under normal bash"
else
  pass "_need_bash_reexec is false under normal bash"
fi

# POSIX mode: SHELLOPTS is readonly in bash, so we can't fake it in-process.
# Exercise the real thing by evaluating the function body in a POSIX-mode bash
# (bash --posix), which sets SHELLOPTS to include `posix`.
if bash --posix -c '
  _need_bash_reexec() {
    [ -z "${BASH_VERSION:-}" ] && return 0
    case ":${SHELLOPTS:-}:" in *:posix:*) return 0 ;; esac
    return 1
  }
  _need_bash_reexec'; then
  pass "_need_bash_reexec is true under POSIX-mode bash (bash --posix)"
else
  fail "_need_bash_reexec is true under POSIX-mode bash (bash --posix)"
fi

# Not bash at all: run the check under a POSIX sh where BASH_VERSION is unset.
if sh -c '
  need_reexec() {
    [ -z "${BASH_VERSION:-}" ] && return 0
    case ":${SHELLOPTS:-}:" in *:posix:*) return 0 ;; esac
    return 1
  }
  need_reexec' 2>/dev/null; then
  pass "_need_bash_reexec is true when not running bash"
else
  # On macOS `sh` IS bash (BASH_VERSION set) but posix -> still returns true.
  # Only fail if it genuinely returned false.
  fail "_need_bash_reexec is true when not running bash"
fi

# --- rc_files_for_shell per-shell mapping ---
rc_out="$(SHELL=/bin/zsh rc_files_for_shell)"
assert_eq "rc_files_for_shell: zsh -> ~/.zshrc" "$HOME/.zshrc" "$rc_out"

rc_out="$(SHELL=/bin/bash rc_files_for_shell)"
assert_eq "rc_files_for_shell: bash -> .bashrc + .bash_profile" \
  "$(printf '%s\n%s' "$HOME/.bashrc" "$HOME/.bash_profile")" "$rc_out"

# Unknown shell with no existing rc -> ~/.profile.
tmp_home="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-home.XXXXXX")"
rc_out="$(HOME="$tmp_home" SHELL=/bin/fish rc_files_for_shell)"
assert_eq "rc_files_for_shell: unknown shell, no rc -> ~/.profile" \
  "$tmp_home/.profile" "$rc_out"
rm -rf "$tmp_home"

# --- ensure_path_in_rc idempotency ---
# Drive a temp HOME + zsh so exactly one rc file (~/.zshrc) is targeted, then
# run the append twice and assert the guarded block lands exactly once.
tmp_home="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-home.XXXXXX")"
: > "$tmp_home/.zshrc"
(
  export HOME="$tmp_home" SHELL=/bin/zsh
  RC_UPDATED=0 RC_ALREADY_HAD_PATH=0
  ensure_path_in_rc
  ensure_path_in_rc
)
marker_count="$(grep -cF "$JENTIC_RC_MARKER" "$tmp_home/.zshrc" || true)"
assert_eq "ensure_path_in_rc writes the marker exactly once (idempotent)" "1" "$marker_count"
export_count="$(grep -cF "$JENTIC_INSTALL_DIR" "$tmp_home/.zshrc" || true)"
assert_eq "ensure_path_in_rc writes the export line exactly once" "1" "$export_count"
rm -rf "$tmp_home"

# --- detect_platform arch mapping ---
# Stub `uname` so we can drive the arch branch deterministically, in a subshell
# so the stub + globals don't leak. Assert the documented mappings and that an
# unsupported arch exits non-zero.
run_detect() { # <uname_s> <uname_m> -> prints "$OS/$ARCH", exit code from die
  local os_s="$1" arch_m="$2" stub_dir
  stub_dir="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-uname.XXXXXX")"
  cat > "$stub_dir/uname" <<EOF
#!/bin/sh
case "\$1" in
  -s) echo "$os_s" ;;
  -m) echo "$arch_m" ;;
  *)  echo "$os_s" ;;
esac
EOF
  chmod +x "$stub_dir/uname"
  (
    PATH="$stub_dir:$PATH"
    detect_platform >/dev/null 2>&1 || exit $?
    printf '%s/%s' "$OS" "$ARCH"
  )
  local rc=$?
  rm -rf "$stub_dir"
  return $rc
}

out="$(run_detect Linux x86_64)";  assert_eq "detect_platform: Linux/x86_64 -> linux/amd64"  "linux/amd64"  "$out"
out="$(run_detect Linux aarch64)"; assert_eq "detect_platform: Linux/aarch64 -> linux/arm64" "linux/arm64"  "$out"
out="$(run_detect Darwin arm64)";  assert_eq "detect_platform: Darwin/arm64 -> darwin/arm64" "darwin/arm64" "$out"
out="$(run_detect Darwin amd64)";  assert_eq "detect_platform: Darwin/amd64 -> darwin/amd64" "darwin/amd64" "$out"

if run_detect Linux sparc64 >/dev/null 2>&1; then
  fail "detect_platform: unsupported arch exits non-zero"
else
  pass "detect_platform: unsupported arch exits non-zero"
fi

# ---------------------------------------------------------------------------
# Contract tier: run the installer through each shell and prove it re-execs and
# reaches main() without a bash syntax error. We build a minimal PATH that has
# the utilities the guard + logo + prereq check need EXCEPT git, so the run
# stops fast at `check_prereqs` with a known message. This is fully offline.
# ---------------------------------------------------------------------------

# Build a temp bin dir with only the tools the early code path needs before it
# dies at `need git`: bash (re-exec target), and cat/mktemp/rm for the piped
# re-exec branch. git is deliberately absent so `check_prereqs` fails fast.
# We resolve each tool to a real on-disk path (skipping shell builtins/aliases)
# so the symlinks are valid.
make_min_path() {
  local d="$1" t p
  mkdir -p "$d"
  for t in bash cat mktemp rm grep; do
    p="$(command -v "$t" 2>/dev/null || true)"
    # Only link real executable files (ignore builtins/aliases/functions).
    if [ -n "$p" ] && [ -x "$p" ] && [ -f "$p" ]; then
      ln -sf "$p" "$d/$t"
    fi
  done
}

run_installer_via() { # <shell> -> captures combined output; expects non-zero (prereq die)
  local shell_bin="$1" out rc bindir
  bindir="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-bin.XXXXXX")"
  make_min_path "$bindir"
  # Pipe the script into the shell (mirrors `curl ... | sh`). The piped re-exec
  # can't re-read stdin, so point JENTIC_INSTALL_SELF at the local installer so
  # the guard re-runs THIS copy under bash (no network fetch) — exactly the
  # code path we want to test. JENTIC_NO_INSTALL is belt-and-braces (we never
  # get that far; the run stops at `need git`).
  set +e
  out="$(PATH="$bindir" JENTIC_NO_INSTALL=1 JENTIC_INSTALL_SELF="$INSTALL_SH" \
    "$shell_bin" < "$INSTALL_SH" 2>&1)"
  rc=$?
  set -e
  rm -rf "$bindir"
  printf '%s' "$out"
  return $rc
}

# Under /bin/sh (macOS: bash in POSIX mode) — the original failure mode.
if [ -x /bin/sh ]; then
  out="$(run_installer_via /bin/sh || true)"
  assert_not_contains "curl|sh via /bin/sh: no bash syntax error" "$out" "syntax error"
  assert_not_contains "curl|sh via /bin/sh: no unexpected token '<'" "$out" "unexpected token"
  assert_contains "curl|sh via /bin/sh: re-execs and reaches prereq check" "$out" "required command not found: git"
fi

# Under dash, if available (Linux CI default /bin/sh).
if command -v dash >/dev/null 2>&1; then
  out="$(run_installer_via "$(command -v dash)" || true)"
  assert_not_contains "curl|sh via dash: no bash syntax error" "$out" "syntax error"
  assert_contains "curl|sh via dash: re-execs and reaches prereq check" "$out" "required command not found: git"
fi

# Under bash directly — no re-exec, must still reach the prereq check.
if command -v bash >/dev/null 2>&1; then
  out="$(run_installer_via "$(command -v bash)" || true)"
  assert_not_contains "curl|sh via bash: no bash syntax error" "$out" "syntax error"
  assert_contains "curl|sh via bash: reaches prereq check" "$out" "required command not found: git"
fi

# --- re-fetch fallback: no JENTIC_INSTALL_SELF and no curl -> clean error ---
# The piped re-exec can't re-read stdin, so without a local self-copy it must
# re-fetch via curl. Prove the failure is a clear, actionable message (not a
# hang or a bash syntax error) when curl is absent. Use the same minimal PATH
# (which has no curl) and DON'T set JENTIC_INSTALL_SELF.
if [ -x /bin/sh ]; then
  bindir="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-bin.XXXXXX")"
  make_min_path "$bindir"
  set +e
  out="$(PATH="$bindir" /bin/sh < "$INSTALL_SH" 2>&1)"
  set -e
  rm -rf "$bindir"
  assert_contains "piped re-exec without curl: clear error, no hang" \
    "$out" "curl is required to bootstrap"
  assert_not_contains "piped re-exec without curl: no bash syntax error" "$out" "syntax error"
fi

# ---------------------------------------------------------------------------
if [ "$FAIL" -ne 0 ]; then
  printf '\nFAILED\n' >&2
  exit 1
fi
printf '\nAll %d checks passed.\n' "$TEST_NUM"
