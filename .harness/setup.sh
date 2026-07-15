#!/usr/bin/env bash
# Runs inside the harness runlet container, on the per-run Docker network.
# Docker service names (postgres, redis) resolve as hostnames here.
#
# Toolchain provisioning (mise install, uv sync) is handled by the harness
# container image / lifecycle, not by this script. This script's job is to
# perform any per-run, repo-specific setup the harness can't do generically.
#
# The harness handles compose up/down — do not invoke docker here.
set -euo pipefail

# HARNESS_ENV_FILE is a sourced shell file the harness uses to propagate
# environment variables from this script back to subsequent harness steps.
# We don't write to it yet, but we validate it up front so future setup logic
# can append KEY=value lines without surprise failures.
: "${HARNESS_ENV_FILE:?HARNESS_ENV_FILE must be set by the harness}"
if [[ ! -w "${HARNESS_ENV_FILE}" ]]; then
    echo "ERROR: HARNESS_ENV_FILE=${HARNESS_ENV_FILE} is not writable" >&2
    exit 1
fi

# --teardown: harness owns compose lifecycle; nothing to do here.
if [[ "${1:-}" == "--teardown" ]]; then
    echo "[harness] Teardown requested — compose lifecycle is managed by the harness; nothing to do."
    exit 0
fi

# Optionally fetch a rules source (read-only) so harness agents can read the full
# rule guidance. The harness runlet lacks the ssh binary; when a rules source is
# configured, the harness injects GITHUB_APP_TOKEN_FILE (a refreshable token) and
# JENTIC_RULES_REPO (the org/name to clone). Absent either, this is skipped and
# the vendored subset is used — so a plain/public run needs nothing.
RULES_DIR="$(pwd)/.rules"
RULES_REPO="${JENTIC_RULES_REPO:-}"

if [[ ! -d "${RULES_DIR}" && -n "${RULES_REPO}" ]]; then
    # Grab the token from the harness's secure sidecar file
    TOKEN=""
    if [[ -n "${GITHUB_APP_TOKEN_FILE:-}" && -s "${GITHUB_APP_TOKEN_FILE}" ]]; then
        TOKEN=$(cat "${GITHUB_APP_TOKEN_FILE}")
    fi

    if [[ -n "${TOKEN}" ]]; then
        # Use --quiet so we don't accidentally leak the token URL in trace logs
        if git clone --quiet "https://x-access-token:${TOKEN}@github.com/${RULES_REPO}.git" "${RULES_DIR}"; then
            echo "[harness] rules: fetched rules source."
        else
            echo "[harness] rules: fetch failed (expected without access)."
        fi
    else
        echo "[harness] rules: no token available; skipping rules fetch."
    fi
fi

# Export the mount so subsequent harness steps inherit it
if [[ -d "${RULES_DIR}" ]]; then
    echo "JENTIC_RULES_DIR=${RULES_DIR}" >>"${HARNESS_ENV_FILE}"
fi

echo "ready"
