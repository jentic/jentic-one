#!/usr/bin/env bash
#
# Jentic CLI installer.
#
# Detects your OS/arch, ensures a Go toolchain, fetches the `cli/` source from
# GitHub, builds the `jenticctl` (installer/lifecycle) and `jentic` (API-spec)
# binaries, and installs both onto your PATH.
#
# Quick start:
#   curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh
#
# For a private fork or a repo you must authenticate to, pass a GitHub token
# with `repo` (read) scope:
#   curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
#     https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh \
#     | GITHUB_TOKEN=$GITHUB_TOKEN sh
#
# Configuration (environment variables, all optional):
#   JENTIC_REPO         owner/name of the source repo   (default: jentic/jentic-one)
#   JENTIC_REF          branch, tag, or commit to build  (default: latest release
#                       tag, e.g. v0.24.0; falls back to main if none is found.
#                       Set explicitly to build main or a dev branch.)
#   JENTIC_INSTALL_DIR  where to install the binaries     (default: ~/.jentic/bin)
#   JENTIC_GO_VERSION   Go to download if none suitable   (default: 1.26.2)
#   GITHUB_TOKEN        token for cloning a private fork  (default: unset/anonymous)
#   JENTIC_NO_INSTALL   set to 1 to stop after installing the binaries, skipping
#                       the automatic hand-off into `jenticctl install`
#   JENTIC_INSTALL_SOURCE_URL
#                       https:// URL the piped re-exec re-fetches this script
#                       from (default: the canonical raw.githubusercontent.com
#                       URL for JENTIC_REPO/JENTIC_REF; non-https values are
#                       refused, and empty is treated as unset — a failed
#                       template substitution falls back to GitHub rather than
#                       fetching an empty URL). First-party install endpoints
#                       that serve this script (e.g. a website proxy) should
#                       set it — by prepending an `export …` line or replacing
#                       the assignment with a statically configured value;
#                       never interpolate request-derived data — so the
#                       re-exec loops back to the origin that served the
#                       script instead of hard-depending on GitHub raw.
#
# This script is invoked via `curl ... | sh`, so it re-execs itself under a
# full (non-POSIX) bash (below) to get predictable behavior across shells.
# Note: on macOS `/bin/sh` is bash in POSIX mode — BASH_VERSION is set but
# bash-only syntax (process substitution, etc.) is disabled — so we detect that
# via SHELLOPTS and re-exec too. When piped (`curl | sh`) there is no script
# file at $0 and stdin can't be re-read (a POSIX sh consumes it up front), so we
# re-fetch this script from its canonical raw URL and run that under bash.

# --- bash re-exec guard -----------------------------------------------------
# _need_bash_reexec reports whether we must re-exec under a full bash: true when
# we're not running bash at all, or when we're bash in POSIX mode (macOS
# /bin/sh), where the bash features this script relies on are disabled.
_need_bash_reexec() {
  [ -z "${BASH_VERSION:-}" ] && return 0
  case ":${SHELLOPTS:-}:" in
    *:posix:*) return 0 ;;
  esac
  return 1
}

if _need_bash_reexec; then
  if ! command -v bash >/dev/null 2>&1; then
    echo "error: bash is required to run this installer" >&2
    exit 1
  fi
  # Guard against an accidental exec loop if detection ever misfires.
  if [ -n "${JENTIC_INSTALL_REEXEC:-}" ]; then
    echo "error: failed to re-exec the installer under a non-POSIX bash" >&2
    exit 1
  fi
  export JENTIC_INSTALL_REEXEC=1
  # Re-exec the on-disk script only when $0 is a regular file that is actually
  # this installer. We identify it by a stable marker string (below). This
  # avoids the trap where a piped `sh` sets $0 to the shell binary itself (a
  # regular file), which would otherwise make us exec `bash <shell>`.
  if [ -f "$0" ] && grep -q "JENTIC_INSTALLER_SELF_ID" "$0" 2>/dev/null; then
    exec bash "$0" "$@"
  fi
  # Piped invocation (curl ... | sh): the body arrived on stdin and $0 is the
  # shell, not this script. We cannot re-read stdin — a POSIX sh (dash) consumes
  # the whole script up front, so `cat` would capture nothing. Instead we obtain
  # a full copy under bash and run that:
  #   * JENTIC_INSTALL_SELF=/path  -> use that local file (used by tests, and to
  #     re-run a local copy without a network round-trip);
  #   * JENTIC_INSTALL_SOURCE_URL -> re-fetch from that URL (set by first-party
  #     install endpoints so the re-exec loops back to the origin that served
  #     the script — see the configuration comment above);
  #   * otherwise re-fetch tools/install.sh from the canonical raw URL for the
  #     configured repo/ref (the same source curl fetched it from), honouring
  #     GITHUB_TOKEN for private forks (mirrors `jenticctl update`).
  _reexec_repo="${JENTIC_REPO:-jentic/jentic-one}"
  _reexec_ref="${JENTIC_REF:-main}"
  _reexec_tmp="$(mktemp "${TMPDIR:-/tmp}/jentic-install.XXXXXX")" || {
    echo "error: could not create a temp file to re-exec the installer" >&2
    exit 1
  }
  if [ -n "${JENTIC_INSTALL_SELF:-}" ] && [ -r "${JENTIC_INSTALL_SELF}" ]; then
    cat "${JENTIC_INSTALL_SELF}" > "$_reexec_tmp"
  else
    if ! command -v curl >/dev/null 2>&1; then
      rm -f "$_reexec_tmp"
      echo "error: curl is required to bootstrap the installer under bash" >&2
      exit 1
    fi
    # JENTIC_INSTALL_SOURCE_URL: single greppable seam for the re-exec source.
    # First-party install endpoints should set this so the executed bytes come
    # from the origin that served the script. Two safe ways, in order of
    # preference: (1) prepend a whole generated `export JENTIC_INSTALL_SOURCE_URL=…`
    # line to the served body, or (2) replace this exact assignment line with a
    # STATICALLY CONFIGURED value. Never interpolate request-derived data
    # (Host/X-Forwarded-* headers) into the served shell source — that is a
    # shell-injection vector — and keep the value to https:// plus
    # [A-Za-z0-9._/-] only.
    _reexec_url="${JENTIC_INSTALL_SOURCE_URL:-https://raw.githubusercontent.com/${_reexec_repo}/${_reexec_ref}/tools/install.sh}"
    # Fail closed on anything but https: the default is https-by-construction,
    # and a plaintext/exotic-scheme override would hand code execution to an
    # on-path attacker (curl accepts http/ftp/file with -fsSL). This also
    # rejects values starting with "-" that curl would parse as options.
    case "$_reexec_url" in
      https://*) ;;
      *)
        rm -f "$_reexec_tmp"
        echo "error: JENTIC_INSTALL_SOURCE_URL must be an https:// URL" >&2
        exit 1 ;;
    esac
    # Only attach the GitHub token when fetching from GitHub itself — never
    # leak it to a third-party/first-party override origin.
    _reexec_auth=""
    if [ -n "${GITHUB_TOKEN:-}" ]; then
      case "$_reexec_url" in
        https://raw.githubusercontent.com/*|https://github.com/*) _reexec_auth="Authorization: Bearer ${GITHUB_TOKEN}" ;;
      esac
    fi
    if ! curl -fsSL ${_reexec_auth:+-H "$_reexec_auth"} -o "$_reexec_tmp" -- "$_reexec_url"; then
      rm -f "$_reexec_tmp"
      echo "error: failed to fetch the installer from ${_reexec_url} to re-exec under bash" >&2
      echo "       (for a private fork set GITHUB_TOKEN, or run the script from a checkout)" >&2
      exit 1
    fi
  fi
  # Run (not exec) so we can clean up the temp file; propagate bash's exit code.
  bash "$_reexec_tmp" "$@"
  _reexec_rc=$?
  rm -f "$_reexec_tmp"
  exit "$_reexec_rc"
fi

set -euo pipefail

# JENTIC_INSTALLER_SELF_ID: stable marker used by the re-exec guard above to
# recognize this script on disk. Do not remove.

# --- configuration ----------------------------------------------------------
JENTIC_REPO="${JENTIC_REPO:-jentic/jentic-one}"
# Empty by default: fetch_source() resolves the latest release tag when unset.
# An explicit value (a branch like `main`, a tag, or a commit) is honored
# verbatim — that's the override for building main or a dev/local branch.
JENTIC_REF="${JENTIC_REF:-}"
JENTIC_INSTALL_DIR="${JENTIC_INSTALL_DIR:-$HOME/.jentic/bin}"
JENTIC_GO_VERSION="${JENTIC_GO_VERSION:-1.26.2}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

# JENTIC_INSTALL_METHOD selects how the binaries are obtained:
#   auto   (default) prefer a verified prebuilt download when the resolved ref is
#          a published release tag with matching assets; otherwise build from
#          source (forks / dev refs / no assets).
#   binary force the download path; error out if no matching release asset.
#   source force the from-source build (the historical behaviour).
JENTIC_INSTALL_METHOD="${JENTIC_INSTALL_METHOD:-auto}"

# JENTIC_INSTALL_BINARIES selects which binaries the download path fetches:
#   jentic (default for downloads) the agent CLI only — most users need only this.
#   both   also fetch jenticctl (installer/lifecycle). The from-source path always
#          builds both regardless of this knob.
JENTIC_INSTALL_BINARIES="${JENTIC_INSTALL_BINARIES:-jentic}"

# cosign keyless-signing identity, mirrored from docs/releasing.md and the Go
# updater (cli/internal/update/download.go). Used to verify checksums.txt.sig.
COSIGN_CERT_IDENTITY_REGEXP="https://github.com/${JENTIC_REPO}/.*"
COSIGN_OIDC_ISSUER="https://token.actions.githubusercontent.com"

# Minimum Go version required to build the CLI (mirrors the `go` directive in
# cli/go.mod). Keep this in sync with that directive.
GO_MIN_MAJOR=1
GO_MIN_MINOR=25

# The two binaries we build and install. BINARY_NAMES is the install/link set;
# CTL_BINARY (jenticctl) is the installer/lifecycle CLI we chain into and record
# as the manifest's primary binary_path. API_BINARY (jentic) is the API-spec CLI.
CTL_BINARY="jenticctl"
API_BINARY="jentic"
BINARY_NAMES=("$CTL_BINARY" "$API_BINARY")
# INSTALL_SET is the set actually installed/linked/verified this run. The source
# path builds both; the download path narrows it to the selected binaries (see
# download_selected_binaries). Populated by main() before install_binary.
INSTALL_SET=("$CTL_BINARY" "$API_BINARY")
TOOLCHAIN_DIR="$HOME/.jentic/toolchain"
WORKDIR=""
STATE_DIR=""
STEP_LOG=""
CURSOR_HIDDEN=0
STEP_NUM=0
TOTAL_STEPS=6

# PATH-wiring outcome flags, set by install_binary/ensure_path_in_rc and read by
# the final banner so it can tell the user exactly what (if anything) they need
# to do to get the binaries on PATH.
PATH_LINKED=0
RC_UPDATED=0
RC_ALREADY_HAD_PATH=0

# cmd_pkg_path <binary> maps a binary name to its main package within cli/.
cmd_pkg_path() {
  case "$1" in
    "$CTL_BINARY") printf './cmd/jenticctl' ;;
    "$API_BINARY") printf './cmd/jentic' ;;
    *) printf './cmd/%s' "$1" ;;
  esac
}

# --- logging ----------------------------------------------------------------
# Brand palette (truecolor) lifted from the CLI theme so the installer matches
# `jentic --help`. Colours are disabled when stderr is not a TTY (e.g. CI logs).
if [ -t 2 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[38;2;219;59;15m'
  C_GREEN=$'\033[38;2;94;222;185m'
  C_YELLOW=$'\033[38;2;241;227;139m'
  C_ORANGE=$'\033[38;2;253;189;121m'
  C_BLUE=$'\033[38;2;104;186;236m'
  C_PINK=$'\033[38;2;237;173;175m'
  C_BRAND=$'\033[38;2;163;202;204m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""
  C_YELLOW=""; C_ORANGE=""; C_BLUE=""; C_PINK=""; C_BRAND=""
fi

info() { printf '    %s%s%s\n' "$C_DIM" "$*" "$C_RESET" >&2; }
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*" >&2; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()  { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }

# step prints a numbered, brand-coloured phase header.
step() {
  STEP_NUM=$((STEP_NUM + 1))
  printf '\n%s%s[%d/%d]%s %s%s%s\n' \
    "$C_BOLD" "$C_BRAND" "$STEP_NUM" "$TOTAL_STEPS" "$C_RESET" \
    "$C_BOLD" "$*" "$C_RESET" >&2
}

# --- cursor + spinner -------------------------------------------------------
hide_cursor() { if [ -t 2 ]; then printf '\033[?25l' >&2; CURSOR_HIDDEN=1; fi; }
show_cursor() { if [ "${CURSOR_HIDDEN:-0}" = 1 ]; then printf '\033[?25h' >&2; CURSOR_HIDDEN=0; fi; }

SPIN_FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)

# spin <label> <command...> runs command with a live spinner, capturing its
# output to STEP_LOG. On success it prints a green check + elapsed time; on
# failure it prints a red cross, dumps the captured output, and returns 1.
# Falls back to a plain line (no animation) when stderr is not a TTY.
spin() {
  local label="$1"; shift
  if [ ! -t 2 ]; then
    info "$label ..."
    if "$@" >"$STEP_LOG" 2>&1; then
      ok "$label"
      return 0
    fi
    cat "$STEP_LOG" >&2 || true
    return 1
  fi

  local start elapsed i=0 frame
  start=$(date +%s)
  "$@" >"$STEP_LOG" 2>&1 &
  local pid=$!

  hide_cursor
  while kill -0 "$pid" 2>/dev/null; do
    frame="${SPIN_FRAMES[i % ${#SPIN_FRAMES[@]}]}"
    printf '\r  %s%s%s %s' "$C_BRAND" "$frame" "$C_RESET" "$label" >&2
    i=$((i + 1))
    sleep 0.08
  done

  if wait "$pid"; then
    elapsed=$(( $(date +%s) - start ))
    printf '\r\033[K  %s✓%s %s %s(%ss)%s\n' \
      "$C_GREEN" "$C_RESET" "$label" "$C_DIM" "$elapsed" "$C_RESET" >&2
    show_cursor
    return 0
  fi
  printf '\r\033[K  %s✗%s %s\n' "$C_RED" "$C_RESET" "$label" >&2
  show_cursor
  cat "$STEP_LOG" >&2 || true
  return 1
}

# logo prints the gradient "jentic" wordmark (matches the CLI help screen).
logo() {
  if [ ! -t 2 ]; then
    printf 'Jentic CLI installer\n' >&2
    return
  fi
  local lines=(
'   _            _   _      '
'  (_) ___ _ __ | |_(_) ___ '
'  | |/ _ \ '"'"'_ \| __| |/ __|'
'  | |  __/ | | | |_| | (__ '
' _/ |\___|_| |_|\__|_|\___|'
'|__/                       '
  )
  local colors=("$C_BLUE" "$C_GREEN" "$C_BRAND" "$C_YELLOW" "$C_ORANGE" "$C_PINK")
  local idx=0
  printf '\n' >&2
  for ln in "${lines[@]}"; do
    printf '%s%s%s\n' "${colors[idx]}" "$ln" "$C_RESET" >&2
    idx=$((idx + 1))
  done
  printf '\n%s  installing the jentic CLIs · jenticctl (install/operate) + jentic (discover/run)%s\n' \
    "$C_DIM" "$C_RESET" >&2
}

# --- cleanup ----------------------------------------------------------------
cleanup() {
  show_cursor
  if [ -n "${WORKDIR:-}" ] && [ -d "$WORKDIR" ]; then
    rm -rf "$WORKDIR"
  fi
  if [ -n "${STATE_DIR:-}" ] && [ -d "$STATE_DIR" ]; then
    rm -rf "$STATE_DIR"
  fi
}
trap cleanup EXIT INT TERM

# --- prerequisites ----------------------------------------------------------
need() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

# check_prereqs probes ALL base tools and reports the full missing set in one
# failure, rather than dying on the first (so a user with two missing tools
# doesn't have to re-run to discover the second).
check_prereqs() {
  local missing="" tool
  for tool in git curl tar mktemp uname; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
  done
  if [ -n "$missing" ]; then
    die "required command(s) not found:$missing"
  fi
}

# plan_summary prints a short "here's what this will do" preamble before any
# download or build, mirroring the RenderPreflight checklist `jenticctl install`
# shows. It states the from-source reality and warns before the ~150 MB Go
# toolchain fetch when no suitable `go` is present.
plan_summary() {
  printf '\n  %s%sInstall plan%s\n' "$C_BOLD" "$C_BRAND" "$C_RESET" >&2
  printf '    %s•%s builds %s + %s from source into %s\n' \
    "$C_DIM" "$C_RESET" "$CTL_BINARY" "$API_BINARY" "$JENTIC_INSTALL_DIR" >&2
  printf '    %s•%s prerequisites checked: git curl tar mktemp uname%s\n' \
    "$C_DIM" "$C_RESET" "" >&2
  if ! go_is_recent_enough; then
    printf '    %s•%s %sno suitable Go found — will download ~150 MB toolchain to %s%s\n' \
      "$C_DIM" "$C_RESET" "$C_YELLOW" "$TOOLCHAIN_DIR" "$C_RESET" >&2
  fi
  printf '\n' >&2
}

# --- platform detection -----------------------------------------------------
OS=""
ARCH=""

detect_platform() {
  step "Detecting platform"
  local uname_s uname_m
  uname_s="$(uname -s)"
  uname_m="$(uname -m)"

  case "$uname_s" in
    Linux)  OS="linux" ;;
    Darwin) OS="darwin" ;;
    MINGW* | MSYS* | CYGWIN*)
      die "Windows is not supported directly; please run this installer inside WSL" ;;
    *) die "unsupported operating system: $uname_s" ;;
  esac

  case "$uname_m" in
    x86_64 | amd64) ARCH="amd64" ;;
    arm64 | aarch64) ARCH="arm64" ;;
    *) die "unsupported architecture: $uname_m" ;;
  esac

  ok "Platform: ${C_BOLD}${OS}/${ARCH}${C_RESET}"
}

# --- Go toolchain -----------------------------------------------------------
# Path to the `go` binary we'll build with (resolved by ensure_go).
GO_BIN=""

# Returns 0 if the `go` on PATH satisfies the minimum version.
go_is_recent_enough() {
  command -v go >/dev/null 2>&1 || return 1
  local ver major minor
  # `go version` -> "go version go1.26.2 darwin/arm64"
  ver="$(go version 2>/dev/null | awk '{print $3}' | sed 's/^go//')"
  [ -n "$ver" ] || return 1
  major="${ver%%.*}"
  minor="$(printf '%s' "$ver" | cut -d. -f2)"
  [ -n "$major" ] && [ -n "$minor" ] || return 1
  if [ "$major" -gt "$GO_MIN_MAJOR" ]; then return 0; fi
  if [ "$major" -eq "$GO_MIN_MAJOR" ] && [ "$minor" -ge "$GO_MIN_MINOR" ]; then return 0; fi
  return 1
}

ensure_go() {
  step "Preparing Go toolchain"

  if go_is_recent_enough; then
    GO_BIN="$(command -v go)"
    ok "Using existing Go: ${C_BOLD}$($GO_BIN version | awk '{print $3}')${C_RESET}"
    return
  fi

  if command -v go >/dev/null 2>&1; then
    warn "Found $(go version | awk '{print $3}') but Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ is required."
  else
    info "Go not found on PATH."
  fi

  local local_go="$TOOLCHAIN_DIR/go/bin/go"
  if [ -x "$local_go" ]; then
    GO_BIN="$local_go"
    ok "Using previously downloaded Go: ${C_BOLD}$($GO_BIN version | awk '{print $3}')${C_RESET}"
    return
  fi

  local tarball url
  tarball="go${JENTIC_GO_VERSION}.${OS}-${ARCH}.tar.gz"
  url="https://go.dev/dl/${tarball}"

  mkdir -p "$TOOLCHAIN_DIR"
  rm -rf "$TOOLCHAIN_DIR/go"
  if ! spin "Downloading Go ${JENTIC_GO_VERSION} (${OS}/${ARCH})" \
        bash -c 'set -o pipefail; curl -fSL "$1" | tar -xz -C "$2"' _ "$url" "$TOOLCHAIN_DIR"; then
    die "failed to download or extract Go from $url"
  fi

  GO_BIN="$local_go"
  [ -x "$GO_BIN" ] || die "Go install appears incomplete: $GO_BIN not executable"
  ok "Installed Go: ${C_BOLD}$($GO_BIN version | awk '{print $3}')${C_RESET}"
}

# --- source fetch -----------------------------------------------------------
# highest_release_tag reads `git ls-remote --tags` output on stdin and prints
# the highest canonical release tag (vMAJOR.MINOR.PATCH, with its `v`). It
# mirrors the Go `highestReleaseTag` (cli/internal/update/version.go): it keeps
# only clean three-part `v` tags and deliberately ignores `cli/v*` noise tags,
# pre-releases (`v1.0.0-rc1`), and the peeled `^{}` lines ls-remote also prints.
# Returns non-zero (and prints nothing) when no release tag is present. Sorting
# is done field-by-field with a plain `sort` so it works on stock macOS bash 3.2
# / BSD tools (no `sort -V`).
highest_release_tag() {
  # Lines look like "<sha>\trefs/tags/<name>". Extract the tag name, keep only
  # canonical vX.Y.Z (anchored, so `cli/v*` and `-rc` suffixes are dropped),
  # strip the leading `v`, numeric-sort by each component, take the largest,
  # then re-add the `v`.
  local top
  top="$(
    sed -n 's|^.*refs/tags/\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$|\1|p' \
      | sed 's/^v//' \
      | sort -t. -k1,1n -k2,2n -k3,3n \
      | tail -n 1
  )"
  [ -n "$top" ] || return 1
  printf 'v%s\n' "$top"
}

# resolve_default_ref sets JENTIC_REF to the latest release tag when the caller
# left it unset. On any failure (no release tags — e.g. a fork — or a network
# error) it warns loudly and falls back to main so a build still happens. An
# explicit JENTIC_REF is honored verbatim and this is a no-op. git auth args are
# passed through so private repos resolve. Must run after the git auth setup in
# fetch_source.
resolve_default_ref() {
  local clone_url="$1"; shift
  local -a git_base_auth=("$@")   # git_base + git_auth, already assembled

  [ -n "$JENTIC_REF" ] && return 0

  local ls_out tag
  if ls_out="$(git "${git_base_auth[@]}" ls-remote --tags "$clone_url" 'v*' 2>/dev/null)" \
      && tag="$(printf '%s\n' "$ls_out" | highest_release_tag)"; then
    JENTIC_REF="$tag"
    ok "Latest release: ${C_BOLD}${JENTIC_REF}${C_RESET}"
    return 0
  fi

  JENTIC_REF="main"
  warn "Could not resolve a release tag in ${JENTIC_REPO} — building from ${C_BOLD}main${C_RESET}."
  warn "  Pin a specific build with ${C_BOLD}JENTIC_REF=<tag|branch|commit>${C_RESET}."
}

fetch_source() {
  step "Fetching source"
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/jentic-install.XXXXXX")"
  local repo_dir="$WORKDIR/repo"
  local clone_url="https://github.com/${JENTIC_REPO}.git"

  # Never fall back to an interactive credential prompt. We run inside a spinner
  # (and often behind `curl | sh`, where stdin is the script), so a prompt would
  # either hang forever or read garbage. Make git fail fast instead so the
  # GITHUB_TOKEN hint below can fire. credential.helper= also disables any
  # inherited helper (macOS keychain, git-credential-manager) that would prompt.
  export GIT_TERMINAL_PROMPT=0
  export GIT_ASKPASS=true
  local -a git_base=(-c "credential.helper=")

  # Pass the token via an in-memory http.extraheader so it never lands in the
  # remote URL, git config on disk, or process listings of the clone URL.
  local -a git_auth=()
  if [ -n "$GITHUB_TOKEN" ]; then
    # base64-encode "x-access-token:<token>" for HTTP Basic auth.
    local basic
    basic="$(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')"
    git_auth=(-c "http.extraheader=Authorization: Basic ${basic}")
  fi

  # Default the build ref to the latest release tag (unless the user pinned one).
  resolve_default_ref "$clone_url" "${git_base[@]}" ${git_auth[@]+"${git_auth[@]}"}

  if ! spin "Cloning ${JENTIC_REPO}@${JENTIC_REF}" \
        git "${git_base[@]}" ${git_auth[@]+"${git_auth[@]}"} clone \
          --depth 1 --filter=blob:none --sparse \
          --branch "$JENTIC_REF" \
          "$clone_url" "$repo_dir"; then
    if [ -z "$GITHUB_TOKEN" ]; then
      die "clone failed — check your network and that the ref '${JENTIC_REF}' exists in ${JENTIC_REPO}.
       If ${JENTIC_REPO} is a private fork, set a token with 'repo' read scope and retry:
       ${C_BOLD}GITHUB_TOKEN=ghp_xxx $0${C_RESET}"
    fi
    die "clone failed (check the ref '${JENTIC_REF}' and your token's access)."
  fi

  # Pass the auth header here too: the blob:none partial clone defers blob
  # downloads, so populating cli/ triggers a lazy promisor fetch that must
  # authenticate against a private repo.
  if ! spin "Checking out cli/" \
        git "${git_base[@]}" ${git_auth[@]+"${git_auth[@]}"} -C "$repo_dir" sparse-checkout set cli; then
    die "failed to sparse-checkout cli/"
  fi

  [ -f "$repo_dir/cli/go.mod" ] || die "cli/go.mod not found in fetched source"

  SRC_CLI_DIR="$repo_dir/cli"
  # Resolve the commit we actually built for version stamping.
  BUILT_COMMIT="$(git -C "$repo_dir" rev-parse --short HEAD 2>/dev/null || echo none)"
  ok "Source ready ${C_DIM}(${BUILT_COMMIT})${C_RESET}"
}

# --- download mode ----------------------------------------------------------
# The download path fetches the verified, prebuilt release archives goreleaser
# publishes (cli/.goreleaser.yaml per-binary archives) instead of compiling from
# source. It reuses install_binary / PATH-wiring / write_manifest unchanged —
# only the "produce the binaries in $WORKDIR" step differs.

# download_selected_binaries prints the binary names the download path should
# fetch, honouring JENTIC_INSTALL_BINARIES (jentic-only default; `both` adds
# jenticctl). Kept a function so main() and the manifest logic agree.
download_selected_binaries() {
  case "$JENTIC_INSTALL_BINARIES" in
    both) printf '%s\n%s\n' "$API_BINARY" "$CTL_BINARY" ;;
    *)    printf '%s\n' "$API_BINARY" ;;
  esac
}

# asset_name <binary> prints the release archive filename for the current
# OS/ARCH, byte-identical to the Go update.AssetName helper and the goreleaser
# name_template (`<binary>_{{.Version}}_{{.Os}}_{{.Arch}}`). goreleaser's
# {{.Version}} is the tag WITHOUT the leading `v`, so we strip it. windows would
# switch to .zip, but the installer refuses Windows in detect_platform, so every
# path here is .tar.gz.
asset_name() {
  local binary="$1" ver="${JENTIC_REF#v}"
  printf '%s_%s_%s_%s.tar.gz' "$binary" "$ver" "$OS" "$ARCH"
}

# release_asset_url <asset> prints the GitHub release download URL for an asset
# of the resolved tag.
release_asset_url() {
  printf 'https://github.com/%s/releases/download/%s/%s' "$JENTIC_REPO" "$JENTIC_REF" "$1"
}

# curl_asset <url> <dest> downloads a release asset. Fails (non-zero) on any
# HTTP error. Attaches the GitHub token only for github.com URLs, via an
# in-memory header — never on disk, matching the clone path's token hygiene.
curl_asset() {
  local url="$1" dest="$2"
  local -a auth=()
  if [ -n "$GITHUB_TOKEN" ]; then
    case "$url" in
      https://github.com/*|https://*.githubusercontent.com/*)
        auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}") ;;
    esac
  fi
  # ${auth[@]+…} guard: macOS stock bash 3.2 treats an EMPTY array expansion as
  # an unbound variable under `set -u` (same idiom as the git_auth call sites).
  curl -fSL ${auth[@]+"${auth[@]}"} -o "$dest" -- "$url"
}

# sha256_file <path> prints the lowercase hex sha256 of a file, using whichever
# tool is present (sha256sum on Linux, shasum -a 256 on macOS).
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# verify_checksum <asset> <archive_path> <checksums_path> enforces the sha256
# gate FAIL-CLOSED: it greps the asset's own line out of checksums.txt and
# compares that exact expected digest. A missing line (grep finds nothing) is a
# hard error — never a vacuous pass (the trap `sha256sum --check --ignore-missing`
# falls into).
verify_checksum() {
  local asset="$1" archive="$2" sums="$3"
  local expected actual
  expected="$(awk -v f="$asset" '$2 == f || $2 == "*"f {print $1; exit}' "$sums")"
  if [ -z "$expected" ]; then
    die "checksums.txt has no entry for ${asset} — refusing to install unverified bytes"
  fi
  actual="$(sha256_file "$archive")"
  if [ "$expected" != "$actual" ]; then
    die "checksum mismatch for ${asset}: expected ${expected}, got ${actual} — aborting"
  fi
}

# verify_cosign <checksums> <sig> <cert> verifies the release signature over
# checksums.txt when cosign is on PATH. cosign absent → loud warning but the
# sha256 gate above already held. cosign present but verification fails → hard
# error (fail-closed). Mirrors docs/releasing.md and the Go updater.
verify_cosign() {
  local sums="$1" sig="$2" cert="$3"
  if ! command -v cosign >/dev/null 2>&1; then
    warn "cosign not found — signature NOT verified (sha256 verified). Install cosign to verify the release signature."
    return 0
  fi
  if ! cosign verify-blob \
        --certificate "$cert" \
        --signature "$sig" \
        --certificate-identity-regexp "$COSIGN_CERT_IDENTITY_REGEXP" \
        --certificate-oidc-issuer "$COSIGN_OIDC_ISSUER" \
        "$sums" >/dev/null 2>&1; then
    die "cosign signature verification failed for checksums.txt — aborting"
  fi
  ok "cosign signature verified"
}

# release_assets_exist reports (0/non-zero) whether the resolved tag has the
# expected jentic archive published — the gate `auto` uses to choose download vs
# source. A HEAD against the archive URL is enough; we don't download here.
release_assets_exist() {
  local url
  url="$(release_asset_url "$(asset_name "$API_BINARY")")"
  local -a auth=()
  if [ -n "$GITHUB_TOKEN" ]; then
    auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
  fi
  # Same empty-array-under-`set -u` guard as curl_asset (macOS bash 3.2).
  curl -fsSL ${auth[@]+"${auth[@]}"} -I -o /dev/null -- "$url" 2>/dev/null
}

# download_binaries fetches + verifies the selected release archives and unpacks
# their binaries into $WORKDIR (the same location build() writes to), so the
# downstream install_binary/verify/manifest steps are identical to the source
# path. Fail-closed: any checksum/signature problem aborts.
download_binaries() {
  step "Downloading verified release binaries"
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/jentic-install.XXXXXX")"
  local dl="$WORKDIR/dl"
  mkdir -p "$dl"

  # checksums.txt + its cosign sig/cert cover ALL archives in the release.
  local sums="$dl/checksums.txt" sig="$dl/checksums.txt.sig" cert="$dl/checksums.txt.pem"
  if ! spin "Fetching checksums.txt" curl_asset "$(release_asset_url checksums.txt)" "$sums"; then
    die "failed to download checksums.txt for ${JENTIC_REF} — is it a published release?"
  fi
  # Signature/cert are best-effort to fetch; verify_cosign decides enforcement.
  curl_asset "$(release_asset_url checksums.txt.sig)" "$sig" 2>/dev/null || true
  curl_asset "$(release_asset_url checksums.txt.pem)" "$cert" 2>/dev/null || true
  if [ -s "$sig" ] && [ -s "$cert" ]; then
    verify_cosign "$sums" "$sig" "$cert"
  else
    warn "release signature/cert not available — verified sha256 only"
  fi

  local name asset archive
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    asset="$(asset_name "$name")"
    archive="$dl/$asset"
    if ! spin "Fetching ${asset}" curl_asset "$(release_asset_url "$asset")" "$archive"; then
      die "failed to download ${asset} — no matching release asset for ${OS}/${ARCH}"
    fi
    verify_checksum "$asset" "$archive" "$sums"
    if ! tar -xzf "$archive" -C "$WORKDIR" "$name" 2>/dev/null; then
      # Fall back to extracting whatever binary the archive contains, then
      # rename — but the archives are single-binary and named after it, so the
      # explicit member above is the normal path.
      die "archive ${asset} did not contain expected binary ${name}"
    fi
    chmod 0755 "$WORKDIR/$name" 2>/dev/null || true
    ok "Verified ${C_BOLD}${name}${C_RESET} ${C_DIM}(sha256 ok)${C_RESET}"
  done < <(download_selected_binaries)

  BUILT_COMMIT="none"
  rm -rf "$dl"
}

# --- build ------------------------------------------------------------------
# Built and installed binaries live at deterministic, name-derived paths:
# built    -> $WORKDIR/<name>
# installed-> $JENTIC_INSTALL_DIR/<name>
# We use helpers rather than associative arrays so the installer runs on the
# stock macOS bash (3.2), which lacks `declare -A`.
built_binary_path() { printf '%s/%s' "$WORKDIR" "$1"; }
installed_binary_path() { printf '%s/%s' "$JENTIC_INSTALL_DIR" "$1"; }

build() {
  step "Building ${CTL_BINARY} + ${API_BINARY}"
  local pkg="github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
  local date_now
  date_now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  local ldflags="-s -w"
  ldflags="$ldflags -X ${pkg}.version=${JENTIC_REF}"
  ldflags="$ldflags -X ${pkg}.commit=${BUILT_COMMIT:-none}"
  ldflags="$ldflags -X ${pkg}.date=${date_now}"

  local name out pkgpath
  for name in "${BINARY_NAMES[@]}"; do
    out="$WORKDIR/${name}"
    pkgpath="$(cmd_pkg_path "$name")"
    # GOTOOLCHAIN=auto lets Go fetch the exact toolchain named in go.mod if the
    # one we resolved is older than required.
    if ! spin "Compiling ${name}" \
          bash -c 'cd "$1" && GOTOOLCHAIN=auto GOFLAGS=-mod=mod "$2" build -trimpath -ldflags "$3" -o "$4" "$5"' \
          _ "$SRC_CLI_DIR" "$GO_BIN" "$ldflags" "$out" "$pkgpath"; then
      die "build failed for ${name}"
    fi
  done
  ok "Compiled ${C_BOLD}${CTL_BINARY}${C_RESET} + ${C_BOLD}${API_BINARY}${C_RESET}"
}

# --- install ----------------------------------------------------------------
# INSTALLED_PATH is the primary (jenticctl) path, used for the manifest and the
# install chain. Per-binary installed paths are derived via
# installed_binary_path().
INSTALLED_PATH=""

install_binary() {
  step "Installing"
  mkdir -p "$JENTIC_INSTALL_DIR"

  local name src dest
  for name in "${INSTALL_SET[@]}"; do
    src="$(built_binary_path "$name")"
    dest="$(installed_binary_path "$name")"
    install -m 0755 "$src" "$dest" 2>/dev/null || {
      cp "$src" "$dest" && chmod 0755 "$dest"
    }
    ok "Installed ${name} ${C_DIM}->${C_RESET} ${dest}"
  done
  # Primary path for the manifest/chain: jenticctl when it's in the set,
  # otherwise the first installed binary (jentic-only download).
  if printf '%s\n' "${INSTALL_SET[@]}" | grep -qx "$CTL_BINARY"; then
    INSTALLED_PATH="$(installed_binary_path "$CTL_BINARY")"
  else
    INSTALLED_PATH="$(installed_binary_path "${INSTALL_SET[0]}")"
  fi

  # If the install dir isn't already on PATH, make it reachable. First try
  # symlinking both binaries into a conventional dir that's already on PATH
  # (no rc edits, effective in the current shell). If that isn't possible,
  # persist the dir onto PATH by appending an idempotent export block to the
  # user's shell rc file, then print the manual fallback either way.
  if ! path_contains "$JENTIC_INSTALL_DIR"; then
    if link_into_path; then
      PATH_LINKED=1
    else
      ensure_path_in_rc
      print_path_hint
    fi
  fi
}

path_contains() {
  case ":${PATH}:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

# link_into_path symlinks every installed binary into the first writable,
# on-PATH conventional directory. Succeeds only if all links are created.
link_into_path() {
  local dir name target linked=0
  for dir in "/usr/local/bin" "$HOME/.local/bin"; do
    if path_contains "$dir" && [ -w "$dir" ]; then
      linked=1
      for name in "${INSTALL_SET[@]}"; do
        target="$(installed_binary_path "$name")"
        if ln -sf "$target" "$dir/${name}"; then
          ok "Linked ${dir}/${name} ${C_DIM}->${C_RESET} ${target}"
        else
          linked=0
        fi
      done
      [ "$linked" = 1 ] && return 0
    fi
  done
  return 1
}

# Sentinel that guards the PATH block we manage in the user's rc file, so a
# re-install updates in place instead of appending a duplicate export.
JENTIC_RC_MARKER="# added by jentic installer (https://github.com/jentic/jentic-one)"

# rc_files_for_shell prints the rc file(s) we should edit for the user's login
# shell, most-preferred first. zsh reads ~/.zshrc for interactive shells; bash
# reads ~/.bashrc (Linux interactive) and, on login shells (notably macOS
# Terminal), ~/.bash_profile — we touch both so the change takes regardless of
# how bash is launched.
rc_files_for_shell() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"
  case "$shell_name" in
    zsh)  printf '%s\n' "$HOME/.zshrc" ;;
    bash) printf '%s\n' "$HOME/.bashrc" "$HOME/.bash_profile" ;;
    *)
      # Unknown/other shell: fall back to whichever common rc already exists,
      # else ~/.profile which POSIX login shells source.
      if [ -f "$HOME/.zshrc" ]; then printf '%s\n' "$HOME/.zshrc"
      elif [ -f "$HOME/.bashrc" ]; then printf '%s\n' "$HOME/.bashrc"
      else printf '%s\n' "$HOME/.profile"; fi
      ;;
  esac
}

# ensure_path_in_rc appends an idempotent block that puts JENTIC_INSTALL_DIR on
# PATH to the appropriate shell rc file(s). It never duplicates: a file already
# containing our marker is left untouched. Best-effort — a failure to write is
# non-fatal (the printed hint still tells the user exactly what to add).
ensure_path_in_rc() {
  local rc export_line appended=0 already=0
  export_line="export PATH=\"${JENTIC_INSTALL_DIR}:\$PATH\""

  while IFS= read -r rc; do
    [ -n "$rc" ] || continue
    if [ -f "$rc" ] && grep -qF "$JENTIC_RC_MARKER" "$rc" 2>/dev/null; then
      # Already managed by a previous install — don't append again.
      already=1
      continue
    fi
    if {
      printf '\n%s\n' "$JENTIC_RC_MARKER"
      printf '%s\n' "$export_line"
    } >> "$rc" 2>/dev/null; then
      ok "Added ${JENTIC_INSTALL_DIR} to PATH in ${C_BOLD}${rc}${C_RESET}"
      appended=1
    fi
  done < <(rc_files_for_shell)

  # A fresh append means the user must restart/source; only report the
  # "already present" state when nothing new was written.
  if [ "$appended" = 1 ]; then
    RC_UPDATED=1
  elif [ "$already" = 1 ]; then
    RC_ALREADY_HAD_PATH=1
  fi
}

print_path_hint() {
  warn "${JENTIC_INSTALL_DIR} is not on your PATH."
  if [ "${RC_UPDATED:-0}" = 1 ]; then
    printf '\n  It has been added to your shell profile. To use %sjenticctl%s / %sjentic%s now,\n' \
      "$C_BOLD" "$C_RESET" "$C_BOLD" "$C_RESET" >&2
    printf '  restart your terminal or run:\n\n' >&2
    printf '    %sexport PATH="%s:$PATH"%s\n\n' "$C_BOLD" "$JENTIC_INSTALL_DIR" "$C_RESET" >&2
  else
    printf '\n  Add it by appending this to your shell profile (~/.bashrc, ~/.zshrc)\n' >&2
    printf '  and restarting your terminal:\n\n' >&2
    printf '    %sexport PATH="%s:$PATH"%s\n\n' "$C_BOLD" "$JENTIC_INSTALL_DIR" "$C_RESET" >&2
  fi
}

# --- manifest ---------------------------------------------------------------
# Record what we installed (~/.jentic/install.json) so `jenticctl update` knows
# which repo/ref/commit to track. We write the CLI fields here; `jenticctl install`
# fills in the stack fields (mode, db). binary_path records the primary
# (jenticctl) binary; the sibling jentic binary is co-located in the same dir.
# Preserve any previously recorded stack-owned fields (mode/db/broker_port and
# stack_ref) so a CLI-only re-install doesn't wipe the stack metadata.
write_manifest() {
  local home_dir manifest now prev_mode prev_db prev_broker_port prev_stack_ref
  home_dir="${JENTIC_HOME:-$HOME/.jentic}"
  manifest="$home_dir/install.json"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  prev_mode=""
  prev_db=""
  prev_broker_port=""
  prev_stack_ref=""
  if [ -f "$manifest" ]; then
    prev_mode="$(manifest_field "$manifest" mode)"
    prev_db="$(manifest_field "$manifest" db)"
    prev_broker_port="$(manifest_field "$manifest" broker_port)"
    # stack_ref records what the *stack* was last built from. This script only
    # ever builds the CLI binaries, so carrying it over is what stops a CLI
    # update from advertising the stack as current (jentic-one#943). Losing it
    # would silently re-wedge users on a stale stack.
    prev_stack_ref="$(manifest_field "$manifest" stack_ref)"
    # Backfill for manifests written before stack_ref existed. Such a manifest
    # records the stack's build only in `ref`, which we are about to overwrite
    # with the newly installed CLI ref. Without this, the old value is lost and
    # ResolvedStackRef() falls back to the *new* `ref`, so a stale stack reports
    # itself current — re-creating #943 on the very first CLI-only re-install,
    # which is exactly when it bites. `mode` is written by `jenticctl install`,
    # so its presence is what distinguishes "a stack was installed at this ref"
    # from a CLI-only install that never built one.
    if [ -z "$prev_stack_ref" ] && [ -n "$prev_mode" ]; then
      prev_stack_ref="$(manifest_field "$manifest" ref)"
    fi
  fi

  mkdir -p "$home_dir"
  {
    printf '{\n'
    printf '  "repo": "%s",\n' "$JENTIC_REPO"
    printf '  "ref": "%s",\n' "$JENTIC_REF"
    if [ -n "$prev_stack_ref" ]; then printf '  "stack_ref": "%s",\n' "$prev_stack_ref"; fi
    printf '  "commit": "%s",\n' "${BUILT_COMMIT:-none}"
    printf '  "cli_version": "%s",\n' "$JENTIC_REF"
    printf '  "binary_path": "%s",\n' "$INSTALLED_PATH"
    if [ -n "$prev_mode" ]; then printf '  "mode": "%s",\n' "$prev_mode"; fi
    if [ -n "$prev_db" ]; then printf '  "db": "%s",\n' "$prev_db"; fi
    if [ -n "$prev_broker_port" ]; then printf '  "broker_port": "%s",\n' "$prev_broker_port"; fi
    printf '  "installed_at": "%s"\n' "$now"
    printf '}\n'
  } > "$manifest"
  chmod 0600 "$manifest" 2>/dev/null || true
  ok "Recorded manifest ${C_DIM}->${C_RESET} ${manifest}"
}

# manifest_field extracts a top-level string value from the install manifest by
# key. Kept to sed so the installer stays dependency-free (no jq).
manifest_field() {
  sed -n 's/.*"'"$2"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" | head -n1
}

# --- verify -----------------------------------------------------------------
verify() {
  step "Verifying"
  local name path
  for name in "${INSTALL_SET[@]}"; do
    path="$(installed_binary_path "$name")"
    if "$path" --version >/dev/null 2>&1; then
      ok "$("$path" --version | head -n 1)"
    else
      warn "Installed ${name} did not respond to --version; it may still work."
    fi
  done
}

banner() {
  local has_ctl=0
  printf '%s\n' "${INSTALL_SET[@]}" | grep -qx "$CTL_BINARY" && has_ctl=1

  if [ "$has_ctl" = 1 ]; then
    printf '\n  %s%s✓ %s + %s installed%s\n\n' \
      "$C_BOLD" "$C_GREEN" "$CTL_BINARY" "$API_BINARY" "$C_RESET" >&2
    printf '  %sjenticctl%s  %s\n' "$C_DIM" "$C_RESET" "$(installed_binary_path "$CTL_BINARY")" >&2
    printf '  %sjentic%s     %s\n' "$C_DIM" "$C_RESET" "$(installed_binary_path "$API_BINARY")" >&2
  else
    printf '\n  %s%s✓ %s installed%s\n\n' \
      "$C_BOLD" "$C_GREEN" "$API_BINARY" "$C_RESET" >&2
    printf '  %sjentic%s     %s\n' "$C_DIM" "$C_RESET" "$(installed_binary_path "$API_BINARY")" >&2
  fi

  # next-step string differs by what we installed: with jenticctl the natural
  # next step is standing up the local stack; a jentic-only download is meant to
  # talk to a REMOTE server, so point at `jentic register`.
  local next_cmd next_note
  if [ "$has_ctl" = 1 ]; then
    next_cmd="jenticctl install"; next_note="# configure & onboard the stack"
  else
    next_cmd="jentic register --url https://<server>"; next_note="# connect to a remote jentic server"
  fi

  # If the install dir is on PATH now (either it always was, or we symlinked
  # into an on-PATH dir), the binaries are reachable by name — say so quietly.
  # Otherwise surface an unmissable block telling the user exactly how to make
  # them reachable, distinguishing the "we edited your rc" case from the pure
  # manual one so the instruction matches reality.
  if path_contains "$JENTIC_INSTALL_DIR" || [ "${PATH_LINKED:-0}" = 1 ]; then
    printf '  %snext%s       %s%s%s %s%s%s\n' \
      "$C_DIM" "$C_RESET" "$C_BRAND" "$next_cmd" "$C_RESET" "$C_DIM" "$next_note" "$C_RESET" >&2
    return
  fi

  printf '\n  %s%s! the installed binaries are not on your PATH yet.%s\n' \
    "$C_BOLD" "$C_YELLOW" "$C_RESET" >&2
  if [ "${RC_UPDATED:-0}" = 1 ] || [ "${RC_ALREADY_HAD_PATH:-0}" = 1 ]; then
    printf '  Your shell profile has been updated. Restart your terminal, or run:\n\n' >&2
  else
    printf '  Add the install dir to your shell profile (~/.bashrc, ~/.zshrc), then\n' >&2
    printf '  restart your terminal. For this shell right now, run:\n\n' >&2
  fi
  printf '    %sexport PATH="%s:$PATH"%s\n\n' "$C_BOLD" "$JENTIC_INSTALL_DIR" "$C_RESET" >&2
  printf '  %sthen%s       %s%s%s %s%s%s\n' \
    "$C_DIM" "$C_RESET" "$C_BRAND" "$next_cmd" "$C_RESET" "$C_DIM" "$next_note" "$C_RESET" >&2
}

# --- chain into the stack wizard --------------------------------------------
# With the binaries installed, flow straight into `jenticctl install` (the stack
# configuration wizard) so onboarding is one continuous experience. The wizard
# needs an interactive terminal: when stdin is a TTY we inherit it; under
# `curl ... | sh` stdin is the script itself, so we fall back to /dev/tty when
# the keyboard is reachable. When neither is available (CI / no TTY) or
# JENTIC_NO_INSTALL=1 is set, we return non-zero so the caller prints the
# next-step hint instead. exec replaces this shell (the EXIT trap won't fire),
# so we run cleanup and clear the trap first.
chain_install() {
  [ "${JENTIC_NO_INSTALL:-0}" = 1 ] && return 1

  # No jenticctl in the install set (jentic-only download) — nothing to chain
  # into. The banner tells the user how to use `jentic` against a remote server.
  if ! printf '%s\n' "${INSTALL_SET[@]}" | grep -qx "$CTL_BINARY"; then
    return 1
  fi

  local stdin_src
  if [ -t 0 ]; then
    stdin_src="inherit"
  elif [ -t 1 ] && [ -r /dev/tty ]; then
    stdin_src="/dev/tty"
  else
    # No interactive terminal (CI, or `curl ... | sh` with stdout not a TTY).
    # Do NOT block on a wizard that can't read input. Print the exact
    # non-interactive next step and the opt-out so the run is actionable, then
    # let main() fall through to banner(). The headless server command is
    # `jenticctl install --defaults` (NOT --no-wizard: that flag only skips the
    # post-install "continue to guided setup?" prompt, it does not make install
    # itself non-interactive).
    printf '\n  %s%s! No interactive terminal — skipping the guided stack setup.%s\n' \
      "$C_BOLD" "$C_YELLOW" "$C_RESET" >&2
    printf '  Configure the stack non-interactively with:\n\n' >&2
    printf '    %s%s install --defaults%s\n\n' "$C_BOLD" "$CTL_BINARY" "$C_RESET" >&2
    printf '  %s(or set JENTIC_NO_INSTALL=1 to stop after installing the binaries.)%s\n' \
      "$C_DIM" "$C_RESET" >&2
    return 1
  fi

  printf '\n  %s%s✓ %s + %s installed%s %s— configuring the stack ...%s\n\n' \
    "$C_BOLD" "$C_GREEN" "$CTL_BINARY" "$API_BINARY" "$C_RESET" "$C_DIM" "$C_RESET" >&2

  show_cursor
  cleanup
  trap - EXIT INT TERM

  if [ "$stdin_src" = "/dev/tty" ]; then
    exec "$(installed_binary_path "$CTL_BINARY")" install < /dev/tty
  fi
  exec "$(installed_binary_path "$CTL_BINARY")" install
}

# resolve_ref_for_download resolves JENTIC_REF to the latest release tag when
# unset, using a bare `git ls-remote --tags` (git is a checked prereq). Unlike
# fetch_source's resolve_default_ref it does not fall back to `main` — a download
# needs a real release tag with published assets; if none resolves it returns
# non-zero so main() can fall back to the source build. Honors GITHUB_TOKEN for
# private forks via an in-memory http.extraheader (no on-disk token).
resolve_ref_for_download() {
  [ -n "$JENTIC_REF" ] && return 0
  local clone_url="https://github.com/${JENTIC_REPO}.git"
  local -a git_auth=()
  if [ -n "$GITHUB_TOKEN" ]; then
    local basic
    basic="$(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')"
    git_auth=(-c "http.extraheader=Authorization: Basic ${basic}")
  fi
  local ls_out tag
  if ls_out="$(git -c "credential.helper=" ${git_auth[@]+"${git_auth[@]}"} \
        ls-remote --tags "$clone_url" 'v*' 2>/dev/null)" \
      && tag="$(printf '%s\n' "$ls_out" | highest_release_tag)"; then
    JENTIC_REF="$tag"
    return 0
  fi
  return 1
}

# provision_via_download attempts the download path end to end: resolve the tag,
# narrow INSTALL_SET to the selected binaries, fetch+verify+unpack into $WORKDIR.
# Returns 0 on success (main() then runs the shared install/verify/manifest
# steps). Returns non-zero WITHOUT installing anything when the download can't
# proceed (no release tag, or `auto` and no published asset) so the caller can
# fall back to source — unless the method was forced to `binary`, in which case
# an unmet precondition is fatal (die).
provision_via_download() {
  local forced="$1"   # 1 when JENTIC_INSTALL_METHOD=binary (no source fallback)
  if ! resolve_ref_for_download; then
    if [ "$forced" = 1 ]; then
      die "JENTIC_INSTALL_METHOD=binary but no release tag resolved in ${JENTIC_REPO} (set JENTIC_REF=<tag>)"
    fi
    return 1
  fi
  if [ "$forced" != 1 ] && ! release_assets_exist; then
    # auto mode, tag has no matching asset (fork / unreleased) — fall back.
    return 1
  fi
  # Narrow the installed/verified set to the download selection.
  INSTALL_SET=()
  local n
  while IFS= read -r n; do [ -n "$n" ] && INSTALL_SET+=("$n"); done < <(download_selected_binaries)
  download_binaries
}

# --- main -------------------------------------------------------------------
main() {
  logo
  check_prereqs
  STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jentic-install-state.XXXXXX")"
  STEP_LOG="$STATE_DIR/step.log"
  detect_platform

  # Decide the acquisition method. `binary` forces download (fatal if no asset);
  # `source` forces the historical from-source build; `auto` prefers a verified
  # download when a release asset exists and falls back to source otherwise.
  local used_download=0
  case "$JENTIC_INSTALL_METHOD" in
    binary)
      if provision_via_download 1; then used_download=1; fi
      ;;
    source)
      : # fall through to source build below
      ;;
    auto|"")
      if provision_via_download 0; then used_download=1; fi
      ;;
    *)
      die "unknown JENTIC_INSTALL_METHOD='${JENTIC_INSTALL_METHOD}' (expected: auto | binary | source)"
      ;;
  esac

  if [ "$used_download" != 1 ]; then
    # Source path: build BOTH binaries (INSTALL_SET already defaults to both).
    INSTALL_SET=("${BINARY_NAMES[@]}")
    plan_summary
    ensure_go
    fetch_source
    build
  fi

  install_binary
  write_manifest
  verify
  # Hand off into the stack wizard when we have an interactive terminal;
  # otherwise (CI / piped with no TTY / JENTIC_NO_INSTALL=1) fall through to
  # the next-step hint. chain_install execs on success and never returns.
  chain_install || true
  banner
}

# Run the installer unless the script is being sourced (e.g. by a test harness
# that exercises individual functions like ensure_path_in_rc). When sourced,
# BASH_SOURCE[0] differs from $0, so we define the functions and stop. The
# `:-` default keeps this safe under `set -u` when run via stdin (no
# BASH_SOURCE), in which case we treat it as a direct run.
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
