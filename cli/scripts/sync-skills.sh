#!/usr/bin/env bash
set -euo pipefail

BRANCH="${SKILLS_BRANCH:-skills/generated}"
PRODUCT="${SKILLS_PRODUCT:-jentic}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)"
TARGET_DIR="${SCRIPT_DIR}/../internal/skillgen/content/tasks/${PRODUCT}"

mkdir -p "${TARGET_DIR}"
TARGET_DIR="$(cd "${TARGET_DIR}" && pwd)"

git -C "${REPO_ROOT}" fetch origin "${BRANCH}" --depth=1 2>/dev/null || {
  echo "Warning: could not fetch ${BRANCH}, using existing embedded skills" >&2
  exit 0
}

# Clear existing docs so removed files don't linger
rm -f "${TARGET_DIR}"/*.md

git -C "${REPO_ROOT}" archive "origin/${BRANCH}" -- "${PRODUCT}/" | tar -x -C "${TARGET_DIR}" --strip-components=1

# Strip wrapping code fences (```markdown ... ```) if present
for f in "${TARGET_DIR}"/*.md; do
  [ -f "$f" ] || continue
  if head -1 "$f" | grep -q '^```'; then
    sed '1d;$d' "$f" > "${f}.tmp" && mv "${f}.tmp" "$f"
  fi
done

echo "Synced $(ls "${TARGET_DIR}"/*.md 2>/dev/null | wc -l | tr -d ' ') skill docs from ${BRANCH}"
