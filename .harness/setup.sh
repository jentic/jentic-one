#!/usr/bin/env bash
# Repo setup for an automated worker: a Software Factory VM runs it once from
# the repo root (sf-repo-setup), after starting the services in
# .harness/compose.yaml. It also runs as-is in any plain checkout.
#
# All product readiness is `make ready` (see the Makefile): Python and UI
# dependencies, the fixture Postgres and migrations. It fails on the first step
# that fails, and so does this script, so a worker never starts on a half-set-up
# checkout.
#
# Optional, off by default: JENTIC_RULES_REPO=<owner>/<name> clones a rules
# source read-only into .rules over plain HTTPS. Access comes from the
# machine's git credential helper; no token is read, put in a URL or passed on
# the command line. A requested clone that fails fails setup.
#
# HARNESS_ENV_FILE, when set, receives KEY=value lines for the agent's
# environment. Nothing secret is written there.
set -euo pipefail

# The runlet harness's teardown hook: compose lifecycle belongs to the harness.
if [[ "${1:-}" == "--teardown" ]]; then
    echo "[harness] teardown: nothing to do"
    exit 0
fi

if [[ -n "${HARNESS_ENV_FILE:-}" && ! -w "${HARNESS_ENV_FILE}" ]]; then
    echo "ERROR: HARNESS_ENV_FILE=${HARNESS_ENV_FILE} is not writable" >&2
    exit 1
fi

# Runtimes pinned in mise.toml, when mise is how this machine provides them.
run() { "$@"; }
if command -v mise >/dev/null 2>&1; then
    mise install
    run() { mise exec -- "$@"; }
fi

if ! run uv --version >/dev/null 2>&1; then
    echo "ERROR: uv is not installed (https://docs.astral.sh/uv/); make ready needs it" >&2
    exit 1
fi

run make ready

RULES_DIR="$(pwd)/.rules"
if [[ -n "${JENTIC_RULES_REPO:-}" && ! -e "${RULES_DIR}" ]]; then
    if [[ ! "${JENTIC_RULES_REPO}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
        echo "ERROR: JENTIC_RULES_REPO must be <owner>/<name>, got '${JENTIC_RULES_REPO}'" >&2
        exit 1
    fi
    git clone --quiet --depth 1 "https://github.com/${JENTIC_RULES_REPO}.git" "${RULES_DIR}"
    echo "[harness] rules: cloned ${JENTIC_RULES_REPO} into .rules"
fi

if [[ -n "${HARNESS_ENV_FILE:-}" && -d "${RULES_DIR}" ]]; then
    echo "JENTIC_RULES_DIR=${RULES_DIR}" >>"${HARNESS_ENV_FILE}"
fi

echo "ready"
