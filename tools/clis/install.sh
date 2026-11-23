#!/bin/sh
#
# Installs the jentic and jenticctl CLIs from a GitHub release (latest by default).
#
#   curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/clis/install.sh | sh
#
# Pin a release with JENTIC_VERSION (e.g. JENTIC_VERSION=v0.24.0).
#
# Download-only wrapper around tools/install.sh: prebuilt release binaries,
# no source build, no hand-off into `jenticctl install`.
set -eu

# JENTIC_CLIS_INSTALLER_SELF_ID: marker for the checkout check below. Do not remove.

export JENTIC_INSTALL_METHOD=binary
export JENTIC_NO_INSTALL=1
if [ -n "${JENTIC_VERSION:-}" ]; then
  case "$JENTIC_VERSION" in
    v*) JENTIC_REF="$JENTIC_VERSION" ;;
    *) JENTIC_REF="v$JENTIC_VERSION" ;;
  esac
  export JENTIC_REF
fi

# From a checkout: run the sibling installer directly. When piped, $0 is the
# shell, so the marker check keeps us off unrelated files near the cwd.
if [ -f "$0" ] && grep -q JENTIC_CLIS_INSTALLER_SELF_ID "$0" 2>/dev/null; then
  here="$(cd "$(dirname "$0")" && pwd)"
  if [ -f "$here/../install.sh" ]; then
    exec bash "$here/../install.sh" "$@"
  fi
fi

# Piped from curl: fetch the installer once and run it under bash.
tmp="$(mktemp "${TMPDIR:-/tmp}/jentic-install.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
curl -fsSL -o "$tmp" -- "https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh"
bash "$tmp" "$@"
