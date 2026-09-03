# The Jentic cloud platform vs self-hosted Jentic One

Jentic offers two distinct products that are easy to conflate —
especially for an AI agent (or its operator) that has used both:

|  | Jentic cloud platform | Jentic One (this repo) |
| --- | --- | --- |
| Where it runs | Hosted by Jentic — dashboard at `app.jentic.com`, API at `api.jentic.com` | Your infrastructure (laptop, VM, Kubernetes) |
| How agents connect | Remote **MCP server** (`https://api.jentic.com/mcp`) configured in the agent runtime with a workspace API key | **`jentic` CLI + generated skill**, the local **`jentic mcp`** stdio server (available in the `jentic` CLI from the next release), an optional hosted **`/mcp`** endpoint (off by default), or the raw HTTP flow — see below |
| Agent identity | Workspace API key | Per-agent Ed25519 keypair via dynamic client registration (`jentic register` / `jentic setup`) |
| Dashboard | `app.jentic.com` | The bundled UI on your deployment (`/app`) |
| Data | Jentic-hosted workspace | Stays on your infrastructure; stored secrets are decrypted only inside your Broker at execution time |

They do not share state: an API imported or a credential stored in one is
invisible to the other.

## MCP against a self-hosted install: `jentic mcp` and the `/mcp` endpoint

MCP access to a Jentic One deployment comes in two shapes. The default is
the **local `jentic mcp` stdio server** — available in the `jentic` CLI from
the next release; check `jentic mcp --help`. Your agent runtime (Claude
Desktop/Code, Cursor, …) spawns it as an ordinary stdio MCP entry; it
authenticates with the agent's registered identity and exposes the same
discover → execute loop the CLI drives (`search_apis`, `inspect_operation`,
`execute`, …). Every tool result carries an identity stamp (`backend`,
`host`, `instance_id`, `fetched_at`) so an agent can always tell which
instance answered.

A deployment can additionally serve a **hosted Streamable HTTP endpoint at
`/mcp`** on the control plane — the same tool surface, per-request bearer
auth, no CLI on the agent machine. It is **off by default**
(`server.mcp.enabled`): on a default install, probing the control plane
(`:8000/mcp`, `/v1/mcp`, `/api/mcp`, `/sse`) still returns 404 (or, on
deployments preparing interactive OAuth, a 401 discovery challenge). The
broker (`:8100`) answers 401 to an unauthenticated `/mcp` probe in every
configuration — that is **not** a hidden MCP server behind auth; it is the
broker's credential-injecting forward proxy rejecting the request, like it
would any other unauthenticated path. Only configure a *URL-based* MCP entry
against a deployment whose operator has enabled the endpoint — see
[mcp-http-endpoint.md](../mcp-http-endpoint.md) for enabling it, client
snippets, and stdio-bridge recipes for runtimes that cannot use URL entries.

The supported integration paths for agents are:

1. **CLI + skill.** Your operator runs `jentic setup` — it registers the
   agent identity, waits for human approval, and installs the onboarding
   skill into detected agent runtimes (Claude Code, Cursor, Codex, Hermes, or
   a generic `AGENTS.md`). The skill teaches the agent to drive
   `jentic search` → `jentic inspect` → `jentic execute`.
2. **MCP (`jentic mcp`).** For MCP-capable runtimes, add a stdio entry that
   spawns `jentic mcp --context <name>`. Setup and registration remain
   CLI-only (`jentic setup`), so onboarding is unchanged; once registered,
   the execute loop is MCP-preferred. If a session has both surfaces, prefer
   the MCP tools and use the CLI for `setup`/`access` recovery and anything
   not exposed over MCP — both talk to the same instance (check
   `backend`/`host` in the identity stamp). Where the operator has enabled
   the hosted `/mcp` endpoint, a URL-based entry with the agent's API key is
   the CLI-less alternative ([mcp-http-endpoint.md](../mcp-http-endpoint.md)).
3. **Raw HTTP.** For runtimes without the CLI, every deployment self-describes
   at `GET /llms.txt`: dynamic client registration, token exchange, discovery,
   access requests, and brokered execution.

If the agent runtime spawning `jentic mcp` shares a host with the instance,
read [Hardening same-host MCP setups](../security/mcp-same-host-hardening.md).

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
