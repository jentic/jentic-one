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

# Fetch the internal rules repo (read-only) so harness agents read the full rule
# guidance. The harness runlet lacks the ssh binary, so we use HTTPS with the
# injected CI token. Fails soft when the rules repo isn't reachable.
echo "[harness] Fetching internal rules repo (jentic-one-rules)..."
RULES_DIR="$(pwd)/.rules"

if [[ ! -d "${RULES_DIR}" ]]; then
    # Grab whatever token the harness exposes in the environment
    TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"

    if [[ -n "${TOKEN}" ]]; then
        # Use --quiet so we don't accidentally leak the token URL in trace logs
        if git clone --quiet "https://x-access-token:${TOKEN}@github.com/jentic/jentic-one-rules.git" "${RULES_DIR}"; then
            echo "[harness] rules-clone: Successfully cloned jentic-one-rules."
        else
            echo "[harness] rules-clone: no access to jentic-one-rules (expected for OSS users)."
        fi
    else
        echo "[harness] rules-clone: No GITHUB_TOKEN found. Skipping rules repo clone."
    fi
fi

# Export the mount so subsequent harness steps inherit it
if [[ -d "${RULES_DIR}" ]]; then
    echo "JENTIC_RULES_DIR=${RULES_DIR}" >>"${HARNESS_ENV_FILE}"
fi

echo "ready"
