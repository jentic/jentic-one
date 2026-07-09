#!/usr/bin/env bash
#
# rules-clone.sh — fetch the internal jentic-one-rules repo for local use.
#
# The public jentic-one repo self-enforces against a small VENDORED subset of
# machine facts (tests/arch/vendored/orm.facts.yaml). Maintainers with access to
# jentic-one-rules can additionally read the FULL rule prose + live facts at
# runtime by cloning that repo here (auto-detected — no env var needed).
#
# This clone is READ-ONLY in intent: it is placed in a gitignored path (.rules/)
# so private rule content can NEVER be committed into this public repo, and the
# facts loader (tests/arch/_rules_facts.py) only ever reads the files.
#
# Contributors without access are unaffected: the script fails SOFT (warns and
# exits 0) so setup flows never break for people who lack access.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Overridable so forks / mirrors don't hardcode the name in one load-bearing spot.
RULES_REMOTE="${JENTIC_RULES_REMOTE:-git@github.com:jentic/jentic-one-rules.git}"
RULES_REF="${JENTIC_RULES_REF:-main}"
MOUNT_DIR="${JENTIC_RULES_DIR:-${REPO_ROOT}/.rules}"

warn() { printf 'rules-clone: %s\n' "$*" >&2; }

if ! command -v git >/dev/null 2>&1; then
  warn "git not found; skipping rules clone (OSS clones use the vendored facts)."
  exit 0
fi

if [ -d "${MOUNT_DIR}/.git" ]; then
  warn "updating existing rules clone at ${MOUNT_DIR}"
  if ! git -C "${MOUNT_DIR}" fetch --quiet origin "${RULES_REF}" \
    || ! git -C "${MOUNT_DIR}" checkout --quiet "${RULES_REF}" \
    || ! git -C "${MOUNT_DIR}" reset --hard --quiet "origin/${RULES_REF}"; then
    warn "could not update ${MOUNT_DIR}; leaving existing checkout in place."
  fi
else
  warn "cloning ${RULES_REMOTE} (ref ${RULES_REF}) into ${MOUNT_DIR}"
  if ! git clone --quiet --depth 1 --branch "${RULES_REF}" "${RULES_REMOTE}" "${MOUNT_DIR}"; then
    warn "no access to ${RULES_REMOTE} (this is expected for OSS users)."
    warn "the repo self-enforces against tests/arch/vendored/*; nothing to do."
    exit 0
  fi
fi

if [ "${MOUNT_DIR}" = "${REPO_ROOT}/.rules" ]; then
  cat <<EOF

rules-clone: full rules available at ${MOUNT_DIR}
This is the auto-detected in-repo path — agents + arch tests will read the live
rules/facts automatically. No env var needed.

EOF
else
  cat <<EOF

rules-clone: full rules available at ${MOUNT_DIR}
This is a non-default path; export it so agents + arch tests find it:

    export JENTIC_RULES_DIR="${MOUNT_DIR}"

EOF
fi
