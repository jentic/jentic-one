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

# --- write_manifest preserves stack-owned fields across a CLI-only run ---
# This script only ever builds the CLI binaries. `stack_ref` records what the
# *stack* was last built from, so dropping it would let a CLI update advertise a
# stale stack as current and wedge later updates (jentic-one#943). mode/db/
# broker_port are likewise owned by `jenticctl install`.
tmp_home="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-home.XXXXXX")"
cat > "$tmp_home/install.json" <<'EOF'
{
  "repo": "jentic/jentic-one",
  "ref": "v0.24.0",
  "stack_ref": "v0.24.0",
  "commit": "aaaaaaa",
  "cli_version": "v0.24.0",
  "binary_path": "/opt/homebrew/bin/jenticctl",
  "mode": "docker",
  "db": "postgres",
  "broker_port": "8100",
  "installed_at": "2026-01-01T00:00:00Z"
}
EOF
(
  export JENTIC_HOME="$tmp_home"
  JENTIC_REPO="jentic/jentic-one" JENTIC_REF="v0.25.0" \
    BUILT_COMMIT="bbbbbbb" INSTALLED_PATH="/opt/homebrew/bin/jenticctl" \
    write_manifest >/dev/null 2>&1
)
manifest_out="$(cat "$tmp_home/install.json")"
assert_contains "write_manifest: advances ref to the new CLI build" "$manifest_out" '"ref": "v0.25.0"'
assert_contains "write_manifest: preserves stack_ref (#943)" "$manifest_out" '"stack_ref": "v0.24.0"'
assert_contains "write_manifest: preserves mode" "$manifest_out" '"mode": "docker"'
assert_contains "write_manifest: preserves db" "$manifest_out" '"db": "postgres"'
assert_contains "write_manifest: preserves broker_port" "$manifest_out" '"broker_port": "8100"'

# A fresh install (no prior manifest) must not emit an empty stack_ref: the
# stack has not been built yet, and `""` would parse as a real (bogus) value.
rm -f "$tmp_home/install.json"
(
  export JENTIC_HOME="$tmp_home"
  JENTIC_REPO="jentic/jentic-one" JENTIC_REF="v0.25.0" \
    BUILT_COMMIT="bbbbbbb" INSTALLED_PATH="/opt/homebrew/bin/jenticctl" \
    write_manifest >/dev/null 2>&1
)
manifest_out="$(cat "$tmp_home/install.json")"
assert_not_contains "write_manifest: fresh install omits stack_ref" "$manifest_out" 'stack_ref'
rm -rf "$tmp_home"

# --- write_manifest: backfill stack_ref for pre-stack_ref manifests ------------
# Every manifest written before stack_ref existed records the stack's build only
# in `ref` — the field a CLI-only re-install overwrites. Losing it makes
# ResolvedStackRef() fall back to the *new* `ref`, so a stale stack reports
# itself current: #943, re-created on the first CLI-only re-install, which is
# precisely when the wedge occurs. `mode` is written by `jenticctl install`, so
# its presence is what says "a stack was built at this ref".
tmp_home="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-home.XXXXXX")"
cat > "$tmp_home/install.json" <<'EOF'
{
  "repo": "jentic/jentic-one",
  "ref": "v0.25.0",
  "commit": "f4cb196",
  "cli_version": "v0.25.0",
  "binary_path": "/opt/homebrew/bin/jenticctl",
  "mode": "docker",
  "db": "postgres",
  "installed_at": "2026-08-04T10:42:27Z"
}
EOF
(
  export JENTIC_HOME="$tmp_home"
  JENTIC_REPO="jentic/jentic-one" JENTIC_REF="v0.26.0" \
    BUILT_COMMIT="ccccccc" INSTALLED_PATH="/opt/homebrew/bin/jenticctl" \
    write_manifest >/dev/null 2>&1
)
manifest_out="$(cat "$tmp_home/install.json")"
assert_contains "write_manifest: legacy manifest advances ref" "$manifest_out" '"ref": "v0.26.0"'
assert_contains "write_manifest: backfills stack_ref from the old ref (#943)" \
  "$manifest_out" '"stack_ref": "v0.25.0"'

# A legacy manifest with no `mode` never had a stack built, so there is nothing
# to backfill — inventing a stack_ref would claim a stack that does not exist.
rm -f "$tmp_home/install.json"
cat > "$tmp_home/install.json" <<'EOF'
{
  "repo": "jentic/jentic-one",
  "ref": "v0.25.0",
  "commit": "f4cb196",
  "cli_version": "v0.25.0",
  "binary_path": "/opt/homebrew/bin/jenticctl",
  "installed_at": "2026-08-04T10:42:27Z"
}
EOF
(
  export JENTIC_HOME="$tmp_home"
  JENTIC_REPO="jentic/jentic-one" JENTIC_REF="v0.26.0" \
    BUILT_COMMIT="ccccccc" INSTALLED_PATH="/opt/homebrew/bin/jenticctl" \
    write_manifest >/dev/null 2>&1
)
manifest_out="$(cat "$tmp_home/install.json")"
assert_not_contains "write_manifest: CLI-only legacy manifest omits stack_ref" \
  "$manifest_out" 'stack_ref'
rm -rf "$tmp_home"

# --- highest_release_tag: latest canonical release tag from ls-remote output ---
# Mirrors the Go highestReleaseTag (cli/internal/update/version.go): keep only
# clean vX.Y.Z tags; ignore cli/v* noise, pre-releases, and peeled "^{}" lines.
# The fixture is deliberately unordered and mixes all the shapes we must ignore.
lsr_fixture="$(printf '%s\n' \
  'b99f974	refs/tags/cli/v0.14.3' \
  'b99f974	refs/tags/cli/v0.14.3^{}' \
  '1b37598	refs/tags/v0.1.0' \
  'f4b8f89	refs/tags/v0.10.0' \
  'aaaaaaa	refs/tags/v0.24.0' \
  'bbbbbbb	refs/tags/v0.24.0^{}' \
  'ccccccc	refs/tags/v1.0.0-rc1' \
  'ddddddd	refs/tags/v0.9.0')"

out="$(printf '%s\n' "$lsr_fixture" | highest_release_tag)"
assert_eq "highest_release_tag: picks the highest vX.Y.Z" "v0.24.0" "$out"

# Numeric (not lexical) ordering: v0.9.0 must beat v0.10.0 only when it's really
# larger — here v0.10.0 > v0.9.0, and neither is the max, so the earlier check
# already covers ordering. Add a focused pair to guard against lexical sort.
out="$(printf '%s\n' 'x	refs/tags/v0.2.0' 'y	refs/tags/v0.10.0' | highest_release_tag)"
assert_eq "highest_release_tag: numeric ordering (v0.10.0 > v0.2.0)" "v0.10.0" "$out"

# Only noise/non-release tags -> non-zero exit and no output.
set +e
out="$(printf '%s\n' 'a	refs/tags/cli/v0.14.3' 'b	refs/tags/v1.0.0-rc1' 'c	refs/heads/main' | highest_release_tag)"
rc=$?
set -e
assert_eq "highest_release_tag: no release tags -> non-zero exit" "1" "$rc"
assert_eq "highest_release_tag: no release tags -> empty output" "" "$out"

# Empty input -> non-zero exit, empty output.
set +e
out="$(printf '' | highest_release_tag)"
rc=$?
set -e
assert_eq "highest_release_tag: empty input -> non-zero exit" "1" "$rc"

# --- check_prereqs batches ALL missing tools (install P0-B) ------------------
# A user missing two base tools should learn both in one failure, not discover
# the second only after installing the first and re-running. Drive check_prereqs
# with a minimal PATH that has bash+grep but omits git AND tar, and assert the
# single die() names both.
prereq_bindir="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-prereq.XXXXXX")"
for t in bash cat mktemp rm grep curl uname; do
  p="$(command -v "$t" 2>/dev/null || true)"
  [ -n "$p" ] && [ -x "$p" ] && [ -f "$p" ] && ln -sf "$p" "$prereq_bindir/$t"
done
# git and tar are deliberately absent.
set +e
prereq_out="$(PATH="$prereq_bindir" bash -c ". '$INSTALL_SH'; check_prereqs" 2>&1)"
set -e
assert_contains "check_prereqs: reports missing git" "$prereq_out" "git"
assert_contains "check_prereqs: reports missing tar in the SAME failure" "$prereq_out" "tar"
rm -rf "$prereq_bindir"

# --- chain_install non-interactive branch prints the headless next step ------
# With no TTY (stdin+stdout both redirected) chain_install must NOT exec the
# wizard; it prints the `install --defaults` command and the JENTIC_NO_INSTALL
# opt-out, then returns non-zero so main() falls through to banner().
set +e
chain_out="$(CTL_BINARY=jenticctl API_BINARY=jentic \
  bash -c ". '$INSTALL_SH'; chain_install </dev/null" 2>&1 >/dev/null)"
chain_rc=$?
set -e
assert_eq "chain_install: non-interactive returns non-zero (no exec)" "1" "$chain_rc"
assert_contains "chain_install: names the non-interactive install command" \
  "$chain_out" "install --defaults"
assert_contains "chain_install: surfaces the JENTIC_NO_INSTALL opt-out" \
  "$chain_out" "JENTIC_NO_INSTALL=1"

# JENTIC_NO_INSTALL=1 short-circuits before printing anything.
set +e
chain_out="$(CTL_BINARY=jenticctl API_BINARY=jentic JENTIC_NO_INSTALL=1 \
  bash -c ". '$INSTALL_SH'; chain_install </dev/null" 2>&1)"
chain_rc=$?
set -e
assert_eq "chain_install: JENTIC_NO_INSTALL=1 returns non-zero" "1" "$chain_rc"
assert_not_contains "chain_install: JENTIC_NO_INSTALL=1 prints no wizard hint" \
  "$chain_out" "install --defaults"

# --- plan_summary prints the from-source plan preamble (install P0-B) --------
plan_out="$(CTL_BINARY=jenticctl API_BINARY=jentic \
  bash -c ". '$INSTALL_SH'; plan_summary" 2>&1)"
assert_contains "plan_summary: states it builds from source" "$plan_out" "from source"
assert_contains "plan_summary: names the install dir" "$plan_out" "$JENTIC_INSTALL_DIR"

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
  assert_contains "curl|sh via /bin/sh: re-execs and reaches prereq check" "$out" "not found: git"
fi

# Under dash, if available (Linux CI default /bin/sh).
if command -v dash >/dev/null 2>&1; then
  out="$(run_installer_via "$(command -v dash)" || true)"
  assert_not_contains "curl|sh via dash: no bash syntax error" "$out" "syntax error"
  assert_contains "curl|sh via dash: re-execs and reaches prereq check" "$out" "not found: git"
fi

# Under bash directly — no re-exec, must still reach the prereq check.
if command -v bash >/dev/null 2>&1; then
  out="$(run_installer_via "$(command -v bash)" || true)"
  assert_not_contains "curl|sh via bash: no bash syntax error" "$out" "syntax error"
  assert_contains "curl|sh via bash: reaches prereq check" "$out" "not found: git"
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

# --- re-fetch source override: JENTIC_INSTALL_SOURCE_URL is honoured ---------
# First-party install endpoints set (or serve-time-rewrite) this variable so
# the piped re-exec loops back to the origin that served the script instead of
# hard-depending on raw.githubusercontent.com (jentic-one#962). Prove it with a
# stubbed curl that records its argv and fails: the recorded URL must be the
# override, and the error message must name it. Each invocation clears the
# JENTIC_* env that could leak in from a developer shell and skew the run.
if [ -x /bin/sh ]; then
  bindir="$(mktemp -d "${TMPDIR:-/tmp}/jentic-test-bin.XXXXXX")"
  make_min_path "$bindir"
  curl_log="$(mktemp "${TMPDIR:-/tmp}/jentic-test-curl.XXXXXX")"
  printf '#!/bin/sh\nprintf '"'"'%%s\\n'"'"' "$*" >> "%s"\nexit 22\n' "$curl_log" > "$bindir/curl"
  chmod +x "$bindir/curl"

  set +e
  out="$(PATH="$bindir" JENTIC_INSTALL_SELF= JENTIC_INSTALL_REEXEC= JENTIC_REPO= JENTIC_REF= \
    JENTIC_INSTALL_SOURCE_URL="https://jentic.example/install.sh" \
    GITHUB_TOKEN="secret-token" /bin/sh < "$INSTALL_SH" 2>&1)"
  set -e
  recorded="$(cat "$curl_log" 2>/dev/null || true)"
  assert_contains "re-exec override: stub curl fetched the override URL" \
    "$recorded" "https://jentic.example/install.sh"
  assert_not_contains "re-exec override: raw.githubusercontent.com not contacted" \
    "$recorded" "raw.githubusercontent.com"
  assert_not_contains "re-exec override: GITHUB_TOKEN not sent to non-GitHub origin" \
    "$recorded" "secret-token"
  assert_contains "re-exec override: failure message names the override URL" \
    "$out" "https://jentic.example/install.sh"
  assert_not_contains "re-exec override: error message does not mention GitHub raw" \
    "$out" "raw.githubusercontent.com"

  # Default (no override): the canonical raw URL is fetched, and the token IS
  # attached for GitHub (private-fork support unchanged).
  : > "$curl_log"
  set +e
  out="$(PATH="$bindir" JENTIC_INSTALL_SELF= JENTIC_INSTALL_REEXEC= JENTIC_REPO= JENTIC_REF= \
    GITHUB_TOKEN="secret-token" /bin/sh < "$INSTALL_SH" 2>&1)"
  set -e
  recorded="$(cat "$curl_log" 2>/dev/null || true)"
  assert_contains "re-exec default: canonical raw URL fetched" \
    "$recorded" "https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh"
  assert_contains "re-exec default: GITHUB_TOKEN attached for GitHub origin" \
    "$recorded" "secret-token"

  # Empty override: ':-' falls back to the canonical URL (guards a future
  # '${VAR-…}' typo that would silently fetch an empty URL).
  : > "$curl_log"
  set +e
  out="$(PATH="$bindir" JENTIC_INSTALL_SELF= JENTIC_INSTALL_REEXEC= JENTIC_REPO= JENTIC_REF= \
    JENTIC_INSTALL_SOURCE_URL="" /bin/sh < "$INSTALL_SH" 2>&1)"
  set -e
  assert_contains "re-exec empty override: falls back to canonical raw URL" \
    "$(cat "$curl_log")" "https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh"

  # Precedence: JENTIC_INSTALL_SELF wins over SOURCE_URL (no network fetch).
  : > "$curl_log"
  set +e
  out="$(PATH="$bindir" JENTIC_INSTALL_REEXEC= JENTIC_REPO= JENTIC_REF= \
    JENTIC_INSTALL_SELF="$INSTALL_SH" JENTIC_INSTALL_SOURCE_URL="https://jentic.example/install.sh" \
    /bin/sh < "$INSTALL_SH" 2>&1)"
  set -e
  assert_eq "re-exec: SELF wins over SOURCE_URL (curl never called)" "" "$(cat "$curl_log")"
  assert_contains "re-exec: SELF wins over SOURCE_URL (reaches prereq check)" \
    "$out" "not found: git"

  # Override on github.com itself: the token IS attached (private-fork proxy).
  : > "$curl_log"
  set +e
  out="$(PATH="$bindir" JENTIC_INSTALL_SELF= JENTIC_INSTALL_REEXEC= JENTIC_REPO= JENTIC_REF= \
    GITHUB_TOKEN="secret-token" \
    JENTIC_INSTALL_SOURCE_URL="https://github.com/jentic/fork/raw/main/tools/install.sh" \
    /bin/sh < "$INSTALL_SH" 2>&1)"
  set -e
  assert_contains "re-exec override on github.com: token IS attached" \
    "$(cat "$curl_log")" "secret-token"

  # Non-https override: refused fail-closed before any fetch (http:// would
  # hand code execution to an on-path attacker; leading '-' would be parsed
  # as a curl option).
  : > "$curl_log"
  set +e
  out="$(PATH="$bindir" JENTIC_INSTALL_SELF= JENTIC_INSTALL_REEXEC= JENTIC_REPO= JENTIC_REF= \
    JENTIC_INSTALL_SOURCE_URL="http://jentic.example/install.sh" \
    /bin/sh < "$INSTALL_SH" 2>&1)"
  set -e
  assert_contains "re-exec non-https override: refused with a clear error" \
    "$out" "must be an https:// URL"
  assert_eq "re-exec non-https override: curl never called" "" "$(cat "$curl_log")"

  rm -rf "$bindir" "$curl_log"
fi

# ---------------------------------------------------------------------------
if [ "$FAIL" -ne 0 ]; then
  printf '\nFAILED\n' >&2
  exit 1
fi
printf '\nAll %d checks passed.\n' "$TEST_NUM"
