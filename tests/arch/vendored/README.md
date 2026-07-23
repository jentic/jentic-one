# Vendored rules facts

This directory holds a **committed subset** of the machine-readable enforcement
facts. Today that is a single file:

- `orm.facts.yaml` — the ORM conventions (`valid_bases`, required columns,
  tablename rules, KSUID-exempt tables) that `tests/arch/test_orm_conventions.py`
  reads instead of hard-coding.

## Why it's vendored

The arch tests resolve facts in priority order (see `tests/arch/_rules_facts.py`):

1. `$JENTIC_RULES_DIR` — an explicitly-pointed rules directory.
2. `.rules/` — an optional in-repo mount, auto-detected with no env var (gitignored).
3. a sibling rules checkout (`$JENTIC_RULES_SIBLING_NAME`).
4. **this vendored copy** — the fallback so a standalone clone with no external
   rules source still self-enforces its architecture.

## The no-leak contract

This is a **public** repo. The vendored file may contain **only** facts that are
meaningful in this repo. Concretely:

- **OSS-applicable facts only.** No section, base, or table name that exists only
  in a downstream (non-public) tier may be copied here.
- This is **enforced**, not a promise: `test_vendored_orm_facts_is_oss_safe`
  (in `tests/arch/test_rules_facts_vendored.py`, always run) fails if the file
  gains an unexpected top-level key, a `valid_bases` entry not defined in
  `src/jentic_one/shared/db/base.py`, or a `ksuid_exempt_tables` entry with no
  matching `__tablename__` in this repo's models.

## Re-vendoring (when upstream facts change)

1. Make the fuller rules facts available locally: set
   `JENTIC_RULES_DIR=/path/to/rules` (or mount them under a gitignored `.rules/`).
2. Run the guards:
   `uv run pytest tests/arch/test_rules_facts_vendored.py tests/arch/test_orm_conventions.py`.
   The drift guard (`test_vendored_orm_facts_matches_mounted_source`) will fail
   if the vendored copy is stale.
3. Copy the upstream file over the vendored one:
   `cp "$JENTIC_RULES_DIR/rules/backend/orm.facts.yaml" tests/arch/vendored/orm.facts.yaml`.
4. Re-run the guards. `test_vendored_orm_facts_is_oss_safe` must still pass — if
   it fails, the upstream file carries content that is not OSS-safe and must not
   be vendored as-is.
