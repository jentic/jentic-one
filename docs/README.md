# Jentic Mini Docs

Design and reference documentation for Jentic Mini — the self-hosted,
open-source implementation of the Jentic API. For the project overview, quick
start, and deployment instructions, see the repo root [README.md](../README.md).
For agent-facing usage (search / inspect / execute), see [AGENTS.md](../AGENTS.md).

## Index

- [architecture.md](architecture.md) — system design, request flow, router registration, data model
- [auth.md](auth.md) — two-actor auth model, human sessions, agent keys, self-enrollment, trusted subnets
- [broker-cli.md](broker-cli.md) — routing HTTP clients (git, curl, etc.) through the broker for credential injection
- [catalog.md](catalog.md) — public catalog manifest, lazy refresh, dedup, auto-import on credential add
- [credential-deeplink.md](credential-deeplink.md) — `/credentials/new` deep-link query params for agent-assisted credential entry
- [credentials.md](credentials.md) — vault, credential lifecycle, overlay flywheel, toolkit binding
- [decisions.md](decisions.md) — architectural decision log
- [monitor.md](monitor.md) — Monitor page: Overview / Execution Log / Jobs, trace–job cross-linking, usage API
- [oauth-broker.md](oauth-broker.md) — `OAuthBroker` protocol, token vs proxy modes, registry, DB schema
- [pipedream.md](pipedream.md) — Pipedream Connect integration: setup, connect-link, sync, supported apps
- [self-registration.md](self-registration.md) — how the server registers its own OpenAPI spec at startup
- [server-variables.md](server-variables.md) — OpenAPI server variables for self-hosted / multi-tenant APIs
- [versioning.md](versioning.md) — `APP_VERSION` source of truth, `/version` endpoint, update-check behaviour
- [workflows.md](workflows.md) — Arazzo workflow execution, dispatch, import, examples

Subdirectories:
- [tutorials/](tutorials/) — step-by-step walkthroughs (Notion API key, Gmail OAuth)
- [deploy/](deploy/) — deployment recipes
- [archive/](archive/) — historical / superseded documents

## See also

- [../README.md](../README.md) — project overview, quick start, configuration
- [../AGENTS.md](../AGENTS.md) — agent onboarding and runtime reference
- [../DEVELOPMENT.md](../DEVELOPMENT.md) — prerequisites, installation, running tests
- [../.claude/CLAUDE.md](../.claude/CLAUDE.md) — working agreement for AI coding agents in this repo
