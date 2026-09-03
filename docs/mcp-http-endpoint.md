# The `/mcp` Streamable HTTP endpoint

Jentic One deployments can serve MCP directly over HTTP: a **stateless
Streamable HTTP endpoint at `/mcp`** on the control plane (MCP spec revision
2026-07-28). It exposes the same tool surface as the local `jentic mcp` stdio
server — `whoami`, `search_apis`, `inspect_operation`, `search_catalog`,
`execute`, `execute_read`, `get_execution_result` — and the two transports
are contract-tested against each other, so an agent gets identical envelopes
whichever one its runtime speaks.

When to use which:

- **Stdio (`jentic mcp`) stays the default for a runtime that can spawn a
  process** — the agent's key never leaves its machine, and registration
  (`jentic setup`) is unchanged. See
  [cloud-vs-self-hosted.md](guides/cloud-vs-self-hosted.md) for the integration
  paths and [security/mcp-same-host-hardening.md](security/mcp-same-host-hardening.md)
  for same-host recipes.
- **The HTTP endpoint is the daemon-native shape**: headless agents, other
  machines, runtimes that cannot spawn processes, and the many-users-one-URL
  deployment. No CLI or context on the agent machine — each request carries
  a bearer.

## Enabling it

The endpoint is config-gated and **off by default** — a default install
answers the framework's plain 404 on `/mcp` (or, with `server.mcp.oauth.enabled`,
the OAuth discovery challenge), exactly as before. Enable it in the backend
config:

```yaml
server:
  mcp:
    enabled: true
```

Flipping the flag needs no rebuild; the gate is evaluated per request. The
endpoint accepts only `POST` (stateless — no SSE stream, no `Mcp-Session-Id`),
validates the `Origin` header strictly (403 on mismatch), and requires a
credential for everything except surface discovery (`tools/list`, `ping`,
the resource listings).

## Connecting an HTTP-capable MCP client

Authenticate each request with the agent's API key (or an access token) —
the same credential shapes every REST route accepts:

```json
{
  "mcpServers": {
    "jentic": {
      "url": "https://your-jentic-host/mcp",
      "headers": { "Authorization": "Bearer <agent-api-key>" }
    }
  }
}
```

The agent detail page's **MCP tab** renders this snippet pre-filled for each
agent when the instance serves `/mcp` (the variant is hidden otherwise). The
same page lists the agent's MCP sessions: on this transport a "session" row
is emitted once per client per fixed six-hour UTC window (a reconnect
straddling a window boundary can yield two rows minutes apart), since spec
2026-07-28 has no protocol-level sessions to count.

Scopes and audit are identical to REST: the endpoint enforces the same
per-tool scopes the fronted routes require, and executions land in the
monitor labeled with the `mcp` origin.

## Recipes: stdio-only clients

Some MCP runtimes only spawn stdio servers. Two third-party bridges pump
stdio ↔ Streamable HTTP; both keep the endpoint's per-request bearer model
(the bridge adds the `Authorization` header, the deployment still enforces
identity, scopes, and audit per call).

### `mcp-remote` (npm)

```json
{
  "mcpServers": {
    "jentic": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-jentic-host/mcp",
        "--header",
        "Authorization:${JENTIC_AUTH}"
      ],
      "env": { "JENTIC_AUTH": "Bearer <agent-api-key>" }
    }
  }
}
```

The env-var indirection is deliberate: several runtimes mangle spaces in
`args` (the no-space `Header:${VAR}` form is `mcp-remote`'s own recommended
workaround), and the expanded header never shows up in `argv`/process
listings. The key still lives in this config file's `env` block; to keep it
out of the file entirely, use `mcp-remote`'s `--header-file <path>` (one
`Name: value` line per header) and store the file outside your synced
config.

> **Caveat — plaintext `~/.mcp-auth`.** `mcp-remote` is an OAuth-capable
> bridge and caches any tokens it negotiates **in plaintext under the
> desktop user's home** (`~/.mcp-auth`). With a static `--header` bearer as
> above nothing sensitive should land there, but treat the directory as
> secret-bearing if you let the bridge do OAuth. A first-party,
> credential-less stdio relay (`jentic mcp --connect <url>`) is planned to
> replace these third-party bridges for exactly this reason.

### `mcp-proxy` (PyPI)

[`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy) in client mode
bridges a stdio runtime to a Streamable HTTP server — pass
`--transport=streamablehttp` (its default is SSE) and the bearer via the
`API_ACCESS_TOKEN` environment variable, which `mcp-proxy` reads natively
and sends as `Authorization: Bearer <token>` (keeping the key out of `argv`
and process listings, like the `mcp-remote` recipe above). The endpoint is
stateless server-side, so no session flags are needed on the client leg:

```json
{
  "mcpServers": {
    "jentic": {
      "command": "mcp-proxy",
      "args": [
        "--transport=streamablehttp",
        "https://your-jentic-host/mcp"
      ],
      "env": { "API_ACCESS_TOKEN": "<agent-api-key>" }
    }
  }
}
```

## Security posture

- **Bearer per request.** There is no session to hijack; every request is
  authenticated independently. Revoking the agent (or the key) cuts access
  immediately.
- **Strict `Origin` validation.** A browser-context request whose `Origin`
  is neither the configured canonical origin (`auth.canonical_base_url`)
  nor loopback is refused with 403 (the spec's DNS-rebinding rule).
  Non-browser MCP clients send no `Origin` and pass.
- **Serve it over TLS** when the deployment is reachable beyond loopback —
  the bearer rides every request.
- **Discovery is public by design**: `tools/list` answers without a
  credential, like the deployment's other self-description documents
  (`/llms.txt` advertises the endpoint only when it is enabled).

For interactive OAuth (Claude/Cursor-style browser consent instead of
bearer-paste), see [oauth-clients.md](oauth-clients.md) — the `/mcp`
resource participates in that discovery chain when
`server.mcp.oauth.enabled` is on.
