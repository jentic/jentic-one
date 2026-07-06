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

## Known intentional patterns (do NOT flag as dead code)

- **Repository-tier `client.ts` files (`ui/src/modules/*/api/client.ts`) mirror
  the full backend API surface 1:1**, so some exported repository functions have
  no service-tier hook consumer yet (e.g. `toolkits/api/client.ts`
  `patchPermissions`, `listPermissions`, `deleteKey`). This asymmetry is
  deliberate — the repository tier is the sanctioned wrapper around
  `@/shared/api` and stays complete even when a hook isn't wired up. Only flag a
  *service-tier hook* (`api/index.ts` / `api/hooks.ts` `useXxx`) as dead when it
  has no view/test/mock reference; leave the underlying `client.ts` function.
- **`shared/__init__.py` `__all__` is a curated public config-types surface.**
  Some re-exports (e.g. `ServerConfig`) have no direct importer but are exported
  for API completeness alongside `AppConfig`/`DatabaseConfig`. Removing one
  asymmetrically is a human judgement call, not automatic dead code.
