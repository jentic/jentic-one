# Connecting an agent

You have a running deployment; this page is how an agent talks to it. There
are four supported paths, and they share one identity model: a per-agent
Ed25519 keypair registered through dynamic client registration, **approval
first** — an operator approves the agent before it gets tokens, and access to
actual operations is a separate, also-approved step
([first brokered call](first-call.md), steps 4–5).

## The four paths

1. **CLI + skill** — the default. Your operator runs `jentic setup`: it
   registers the agent identity, waits for human approval, and installs the
   onboarding skill into detected agent runtimes (Claude Code, Cursor, Codex,
   Hermes, or a generic `AGENTS.md`). The skill teaches the agent to drive
   `jentic search` → `jentic inspect` → `jentic execute`. Running the agent on
   the same machine as your own work? Isolate it —
   [Run coding agents in isolation](local-agent.md).
2. **MCP over stdio (`jentic mcp`)** — for MCP-capable runtimes, add a stdio
   entry that spawns `jentic mcp --context <name>`. Setup and
   registration stay CLI-only (`jentic setup`), so onboarding is unchanged;
   once registered, the execute loop is MCP-preferred. It exposes the same
   discover → execute loop the CLI drives (`search_apis`,
   `inspect_operation`, `execute`, …), and every tool result carries an
   identity stamp (`backend`, `host`, `instance_id`, `fetched_at`) so an
   agent can always tell which instance answered.
3. **MCP over HTTP** — a deployment can serve a Streamable HTTP endpoint at
   `/mcp` on the control plane: same tool surface, per-request bearer auth,
   no CLI or context on the agent machine. It is **off by default**
   (`server.mcp.enabled`) — enabling it, client snippets, and stdio-bridge
   recipes for runtimes that cannot use URL entries:
   [Serve MCP over HTTP](mcp-http-endpoint.md).
4. **Raw HTTP** — for runtimes with none of the above, every deployment
   self-describes at `GET /llms.txt`: dynamic client registration, token
   exchange, discovery, access requests, and brokered execution.

If a session has both the CLI and MCP tools, prefer the MCP tools and use the
CLI for `setup`/`access` recovery and anything not exposed over MCP — both
talk to the same instance (check `backend`/`host` in the identity stamp).

**One probe result that misleads:** on a default install, probing the control
plane (`:8000/mcp`, `/v1/mcp`, `/api/mcp`, `/sse`) returns 404 (or, on
deployments preparing interactive OAuth, a 401 discovery challenge) — the
hosted endpoint really is off. The broker (`:8100`) answers 401 to an
unauthenticated `/mcp` probe in **every** configuration — that is not a hidden
MCP server behind auth; it is the broker's credential-injecting forward proxy
rejecting the request, like it would any other unauthenticated path. Only
configure a *URL-based* MCP entry against a deployment whose operator has
enabled the endpoint.

If the agent runtime spawning `jentic mcp` shares a host with the instance,
read [Hardening same-host MCP setups](../security/same-host/mcp-same-host-hardening.md).

## Not the Jentic cloud platform

Jentic also runs a hosted platform — dashboard at `app.jentic.com`, API and
remote MCP server at `api.jentic.com`. It is a **different product** and
shares no state with a self-hosted install: an API imported or a credential
stored in one is invisible to the other. Don't copy integration instructions
across.

|  | Jentic cloud platform | Jentic One (this repo) |
| --- | --- | --- |
| Where it runs | Hosted by Jentic | Your infrastructure (laptop, VM, Kubernetes) |
| How agents connect | Remote MCP server (`https://api.jentic.com/mcp`) with a workspace API key | The four paths above |
| Agent identity | Workspace API key | Per-agent Ed25519 keypair via dynamic client registration |
| Dashboard | `app.jentic.com` | The bundled UI on your deployment (`/app`) |
| Data | Jentic-hosted workspace | Stays on your infrastructure; stored secrets are decrypted only inside your Broker at execution time |

## Running both side by side (coexistence)

If you used the cloud platform first and later installed Jentic One, you end
up with a **dual setup**: the agent runtime's MCP tools (`search_apis`,
`list_credentials`, `execute`, …) may still point at the cloud workspace,
while the `jentic` CLI (and a local `jentic mcp` entry) point at the local
install. Cloud MCP responses don't say which backend replied, so the failure
mode is *silent wrong answers*, not errors:

- an API you just imported locally "doesn't exist" (the MCP search answered
  from the cloud workspace),
- credentials "disappeared" (the cloud workspace never had them),
- operation IDs from one surface don't resolve on the other (different ID
  namespaces).

The debugging transcript reads like data loss; everything is actually fine —
half the tools are talking to a different product.

**How to check which backend each surface is bound to:**

- **MCP server** — inspect the MCP entry in the agent runtime's config (e.g.
  Claude Desktop's `claude_desktop_config.json`, Cursor's MCP settings): a
  URL on `api.jentic.com` is the cloud platform; an entry that spawns
  `jentic mcp` is the local server, bound to whatever backend its context's
  environment points at. `jentic mcp` tool results also stamp
  `backend`/`host` on every response, so the answering instance is visible
  in-session.
- **CLI** — `jentic env list` prints each environment's `base_url` (and
  `broker_url`); `jentic context view` shows the active one. Local installs
  point at your own host (e.g. `http://127.0.0.1:8000`).

**Rule of thumb:** pick one surface per task and stay on it. If you work
against the self-hosted install, use the `jentic` CLI and its `jentic mcp`
tools for everything, and consider removing (or disabling) the stale cloud
MCP entry so an agent can't answer from the wrong backend.

## Migrating from the cloud MCP to a self-hosted install

1. Install the stack: `curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh`, then `jenticctl install`.
2. Re-import the APIs you need (`jentic catalog import`, or the dashboard) —
   catalog state does not transfer from the cloud workspace.
3. Re-enter credentials in your deployment's dashboard — secrets cannot be
   exported from the cloud platform (nor from Jentic One; that's the point).
4. Register your agents against the local install (`jentic setup`).
5. Replace the cloud MCP server entry in your agent runtime's config with a
   `jentic mcp` entry (or remove it entirely) to avoid the split-brain
   scenario above.
