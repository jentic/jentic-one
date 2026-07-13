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

## Filing an issue (not changing code)

If your task is to **file an issue** (bug, feedback, idea) rather than change the
codebase, see
[`CONTRIBUTING.md` → Filing an Issue with an AI Agent](CONTRIBUTING.md#filing-an-issue-with-an-ai-agent).

Two things up front: (1) this is a **public** repo, so you **cannot apply labels**
(GitHub silently drops `--label` for non-maintainers) — don't try; the automated
intake assistant applies them. (2) Write a clear, faithful issue with the **exact
error/output verbatim** and secrets redacted (`***`); if you know the type/area, put
it in the body prose and the assistant will confirm it.
