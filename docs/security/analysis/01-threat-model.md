# Threat model — local same-user deployment

> Grounded in [`04-current-state.md`](04-current-state.md). Focus: the posture the
> one-line install lands in by default (Tier T0).

## Assets (what the attacker wants)

1. **Stored third-party credentials in cleartext** — the crown jewels. Only ever
   cleartext inside the Broker at injection time.
2. **The AES-256-GCM keyset** — decrypts (1) offline. In T0 it sits in
   `~/.jentic/jentic-one.yaml`.
3. **`admin.auth.jwt_secret`** — forges any operator JWT, incl. `org:admin`. Same
   file.
4. **Use of the credentials** — the attacker may not need the cleartext at all;
   driving the Broker to make authenticated upstream calls is often the real goal
   (exfiltrate data, take actions) and leaves the boundary's redaction intact.
5. **Agent Ed25519 keys + bearer tokens** — impersonate the agent off-host.

## Actors / threat sources

- **T-AGENT — a compromised or prompt-injected local agent.** The realistic
  primary threat: the agent is not malicious, but processes attacker-controlled
  content (a web page, a repo, a tool result) that redirects it. It runs as the
  operator's user with the operator's ambient access. This is the actor the whole
  product exists to contain.
- **T-MALWARE — any same-user malware.** Strictly stronger than T-AGENT; if we
  stop T-AGENT via OS isolation we mostly stop this too. Out of scope to *fully*
  solve (owning the user's account is game-over generally), but the credential
  keyset should not be a one-file grab.
- **T-REMOTE — an external attacker** who obtains an exfiltrated agent token or
  admin JWT and connects from off-host.

## Trust boundaries — where they hold and where they don't

| Boundary | Holds against | Broken by |
|---|---|---|
| **Network / API** (Broker never returns secrets; PBAC default-deny) | T-REMOTE without a token; an agent trying to *read* a secret via the API | **Nothing on this path** — this part is sound (`execute.py:547-592,746-762`) |
| **Host / filesystem** (`0600` on `~/.jentic/*`) | Other OS users | **T-AGENT / T-MALWARE — same user reads the file** |
| **Operator session** (admin JWT) | T-REMOTE without the token | Token in `localStorage`; a local agent that can reach the browser profile or drive the browser (see [`02`](02-browser-session-hijack.md)) |

The network boundary is strong; **the host and session boundaries are the ones
that collapse in T0**, and they collapse by *bypass*, not by breaking crypto.

## Attack paths (T0, same user)

### AP-1 — Read the key + DB, decrypt offline *(highest severity, lowest effort)*
The agent reads `~/.jentic/jentic-one.yaml` (the AES keyset + `jwt_secret`) and
the credential ciphertext (control DB — local Postgres/SQLite reachable as the
same user). AES-256-GCM is irrelevant when you hold the key. **Every stored
credential is compromised, silently, with no audit-log entry** (the read never
touches the Broker). *Enabler: key-in-file + same-user + DB reachable.*

### AP-2 — Forge an operator JWT, self-provision, use the Broker
The agent reads `jwt_secret`, mints an `org:admin` HS256 JWT (claims are
self-contained, verified against the secret with no DB check —
`shared/auth/verify.py:63-66`), then creates a fully-scoped agent + API key
(`agent_service.py:374-405`; ownership bypassed for `org:admin`) and calls the
Broker as that agent. It never sees cleartext but **exercises every credential**,
and the calls look legitimately authorised in the audit log. *Enabler:
secret-in-file; unbounded scope replace.*

### AP-3 — Drive the operator's browser session
Even if AP-1/AP-2 are closed (key/DB isolated to another user), an agent with
browser control reaches the same-origin UI on loopback and acts as the logged-in
operator — grant access, mint agent keys, approve its own access requests. See
[`02-browser-session-hijack.md`](02-browser-session-hijack.md). *Enabler:
shared browser profile / drivable browser + loopback-reachable UI.*

### AP-4 — Steal the agent's own token/key and use it off-host
T-REMOTE angle: exfiltrate `~/.jentic/<profile>` (opaque tokens, Ed25519 key) and
connect from anywhere. Live-resolved scopes mean the token is as powerful as the
agent's current grants. *Enabler: long-lived token/key at rest in the shared home
dir + no binding to host/network.*

## Severity ranking (impact × ease in T0)

1. **AP-1** — total silent compromise, trivial. *Critical.*
2. **AP-2** — full credential *use* + provisioning, trivial. *Critical.*
3. **AP-3** — full operator capability, needs browser control. *High.*
4. **AP-4** — off-host reuse, needs exfil path. *High.*

## The design implication

AP-1 and AP-2 share one root cause: **the key and the operator secret live in a
file the agent's own user can read.** No API-layer control can fix a file read.
The only real fixes are **(a) get the key/DB out of the agent's reach** (separate
user / separate host — Axis A) and **(b) reduce what a single stolen secret grants**
(scope caps, host/network binding, short TTLs). AP-3 needs its own class of
defense because it rides the *human's* legitimate session — covered next.
