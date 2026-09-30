#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
    cat <<USAGE
Usage: $(basename "$0") [--db <name>]... [--target <revision>] [--check]
       $(basename "$0") --dry-run --db <name> [--target <revision>]

With no options, runs the full migrations runner
(uv run python -m jentic_one.migrations.run): every database to head, in
dependency order, including the cross-database steps — the service-account
retirement before the admin drop, and the post-migration upgrade steps. This
is the normal upgrade path.

Options:
  --db <name>        Only migrate this database (registry, control, admin);
                     repeatable. A partial upgrade SKIPS the service-account
                     retirement and the upgrade steps (the admin drop then
                     refuses while any service account is unretired).
  --target <rev>     Target revision (default: head). A targeted upgrade also
                     skips the retirement and the upgrade steps.
  --check            Report each database's schema state; change nothing.
  --dry-run          Generate the SQL for ONE database (--db required) with
                     plain Alembic, without applying it. The SQL never
                     includes the service-account retirement (it is not a
                     schema step), so it is for review only.
  -h, --help         Show this help.
USAGE
    exit "${1:-0}"
}

DBS=()
TARGET=""
DRY_RUN=false
CHECK=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db) DBS+=("$2"); shift 2 ;;
        --target) TARGET="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --check) CHECK=true; shift ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown option: $1"; usage 1 ;;
    esac
done

VALID_DBS=("registry" "control" "admin")
for db in "${DBS[@]+"${DBS[@]}"}"; do
    if [[ ! " ${VALID_DBS[*]} " =~ " ${db} " ]]; then
        echo "ERROR: invalid database '$db'. Must be one of: ${VALID_DBS[*]}"
        exit 1
    fi
done

LOG_DIR="$PROJECT_ROOT/logs/migrations"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [[ "$DRY_RUN" == "true" ]]; then
    if [[ ${#DBS[@]} -ne 1 ]]; then
        echo "ERROR: --dry-run needs exactly one --db"
        usage 1
    fi
    DB_NAME="${DBS[0]}"
    LOG_FILE="$LOG_DIR/${DB_NAME}_${TIMESTAMP}.log"
    echo "==> Dry-run: generating SQL for $DB_NAME to ${TARGET:-head} (not applying;"
    echo "    the service-account retirement is not part of it)..."
    uv run alembic -n "$DB_NAME" upgrade "${TARGET:-head}" --sql 2>&1 | tee -a "$LOG_FILE"
    echo ""
    echo "==> Dry-run complete. SQL logged to: $LOG_FILE"
    exit 0
fi

ARGS=()
for db in "${DBS[@]+"${DBS[@]}"}"; do
    ARGS+=(--db "$db")
done
if [[ -n "$TARGET" ]]; then
    ARGS+=(--target "$TARGET")
fi
if [[ "$CHECK" == "true" ]]; then
    ARGS+=(--check)
fi

LOG_FILE="$LOG_DIR/migrate_${TIMESTAMP}.log"
echo "==> Running the migrations runner ${ARGS[*]+"${ARGS[*]}"}"
uv run python -m jentic_one.migrations.run "${ARGS[@]+"${ARGS[@]}"}" 2>&1 | tee -a "$LOG_FILE"
echo ""
echo "==> Done. Log: $LOG_FILE"
