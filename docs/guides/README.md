# Guides

Task-scoped guides for using a running Jentic One instance. Install and
day-2 operations live in [`../installation/`](../installation/README.md) and
[`../operations/`](../operations/README.md); the full documentation map is
[`../README.md`](../README.md).

Get going:

- [First brokered call](first-call.md) — the six steps from a running instance
  to a real API response, each linking deeper.

Connect agents:

- [Connect an agent](connecting-agents.md) — the four integration paths
  (skill, MCP stdio, MCP over HTTP, plain HTTP), and how self-hosted differs
  from the Jentic cloud platform.
- [Run coding agents in isolation](local-agent.md) — `jentic run` and the
  local-agent sandbox.
- [Serve MCP over HTTP](mcp-http-endpoint.md) — the optional hosted `/mcp`
  endpoint.

Operate the catalog:

- [Fix a spec with an overlay](overlays.md) — patching an imported API
  description without forking it.
- [How credential resolution works](credentials-and-toolkits.md) — the
  credential/toolkit model and its invariants.

Integrate apps:

- [Register an OAuth client](oauth-clients.md) — third-party apps
  authenticating users through Jentic One.
