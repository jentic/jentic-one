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
#   JENTIC_REF          branch, tag, or commit to build  (default: main)
#   JENTIC_INSTALL_DIR  where to install the binaries     (default: ~/.jentic/bin)
#   JENTIC_GO_VERSION   Go to download if none suitable   (default: 1.26.2)
#   GITHUB_TOKEN        token for cloning a private fork  (default: unset/anonymous)
#   JENTIC_NO_INSTALL   set to 1 to stop after installing the binaries, skipping
#                       the automatic hand-off into `jenticctl install`
#
# This script is invoked via `curl ... | sh`, so it re-execs itself under a
# full (non-POSIX) bash (below) to get predictable behavior across shells.
# Note: on macOS `/bin/sh` is bash in POSIX mode — BASH_VERSION is set but
# bash-only syntax (process substitution, etc.) is disabled — so we detect that
# via SHELLOPTS and re-exec too. When piped (`curl | sh`) there is no script
# file at $0, so we spool the source to a temp file before re-execing.

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
  # shell, not this script — spool stdin to a temp file and run bash on it. Run
  # (not exec) so we can clean up the temp file; propagate bash's exit code.
  _reexec_tmp="$(mktemp "${TMPDIR:-/tmp}/jentic-install.XXXXXX")" || {
    echo "error: could not create a temp file to re-exec the installer" >&2
    exit 1
  }
  cat > "$_reexec_tmp"
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
JENTIC_REF="${JENTIC_REF:-main}"
JENTIC_INSTALL_DIR="${JENTIC_INSTALL_DIR:-$HOME/.jentic/bin}"
JENTIC_GO_VERSION="${JENTIC_GO_VERSION:-1.26.2}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

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

check_prereqs() {
  need git
  need curl
  need tar
  need mktemp
  need uname
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
  local pkg="github.com/jentic/jentic-one/cli/internal/cmd"
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
  for name in "${BINARY_NAMES[@]}"; do
    src="$(built_binary_path "$name")"
    dest="$(installed_binary_path "$name")"
    install -m 0755 "$src" "$dest" 2>/dev/null || {
      cp "$src" "$dest" && chmod 0755 "$dest"
    }
    ok "Installed ${name} ${C_DIM}->${C_RESET} ${dest}"
  done
  INSTALLED_PATH="$(installed_binary_path "$CTL_BINARY")"

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
      for name in "${BINARY_NAMES[@]}"; do
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
# Preserve any previously recorded mode/db so a CLI-only re-install doesn't wipe
# the stack metadata.
write_manifest() {
  local home_dir manifest now prev_mode prev_db
  home_dir="${JENTIC_HOME:-$HOME/.jentic}"
  manifest="$home_dir/install.json"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  prev_mode=""
  prev_db=""
  if [ -f "$manifest" ]; then
    prev_mode="$(sed -n 's/.*"mode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" | head -n1)"
    prev_db="$(sed -n 's/.*"db"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" | head -n1)"
  fi

  mkdir -p "$home_dir"
  {
    printf '{\n'
    printf '  "repo": "%s",\n' "$JENTIC_REPO"
    printf '  "ref": "%s",\n' "$JENTIC_REF"
    printf '  "commit": "%s",\n' "${BUILT_COMMIT:-none}"
    printf '  "cli_version": "%s",\n' "$JENTIC_REF"
    printf '  "binary_path": "%s",\n' "$INSTALLED_PATH"
    if [ -n "$prev_mode" ]; then printf '  "mode": "%s",\n' "$prev_mode"; fi
    if [ -n "$prev_db" ]; then printf '  "db": "%s",\n' "$prev_db"; fi
    printf '  "installed_at": "%s"\n' "$now"
    printf '}\n'
  } > "$manifest"
  chmod 0600 "$manifest" 2>/dev/null || true
  ok "Recorded manifest ${C_DIM}->${C_RESET} ${manifest}"
}

# --- verify -----------------------------------------------------------------
verify() {
  step "Verifying"
  local name path
  for name in "${BINARY_NAMES[@]}"; do
    path="$(installed_binary_path "$name")"
    if "$path" --version >/dev/null 2>&1; then
      ok "$("$path" --version | head -n 1)"
    else
      warn "Installed ${name} did not respond to --version; it may still work."
    fi
  done
}

banner() {
  printf '\n  %s%s✓ %s + %s installed%s\n\n' \
    "$C_BOLD" "$C_GREEN" "$CTL_BINARY" "$API_BINARY" "$C_RESET" >&2
  printf '  %sjenticctl%s  %s\n' "$C_DIM" "$C_RESET" "$(installed_binary_path "$CTL_BINARY")" >&2
  printf '  %sjentic%s     %s\n' "$C_DIM" "$C_RESET" "$(installed_binary_path "$API_BINARY")" >&2

  # If the install dir is on PATH now (either it always was, or we symlinked
  # into an on-PATH dir), the binaries are reachable by name — say so quietly.
  # Otherwise surface an unmissable block telling the user exactly how to make
  # them reachable, distinguishing the "we edited your rc" case from the pure
  # manual one so the instruction matches reality.
  if path_contains "$JENTIC_INSTALL_DIR" || [ "${PATH_LINKED:-0}" = 1 ]; then
    printf '  %snext%s       %sjenticctl install%s %s# configure & onboard the stack%s\n' \
      "$C_DIM" "$C_RESET" "$C_BRAND" "$C_RESET" "$C_DIM" "$C_RESET" >&2
    return
  fi

  printf '\n  %s%s! %sjenticctl%s / %sjentic%s are not on your PATH yet.%s\n' \
    "$C_BOLD" "$C_YELLOW" "$C_RESET$C_BOLD" "$C_YELLOW$C_BOLD" "$C_RESET$C_BOLD" "$C_YELLOW$C_BOLD" "$C_RESET" >&2
  if [ "${RC_UPDATED:-0}" = 1 ] || [ "${RC_ALREADY_HAD_PATH:-0}" = 1 ]; then
    printf '  Your shell profile has been updated. Restart your terminal, or run:\n\n' >&2
  else
    printf '  Add the install dir to your shell profile (~/.bashrc, ~/.zshrc), then\n' >&2
    printf '  restart your terminal. For this shell right now, run:\n\n' >&2
  fi
  printf '    %sexport PATH="%s:$PATH"%s\n\n' "$C_BOLD" "$JENTIC_INSTALL_DIR" "$C_RESET" >&2
  printf '  %sthen%s       %sjenticctl install%s %s# configure & onboard the stack%s\n' \
    "$C_DIM" "$C_RESET" "$C_BRAND" "$C_RESET" "$C_DIM" "$C_RESET" >&2
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

  local stdin_src
  if [ -t 0 ]; then
    stdin_src="inherit"
  elif [ -t 1 ] && [ -r /dev/tty ]; then
    stdin_src="/dev/tty"
  else
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

# --- main -------------------------------------------------------------------
main() {
  logo
  check_prereqs
  STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jentic-install-state.XXXXXX")"
  STEP_LOG="$STATE_DIR/step.log"
  detect_platform
  ensure_go
  fetch_source
  build
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
