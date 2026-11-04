# Same-host setups: the threat model and your options

Jentic One's promise — *the agent never sees the credential* — is a
**network-path** guarantee: the Broker injects the secret into the outbound
request and the agent only ever sees the response. A one-line local install
puts the agent and Jentic One on the **same machine, as the same OS user**,
and there that guarantee does not hold.

In that posture the boundary is defeated not by breaking the Broker but by going
around it. Everything the boundary depends on — the AES-256 encryption key, the
`jwt_secret`, the agent's Ed25519 key, its bearer tokens — lives in `0600`
files owned by the same user the agent runs as, and `0600` does not stop the
owning user.

This page is the canonical statement of the problem. Other pages cite its
attack-path codes (AP-1…AP-4).

## The attack paths (same user, tier T0)

| # | Path | Impact |
|---|------|--------|
| **AP-1** | Agent reads the instance config (keyset + `jwt_secret`) and the credential DB, decrypts offline | Total, silent compromise — no Broker call, no audit entry |
| **AP-2** | Agent forges an `org:admin` JWT, self-provisions a fully-scoped agent, drives the Broker | Full credential *use* (calls look authorised) |
| **AP-3** | Agent drives the operator's authenticated browser session on loopback | Full operator capability — survives even a perfect key/DB isolation |
| **AP-4** | Agent's own token or signing key is copied and reused from elsewhere | Impersonation of that agent, within its scopes, from wherever the instance is reachable |

AP-1 and AP-2 share one root cause: the key and the operator secret live in a
file the agent's own user can read, and no API-layer control fixes a file read.
AP-3 rides the *human's* legitimate session, so scope caps on agents don't touch
it, and the server cannot tell "operator clicked" from "agent drove the
operator's tab". AP-4 is different in kind: the agent is *meant* to hold its own
credentials, so no OS boundary can keep them from it.

## Isolate the agent, not just the instance

There are two structurally different moves, and they are not equally strong:

- **Isolate the *agent*** (its own OS user, or a container). The agent cannot
  reach the instance's files, the DB, *or* the operator's browser profile. This
  closes **AP-1, AP-2 and AP-3**.
- **Isolate *Jentic One*** (its own OS user, a container, or another host).
  This closes **AP-1 and AP-2** only — the operator's browser still runs next to
  the agent, so **AP-3 stays open**. Pair it with operator-browser hygiene (a
  separate browser profile for the Jentic One UI, signed out when not in use).

So agent isolation is strictly stronger on a single machine. Moving the
instance to a separate host or private network is still the recommended posture
for real credentials, because it also removes the instance from the machine an
agent can compromise — but on its own it leaves the browser path to whatever
else the operator does on that laptop.

**AP-4 is not closed by any uid boundary** — an isolated agent still reads its
own key and tokens, because it has to. What bounds it is the token contract and
reachability:

- Access tokens are opaque, stored server-side only as a hash, and expire after
  `auth.access_ttl_seconds` (default one hour); every token and agent is
  revocable in `/app/agents`.
- A token carries only the agent's own scopes, so a stolen one buys nothing the
  agent could not already do — and every use is attributed to that agent.
- A token or signing key is only redeemable where the instance is reachable.
  `jenticctl install` binds the instance to `127.0.0.1` by default, so material
  carried off the machine is useless unless you expose the instance. If you bind
  it to another interface, AP-4 widens to everything that can reach it.

## Your options

The options differ in strength and can be combined. Pick by how the agent runs
and what the credentials are worth; the
[deployment tiers](../README.md#deployment-tiers) put them on one ladder.

| Option | Closes | Where |
| ------ | ------ | ----- |
| **Separate host / private network** — the instance never shares a machine with any agent | AP-1, AP-2 by construction; pair with browser hygiene for AP-3. The recommended posture for real credentials | [Tiers T3–T4](../README.md#deployment-tiers) |
| **Isolate the agent — `jentic run`** — the CLI launches a coding agent as its own unprivileged Unix user, confined per session | AP-1, AP-2, AP-3 on this machine; AP-4 bounded as above | Usage: [local-agent guide](../../guides/local-agent.md). Design: [isolation-design.md](isolation-design.md) |
| **Isolate the instance** — run Jentic One under a different OS user, in a container, or rootless | AP-1, AP-2 only — **AP-3 stays open** | [Tier T2](../README.md#deployment-tiers) |
| **MCP-spawned agents** — a runtime (Claude Desktop, Cursor, …) spawns `jentic mcp` as the desktop user | The same problem through a different seam. Four recipes, from isolating the instance to a socket-activated daemon that holds the keys | [mcp-same-host-hardening.md](mcp-same-host-hardening.md), [mcp-daemon.md](mcp-daemon.md) |
| **Generic sandboxing** — the agent's built-in sandbox, OS primitives, containers, microVMs | Defense in depth of varying strength; not a substitute for the rows above | [Sandboxing the agent](../README.md#sandboxing-the-agent-axis-a) |

Independently of the option you choose, keep the encryption key out of the
config file (`material_file` / `material_env`, see the
[concrete controls](../README.md#concrete-controls)) and keep agent tokens
least-privilege.
