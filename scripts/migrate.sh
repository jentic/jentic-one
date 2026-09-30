#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
    cat <<USAGE
Usage: $(basename "$0") --db <name> [--target <revision>] [--dry-run]

Options:
  --db <name>        Database to migrate (registry, control, admin). Required.
  --target <rev>     Target revision (default: head).
  --dry-run          Generate SQL without applying.
  -h, --help         Show this help.

Migrates ONE database with plain Alembic: it skips the runner's cross-database
upgrade steps. For a normal upgrade use the full runner instead:
  uv run python -m jentic_one.migrations.run
(The admin service-account drop refuses to run until that runner has retired
the service accounts.)
USAGE
    exit "${1:-0}"
}

DB_NAME=""
TARGET="head"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db) DB_NAME="$2"; shift 2 ;;
        --target) TARGET="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown option: $1"; usage 1 ;;
    esac
done

if [[ -z "$DB_NAME" ]]; then
    echo "ERROR: --db is required"
    usage 1
fi

VALID_DBS=("registry" "control" "admin")
if [[ ! " ${VALID_DBS[*]} " =~ " ${DB_NAME} " ]]; then
    echo "ERROR: invalid database '$DB_NAME'. Must be one of: ${VALID_DBS[*]}"
    exit 1
fi

LOG_DIR="$PROJECT_ROOT/logs/migrations"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/${DB_NAME}_${TIMESTAMP}.log"

echo "==> Database: $DB_NAME"
echo "==> Target:   $TARGET"

echo "==> Current revision:"
uv run alembic -n "$DB_NAME" current 2>&1 | tee -a "$LOG_FILE"

if [[ "$DRY_RUN" == "true" ]]; then
    echo ""
    echo "==> Dry-run: generating SQL (not applying)..."
    uv run alembic -n "$DB_NAME" upgrade "$TARGET" --sql 2>&1 | tee -a "$LOG_FILE"
    echo ""
    echo "==> Dry-run complete. SQL logged to: $LOG_FILE"
else
    echo ""
    echo "==> Applying migrations..."
    uv run alembic -n "$DB_NAME" upgrade "$TARGET" 2>&1 | tee -a "$LOG_FILE"
    echo ""
    echo "==> New revision:"
    uv run alembic -n "$DB_NAME" current 2>&1 | tee -a "$LOG_FILE"
    echo ""
    echo "==> Migration complete. Log: $LOG_FILE"
fi
