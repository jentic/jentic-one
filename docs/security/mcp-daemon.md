# The isolated MCP daemon (`jentic mcp --http` + `--connect`)

`jentic mcp` can serve its MCP surface as a **standalone local daemon** over
stateless Streamable HTTP instead of per-runtime stdio processes. Paired
with the credential-less relay (`jentic mcp --connect`), this is the
strongest same-host rung: the daemon — and the context keys it signs with —
run under a dedicated service user, agent runtimes hold **no key material at
all**, and the OS itself decides who may act as the context.

This page is the daemon's security model. For the general ladder and the
stdio-based recipes (sudo shim, container entry), see
[mcp-same-host-hardening.md](mcp-same-host-hardening.md); ready-made systemd
and launchd templates live in
[`deploy/mcp-daemon/`](../../deploy/mcp-daemon/README.md).

## The shape

```
agent runtime ── stdio ── jentic mcp --connect unix:///run/jentic-mcp/mcp.sock
                                   │  (relay: no config, no state, no keys)
                                   ▼
              jentic mcp --http  (daemon, service user, one context's keys)
                                   │  signed backend calls
                                   ▼
                          Jentic One instance / broker
```

- **The daemon** (`jentic mcp --http`) assembles the exact same MCP server
  as stdio mode — same tools, same envelopes, same attribution; the golden
  transcripts cannot tell the transports apart — and serves it at `/mcp`
  (parity with the control plane's mounted `/mcp`).
- **The relay** (`jentic mcp --connect <url>`) is a dumb stdio↔HTTP pump
  for runtimes that only speak stdio. It reads no config, touches no state
  dir, and holds no keys — byte in, byte out.

## Per-context key custody

A daemon holds exactly **one** context's keys (`--context` /
`$JENTIC_CONTEXT`, like any invocation) and signs every backend call with
them. Consequences:

- **Reachability is the boundary.** Anyone who can complete a connection to
  the daemon acts as that context's agent, with that agent's server-side
  scopes. All the posture rules below exist to control that one thing.
- **One runtime ↔ one context ↔ one daemon.** Run a second daemon (on its
  own socket, under its own service user) for a second identity — a shared
  daemon would recreate the shared-key failure mode.
- **Custody never moves.** Keys live in the service user's own
  `XDG_CONFIG_HOME`/`XDG_STATE_HOME` with owner-only modes. Callers can
  *use* them (calls signed daemon-side), never *read* them. Revoking the
  runtime = disable the agent or delete the service user; nothing to clean
  up on the desktop side.
- The API-level credential boundary is unchanged: upstream credentials are
  injected by the broker server-side and never returned to any caller.

## Binding postures (who can reach it)

The daemon refuses to start in any posture it cannot defend:

| Bind | Auth | Rules |
|---|---|---|
| Unix socket (default; `--socket`) | **OS identity** — each connection's peer uid (`SO_PEERCRED`/`LOCAL_PEERCRED`) is checked against `--allow-uid` (own uid + root always pass) | Credential-less: nothing stored on the client side |
| Loopback TCP (`--listen 127.0.0.1:…`) | Bearer token (`--token-file`, mode `0600`) | TCP has no peer identity; `--allow-unauthenticated` is the explicit, loopback-only opt-out |
| Non-loopback TCP | Bearer token **and** TLS | Refused unless `--allow-non-loopback` *and* `--tls-cert`/`--tls-key` *and* `--token-file` are all present — never plaintext, never open |

Every posture also enforces:

- **Strict Origin validation**: browser-originated requests are refused with
  403 unless the Origin is loopback or explicitly listed via
  `--allow-origin` (DNS rebinding defense).
- **Pre-authenticated discovery**: `initialize`, `tools/list`, `ping` and
  the other listing methods answer without a credential, so clients can
  boot and show the tool surface; every `tools/call` crosses the gate.
- **Stateless JSON responses**: no server-held sessions, one response per
  POST, `GET /mcp` is 405.

## Idle-exit and socket activation

The daemon exits cleanly after `--idle-timeout` (default `15m`, `0`
disables) without a request in flight — so a socket-activated unit spawns it
on the first connection and nothing lingers holding keys in memory:

- **systemd**: the socket unit owns the path; the daemon inherits it via
  `LISTEN_FDS`. If the unit hands over a socket the flag posture would have
  refused to bind (say, a non-loopback `ListenStream` without TLS+token),
  the daemon fails closed at startup.
- **launchd**: inetd *wait* mode; the daemon adopts the listening socket on
  fd 0 via `--from-launchd`.

Templates for both, with the dedicated-service-user setup, are in
[`deploy/mcp-daemon/`](../../deploy/mcp-daemon/README.md).

## The relay's rules

`jentic mcp --connect <url>` accepts three target shapes and applies the
matching credential posture:

- `unix:///path/mcp.sock` — **no credential exists**; the daemon
  authenticates the relay by its uid. A stray `--bearer-file` or
  `$JENTIC_MCP_BEARER` here is refused: it would be pointless key material.
- `http://<loopback>` — plaintext is loopback-only (same SEC-1 posture as
  the broker check); a bearer is forwarded if supplied.
- `https://…` — for a remote daemon: the caller supplies a **short-lived**
  bearer via `$JENTIC_MCP_BEARER` or `--bearer-file` (mode `0600`), the
  relay attaches it to each request and **never persists it** anywhere.

A typical runtime entry:

```json
{ "command": "jentic", "args": ["mcp", "--connect", "unix:///run/jentic-mcp/mcp.sock"] }
```

## Residual risk

Within the allowed uid set there is no further discrimination: any process
running as an allowed desktop uid may use the daemon — impersonation
*within* the audited channel, bounded by the daemon's pinned context and the
agent's server-side scopes. That is the same residual as the sudo-shim
recipe, with the same compensations (attribution, scoping, revocability) —
and strictly less exposure than plain stdio, where the runtime process
itself holds the key files.
