# RepoStandardsAgent — steering notes

Additional, job-only context for the repo-standards maintenance job. Layered
**additively** on top of `CLAUDE.md` / `AGENTS.md` (those win on conflict).

## What the repo's own tooling already covers (do NOT re-flag)

- Import ordering, unused imports, unused locals, formatting, type-hint presence
  → ruff + mypy strict (`make lint`).
- Architectural conventions are enforced by `tests/arch/*` (128 tests, all green
  as of 2026-06-29). Notable ones: layered Router→Service→Repository
  (`test_no_direct_db`, `test_web_layer`), metrics/tracing/crypto facades,
  no stdlib logging, no manual commits, commit-message convention, OpenAPI
  conformance. If an arch test exists for a rule, trust it — don't hand-audit.

## Detection assets

No lint configs or check scripts of our own — the arch-test suite is the main
detection mechanism for convention drift.

Note: `.rules/` is present in the checkout, so `.rules/AGENTS.md` (not the root
`CLAUDE.md`) is the canonical index. Its always-applies
`rules/style/code-comments.md` rule (present tense only, no private plan
citations) has **no** arch test, so it is a useful hand-audit target. Starting
grep (most hits are allowed runtime-state uses — "a previously denied client",
"no longer exists" — so triage each one):

```bash
grep -rnEi "(#|//|\*|\"\"\").*\b(used to (be|match|live|read|return)|previously|formerly|historically|replaces the old|was removed|were removed)\b" \
  src tools ui/src --include=*.py --include=*.ts --include=*.tsx \
  | grep -v migrations/ | grep -v /generated/
```

Private plan citations (`theme-N Phase M`) are widespread under `src/` and
`ui/src/`. A bulk sweep is a human decision; don't fold it into an
unrelated PR.

The host has no system `python3` — use `uv run python` or sed. UI browser-mode
Vitest needs Playwright Chromium, which may fail to install locally; CI runs it.
