# AGENTS.md

> **If `.rules/` exists, read `.rules/AGENTS.md` and use it instead of this file.**
> Otherwise use the guidance below.

## In-repo guidance

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

