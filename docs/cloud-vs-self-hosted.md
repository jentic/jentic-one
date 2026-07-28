# Jentic Cloud vs Self-Hosted Jentic One

Jentic offers two distinct products that are easy to conflate — especially for
an AI agent (or its operator) that has used both:

|  | Jentic cloud platform | Jentic One (this repo) |
| --- | --- | --- |
| Where it runs | Hosted by Jentic — dashboard at `app.jentic.com`, API at `api.jentic.com` | Your infrastructure (laptop, VM, Kubernetes) |
| How agents connect | Remote **MCP server** (`https://api.jentic.com/mcp`) configured in the agent runtime with a workspace API key | **`jentic` CLI + generated skill** (or the raw HTTP flow) — see below |
| Agent identity | Workspace API key | Per-agent Ed25519 keypair via dynamic client registration (`jentic register` / `jentic bootstrap`) |
| Dashboard | `app.jentic.com` | The bundled UI on your deployment (`/app`) |
| Data | Jentic-hosted workspace | Stays on your infrastructure; credentials never leave your Broker |

They do not share state: an API imported or a credential stored in one is
invisible to the other.

## Self-hosted Jentic One exposes no MCP endpoint

There is **no `/mcp` endpoint** on a Jentic One deployment — not on the
control plane (`:8000/mcp`, `/v1/mcp`, `/api/mcp`, `/sse`) and not on the
broker (`:8100/mcp`). Probing them returns 404. Do not configure an MCP server
entry pointing at your self-hosted host; it cannot work.

The supported integration paths for agents are:

1. **CLI + skill (recommended).** Your operator runs `jentic bootstrap` — it
   registers the agent identity, waits for human approval, and installs the
   onboarding skill into detected agent runtimes (Claude Code, Cursor, Codex,
   Hermes, or a generic `AGENTS.md`). The skill teaches the agent to drive
   `jentic search` → `jentic inspect` → `jentic execute`.
2. **Raw HTTP.** For runtimes without the CLI, every deployment self-describes
   at `GET /llms.txt`: dynamic client registration, token exchange, discovery,
   access requests, and brokered execution — no MCP required.

## Running both side by side (coexistence)

Anyone who used the cloud platform first and later installed Jentic One ends
up with a **dual setup**: the agent runtime's MCP tools (`search_apis`,
`list_credentials`, `execute`, …) still point at the cloud workspace, while
the `jentic` CLI points at the local install. Nothing in any tool response
says which backend replied, so the failure mode is *silent wrong answers*,
not errors:

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
  URL on `api.jentic.com` is the cloud platform. A self-hosted install is
  never behind an MCP entry.
- **CLI** — `jentic profile list` prints each profile's `base_url`; local
  installs point at your own host (e.g. `http://127.0.0.1:8000`).

**Rule of thumb:** pick one surface per task and stay on it. If you work
against the self-hosted install, use the `jentic` CLI for everything, and
consider removing (or disabling) the stale cloud MCP entry so an agent can't
half-answer from the wrong backend.

## Migrating from the cloud MCP to a self-hosted install

1. Install the stack: `curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh`, then `jenticctl install`.
2. Re-import the APIs you need (`jentic catalog import`, or the dashboard) —
   catalog state does not transfer from the cloud workspace.
3. Re-enter credentials in your deployment's dashboard — secrets cannot be
   exported from the cloud platform (nor from Jentic One; that's the point).
4. Register your agents against the local install (`jentic bootstrap`).
5. Remove the cloud MCP server entry from your agent runtime's config to
   avoid the split-brain scenario above.
