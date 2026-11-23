# The same-host threat model

> **Status:** the canonical problem statement: the same-user posture and the
> attack-path codes (AP-1…AP-4) the other same-host docs cite. The menu of
> mitigations is indexed in the [directory README](README.md).

## The problem in one sentence

Jentic One's promise — *the agent never sees the credential* — is a
**network-path** guarantee, but the one-line install lands most users with the
agent and Jentic One running as the **same OS user on the same machine**, where
that guarantee does not hold.

In that posture the boundary is defeated not by breaking the Broker but by going
around it. Everything the boundary depends on — the AES-256 encryption key, the
`jwt_secret`, the agent's Ed25519 key, its bearer tokens — lives in `0600` files
under `~/.jentic` owned by the same user the agent runs as, and `0600` does not
stop the owning user.

## The attack paths (same-user, "Tier T0")

| # | Path | Impact |
|---|------|--------|
| **AP-1** | Agent reads `~/.jentic/*.yaml` (keyset + `jwt_secret`) and the credential DB, decrypts offline | Total, silent compromise — no Broker call, no audit entry |
| **AP-2** | Agent forges an `org:admin` JWT, self-provisions a fully-scoped agent, drives the Broker | Full credential *use* (calls look authorised) |
| **AP-3** | Agent drives the operator's authenticated browser session on loopback | Full operator capability — survives even a perfect key/DB isolation |
| **AP-4** | Agent's own token/key exfiltrated and reused off-host | Off-host impersonation |

AP-1/AP-2 share one root cause: the key and operator secret live in a file the
agent's own user can read — no API-layer control fixes a file read. AP-3 is the
hard one: it rides the *human's* legitimate session, so scope caps on agents
don't touch it, and the server can't tell "operator clicked" from "agent drove
the operator's tab."

## The fix: isolate the agent

There are two structurally different moves, and they are **not** equally powerful:

- **Isolate the *agent*** (container, VM, or another machine) — the agent can't reach
  `~/.jentic`, the DB, *or* the operator's browser profile. Closes AP-1
  through AP-3 outright. It cannot close AP-4 (the agent's *own* key or token
  reused off-host): the agent must read its own credentials. AP-4 is only
  bounded — access tokens are opaque, stored hashed, and expire after
  `auth.access_ttl_seconds` (default 1 hour; refresh tokens 7 days), and carry
  only the agent's scopes; but the agent's signing key is long-lived and mints
  new tokens until the agent is disabled or archived, or its JWKS is replaced
  (revoking its API key does not stop it). `jenticctl install` binds the
  instance to `127.0.0.1` by default; the server's own default bind is `0.0.0.0`, and any exposure
  beyond loopback widens AP-4. This is the real fix.
- **Isolate *Jentic One*** (own user / separate host) — closes AP-1/AP-2, but the
  operator's browser still runs next to the agent, so **AP-3 stays open** (a
  WebAuthn step-up on dangerous mutations could close that, but no such step-up
  exists in the product today — pair with operator-browser hygiene instead).

So **agent isolation ⊃ Jentic-One isolation.** The classic "put it on a VPC"
advice is the weaker of the two: necessary for teams, but on a single dev machine
it leaves the browser gap. The only reason to prefer it is that it needs nothing
of the agent client — and the hard constraint here is that **we do not own the
agent client**, so any agent-side ask must be near-zero-effort.

The rest of this analysis is defense-in-depth. Part of it is shipped: the
encryption keyset can be injected via environment rather than a file, agent
tokens are short-lived, least-privilege, and subset-bound in scope. The rest is
possible future hardening that does **not** exist in the product today: DPoP,
a WebAuthn step-up, a same-host posture banner, a reveal-never arch test. All
worth doing; none closes a structural gap on its own.

The [directory README](README.md) indexes the concrete options that respond to
this analysis.
