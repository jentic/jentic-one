# AGENTS.md

This repo's agent guidance lives in **[`CLAUDE.md`](CLAUDE.md)**. Read it first.

It covers the quick start, commands, project layout (backend + `ui/` frontend),
code style, testing conventions, and — most importantly — the **Rules index**:
the `.cursor/rules/*` you must consult before changing ORM
models, services, web handlers, or any UI code.

Key conventions, in one breath:

- **Backend:** layered Router → Service → Repository; no raw DB in web/service;
  enforced by the architecture tests in [`tests/arch/`](tests/arch/).
- **Frontend (`ui/`):** feature modules mirror the backend's module/layer shape;
  views talk to the backend only through their module's `api/hooks`; import
  shared code via the `@/shared` / `@/shared/ui` barrels; routes live under
  `/app/*` and are registered additively. Enforced by `ui/eslint.config.js`.
- **Git:** see [`.cursor/rules/git-conventions.mdc`](.cursor/rules/git-conventions.mdc)
  (`alwaysApply`).

When `CLAUDE.md` and this file disagree, `CLAUDE.md` wins.

## Rules source (read `.rules/` if present)

The full development rules live in the internal `jentic-one-rules` repo. If a
read-only clone is present at `.rules/` in this repo (or `JENTIC_RULES_DIR` is
set), **treat that as the authoritative, complete rule set** and follow its
guidance (`.rules/rules/**/*.md`) in addition to `CLAUDE.md`.

If `.rules/` is absent (the common case for a public clone), fall back to the
in-repo guidance here + `CLAUDE.md`; the architecture tests still self-enforce
the machine-checkable subset via the vendored facts in
[`tests/arch/vendored/`](tests/arch/vendored/). No setup is required either way —
this is a read-if-present rule, not a step to perform.

