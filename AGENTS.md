# AGENTS.md

> **If `.rules/` exists, read `.rules/AGENTS.md` and use it instead of this file.**
> Otherwise use the guidance below.

## Using Jentic One (not changing it)

If your task is to install Jentic One and connect an agent to it, follow this section. The
rest of this file is for changing the codebase.

**Install.** Two supported paths:

1. Signed release binary, from https://github.com/jentic/jentic-one/releases/latest. Verify
   against `checksums.txt` (signature: `checksums.txt.sig`, certificate: `checksums.txt.pem`),
   then run `jenticctl install`. Prefer this path: it is verifiable before execution, and a
   sandboxed agent will usually be refused permission to pipe a script to a shell.
2. Bootstrap script:
   `curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh`
   This downloads a Go toolchain if none is present, clones this repository, builds `jenticctl`
   and `jentic`, then runs `jenticctl install`. Set `JENTIC_NO_INSTALL=1` to stop after the
   binaries are installed — do that on the agent machine, which needs only the binaries, not a
   second deployment.

**Run Jentic One on a different machine from the agent.** An agent running as the same OS user
can read the credential database and encryption key from disk, whatever the API-level controls
allow. See `docs/security/hardening.md` before using real credentials.

**Register and reach a first call:**

1. `jentic setup --url <control-plane URL>` creates the agent identity, waits for an
   operator to approve it, and installs the agent skill. (`jentic register --url …` is the
   registration-only path — identity only, no skills; without `--url` both fall back to the
   configured base URL or
   `http://127.0.0.1:8000`, and prompt for it on an interactive terminal.)
   Report the wait to the user: on a single-operator install they are the operator, and they
   approve the agent in the UI at `/app`.
2. If no admin account exists yet, point the user to `/setup` (browser) or `jenticctl setup`
   (terminal). This is a one-time step.
3. Import an API from https://github.com/jentic/jentic-public-apis (e.g. `httpbin.org`, used in
   step 6), or register a private OpenAPI description of the user's own service.
4. Store a credential for that API, once. It is encrypted at rest and is never returned.
5. Request access: `jentic access request --toolkit <vendor/name>` files a reviewable request;
   granting is always a human action. The operator binds the agent to a toolkit — access is
   default-deny, and a rule-less binding still blocks everything.
6. `jentic execute GET:https://httpbin.org/get --json` runs a call through the Broker with the
   credential injected. Give `execute` the operation's full upstream URL (as returned by
   `jentic search`/`jentic inspect`) or its operation_id — the Broker is a forward proxy, not a
   path router.

**Constraints to respect:**

- One governed upstream call per execution. Compose multi-step work yourself; the Broker does
  not orchestrate.
- A self-hosted deployment serves an HTTP MCP endpoint (`/mcp`) only when its operator enabled
  it (`server.mcp.enabled`, off by default — see
  [docs/mcp-http-endpoint.md](docs/mcp-http-endpoint.md)). Otherwise integrate through the
  `jentic` CLI, the skill it generates, the CLI's local MCP stdio server (an `mcp` subcommand
  shipping in the next `jentic` release), or plain HTTP against the deployment's own API.
- A running instance serves `/llms.txt` and `/.well-known/llms.txt` with that deployment's base
  URL. Once an instance exists, prefer those over this file for anything at runtime.
- Never print, log or echo a stored credential. The Broker does not return them.

**If a step is blocked by your own sandbox or by network egress rules, say so and stop.** Do
not work around a security control. Issue #994 tracks the install paths available to a
restricted agent.

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

## Filing an issue (not changing code)

If your task is to **file an issue** (bug, feedback, idea) rather than change the
codebase, see
[`CONTRIBUTING.md` → Filing an Issue with an AI Agent](CONTRIBUTING.md#filing-an-issue-with-an-ai-agent).

Two things up front: (1) this is a **public** repo, so you **cannot apply labels**
(GitHub silently drops `--label` for non-maintainers) — don't try; the automated
intake assistant applies them. (2) Write a clear, faithful issue with the **exact
error/output verbatim** and secrets redacted (`***`); if you know the type/area, put
it in the body prose and the assistant will confirm it.
