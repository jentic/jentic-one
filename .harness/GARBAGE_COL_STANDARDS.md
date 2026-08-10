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

None yet. (No new lint configs or check scripts created — the existing arch-test
suite is the detection mechanism for convention drift.)

## Recurring check: docs/config → Makefile-target drift

The `Commands` table in `CLAUDE.md`, prose in `README.md`/`CONTRIBUTING.md`, and
the permission allowlist in `.claude/settings.json` all name `make <target>`s.
These drift as the Makefile evolves. To re-verify on future runs:

- Ground truth: `grep -nE '^[a-zA-Z0-9_-]+:.*?## ' Makefile` (targets + help text).
- Cross-check every `make <target>` reference in `CLAUDE.md`, `README.md`,
  `CONTRIBUTING.md`, and every `"Bash(make …)"` entry in `.claude/settings.json`
  against that list. Any target not in the list is a phantom.
- Known past phantoms (fixed 2026-07-27): `fmt` (real target is `fix`) and
  `pre-commit` (real target is `hooks`). `check` runs `lint score detect-secrets
  test-arch` — it does NOT run unit tests, so descriptions saying otherwise are wrong.
