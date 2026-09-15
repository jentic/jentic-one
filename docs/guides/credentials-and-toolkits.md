# How credential resolution works

How Jentic One keeps API secrets away from agents: what an agent actually
sends, how a bound credential for an API is resolved and injected
server-side, how secrets are encrypted at rest, how credentials get into the
system through providers, and what every use leaves in the audit trail.
Storing a credential and granting an agent access are walked through in
[Your first brokered call](first-call.md) (steps 4–5); read
[Deploying Jentic One securely](../security/README.md) before storing a
real one. The rest of the guides are indexed in [`README.md`](README.md).

## The promise: the agent never holds the secret

An agent calls an API by sending the broker a method and the full upstream
URL, authenticated with its Jentic token — never the API's secret. The
broker resolves the credential from the control DB, decrypts it in-process,
and attaches it to the outbound request ([`broker/core/injection.py`](../../src/jentic_one/broker/core/injection.py), driven
by the resolve → refresh → inject orchestrator in
[`broker/services/credentials/orchestrator.py`](../../src/jentic_one/broker/services/credentials/orchestrator.py)). The upstream response is
mirrored back; the injected auth material is not part of it.

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (CLI / MCP / HTTP)
    participant B as Broker
    participant C as Control DB
    participant U as Upstream API
    participant D as Admin DB

    A->>B: METHOD /{full upstream URL} + Jentic bearer
    Note over A,B: the request carries no API secret —<br/>only the agent's platform token
    B->>C: resolve one bound, covering credential for the API tuple
    C-->>B: encrypted blob (key-id-prefixed AES-256-GCM)
    B->>B: decrypt in-process, inject header / query / cookie / SigV4 signing
    B->>U: forward with auth attached
    U-->>B: response
    B->>D: credential.accessed event + execution record (credential id and name, never the secret)
    B-->>A: upstream response — injected auth is not echoed back
```

What can leave the platform is bounded:

- **No export path.** No API returns a *stored* secret (`POST /credentials`
  echoes the just-supplied value once, in its own response, and never
  again), and a [backup](../operations/backup-restore.md) contains only
  ciphertext — there is no export-secrets path, by design.
- **Redacted reads.** `GET /credentials` and `GET /credentials/{id}` return
  redacted views. Error bodies disclose at most `last4` — the tail of the
  non-secret credential *id*, never the secret.
- **Records carry identifiers.** Execution records and audit events
  reference the credential by id and name only.

The full execution pipeline — binding derivation, default-deny permission
rules, SSRF gates, the runner stack — is documented in
[Broker execution](../architecture/broker-execution.md). This guide owns the
credential-centric view.

## The model

- A **credential** stores the secret for one API identity, keyed by the tuple
  `(api_vendor, api_name, api_version)` (control DB, `credentials`). The
  `name` and `version` axes may be unset — an unset axis is a wildcard, so a
  vendor-wide credential covers every API under that vendor.
- An **agent-credential binding** (admin DB, `agent_credential_bindings`)
  grants one agent (or service account) the use of one credential. A binding
  optionally points at a shared **rule set** (`rule_set_id` → control DB
  `permission_rule_sets`); with no rule set, the binding's policy is its own
  inline permission rules. Either way access is **default-deny**: a binding
  with zero rules blocks everything
  ([`control/web/schemas/permission_rules.py`](../../src/jentic_one/control/web/schemas/permission_rules.py)).
- At execution time the Broker resolves the agent's **bound** credentials,
  filters them to the ones whose identity covers the requested API, picks the
  single winner, and injects its secret — the secret never reaches the agent.

An agent may hold bindings to several credentials of the same API
(multi-account); a credential may be bound to several agents. Neither
direction is artificially unique — disambiguation happens at resolution time,
below.

Registry (the `apis` table), Control (`credentials`,
`permission_rule_sets`), and Admin (`agent_credential_bindings`) are
**separate databases** with no foreign keys between them; the registry↔control
link is the API identity tuple carried on the credential, and the
admin↔control link is the plain `credential_id` string on the binding, both
resolved at the application layer.

## How the Broker picks a credential

For each execute request the Broker derives the caller's bindings and resolves
in this order ([`broker/services/credentials/resolver.py`](../../src/jentic_one/broker/services/credentials/resolver.py)):

1. **Binding boundary.** Only credentials the caller is bound to are ever
   considered — an unbound credential cannot resolve, appear in an error body,
   or leak its existence, even if it covers the API.
2. **Coverage.** Restrict to active credentials whose stored identity covers
   the discovered operation (each axis unset-or-equal).
3. **`Jentic-Credential-Id`** (request header) — the authoritative signal: an
   exact id names one bound credential. A supplied id that is not among the
   covering candidates is refused with `400 credential_id_not_found`, listing
   the valid candidates.
4. **`Jentic-Credential-Name`** (request header) — restrict to that name
   across all covering candidates. An unknown name is refused with
   `400 credential_name_not_found`, listing the candidates.
5. **Most-specific-wins.** A `vendor/name/version` pin beats `vendor/name`,
   which beats a bare vendor wildcard — so a vendor-wide credential coexisting
   with a pinned one resolves cleanly instead of forcing a spurious conflict.

The outcomes:

- **0 bound credentials for the API → `403 no_credential_binding`.** The
  problem body carries an agent directive naming the recovery: start a vendor
  connect flow (`jentic connect <vendor>`, over `POST /integrations:connect`)
  when the deployment's vendor registry can mint the credential, or hand off
  to a human — the operator stores the credential and binds the agent in the
  console — when it cannot. When a *bound*
  credential is a near-miss (its identity does not cover this operation), the
  refusal is `403 credential_identity_mismatch` instead — fix the credential,
  don't connect a new one.
- **1 winner → use it.** The response carries `Jentic-Credential-Id` and
  `Jentic-Credential-Name` (absent when no stored credential was used), and
  the execution record carries the same attribution — every execution names
  the credential used, never the secret.
- **A genuine same-specificity tie → `409 ambiguous_credential_binding`.**
  The body lists the candidates so the caller can resend with
  `Jentic-Credential-Name` or `Jentic-Credential-Id`. Each candidate carries
  `id`, `name`, `last4` (the tail of the non-secret credential id — never the
  secret), and `created_at`, so two similarly-named credentials remain
  distinguishable.

There is no bind-time uniqueness rule to trip over: binding a second
credential for an API the agent already reaches is allowed, and resolution
disambiguates per request. Ambiguity is only ever surfaced as the loud,
recoverable 409 above.

### A missing secret is a loud 424

A binding covers the API but no usable secret is connected — the credential
was never connected, or its account link lapsed — the broker answers
`424 credential_not_provisioned` with a `prompt_human` directive (and a
provisioning URL when one is configured) so the agent hands off to a human
instead of retrying. Every credential failure is also emitted as a typed
event (see [What's audited](#whats-audited)).

### Deleting an API deactivates its credentials

Because the databases share no referential integrity, deleting an API from the
registry does not delete the control-plane credentials that reference it. To
avoid stranding them — a later re-import plus a new credential would collide
in resolution — the API delete **deactivates** the matching credentials
(`active = false`; [`registry/repos/control_credential_boundary_repo.py`](../../src/jentic_one/registry/repos/control_credential_boundary_repo.py)). The
rows are preserved (the operator can still see and rotate them) and their
bindings survive, but a deactivated credential no longer participates in
resolution, so a re-import starts clean.

## Where secrets live

Every stored secret is encrypted with **AES-256-GCM envelope encryption**
([`shared/crypto/encryption.py`](../../src/jentic_one/shared/crypto/encryption.py)) before it touches the database. The keyset
is versioned: `credentials.encryption.entries` is a list of
`(id, 32-byte key)` pairs and `active_id` names the write key. Each blob is
prefixed with the id of the key that produced it (`<key_id>:<payload>`), so
retired keys keep decrypting old rows. An unknown key id or a failed GCM
authentication raises `DecryptionError`, which the broker maps to
`424 credential_undecryptable` with a prompt-human directive — the agent
cannot self-recover; an operator must re-add the credential. The crypto
facade (`encryption.py` and its sibling `signing.py`) is the only code
permitted to import `cryptography`, enforced by an architecture
test ([`tests/arch/test_encryption_facade.py`](../../tests/arch/test_encryption_facade.py)).

The keyset reaches the process one of three ways:

| Source | How |
| ------ | --- |
| Config file | `credentials.encryption.active_id` / `.entries` — see the [config reference](../reference/config.md) |
| Environment | `JENTIC__CREDENTIALS__ENCRYPTION__ACTIVE_ID`, `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__0__ID`, `…__0__MATERIAL` (indexed per entry) |
| Helm | generated into the release-scoped `<release>-app-secrets` Secret on first install, or supplied via `global.appSecrets.existingSecret` — see [Helm → Secrets](../installation/helm.md#secrets) |

Rotation is additive: add a new entry, flip `active_id`. New writes use the
new key immediately, but a stored secret re-encrypts only when its row is
next rewritten, so retired keys stay in the keyset — removing one makes
anything still encrypted under it permanently unreadable. The contract is
spelled out in [Upgrades](../operations/upgrades.md#what-an-upgrade-never-does).

## How credentials get in: providers

Every credential names a **provider** — the component that acquires and
maintains its secret. Three ship
([`control/services/credentials/providers/`](../../src/jentic_one/control/services/credentials/providers/)):

| Provider | Managed | Stored locally (encrypted) | Stays remote |
| -------- | ------- | -------------------------- | ------------ |
| `static` | no | the operator-supplied secret (bearer/API key/basic/SigV4/…) | nothing |
| `direct_oauth2` | yes | the OAuth client secret, plus the access and refresh tokens the platform obtains — the platform *is* the OAuth2 client | nothing |
| `pipedream` | yes | an opaque `provider_account_ref`, plus a short-lived access token cached at refresh time | the durable OAuth grant, held by Pipedream Connect |

`static` is always registered; the OAuth providers are enabled per
deployment under `credentials.providers.<name>` in the
[config reference](../reference/config.md) or at runtime with the
`jentic admin config providers` CLI.

Managed providers acquire tokens through the **connect flow**
([`control/services/credentials/connect_service.py`](../../src/jentic_one/control/services/credentials/connect_service.py)):

```mermaid
sequenceDiagram
    autonumber
    participant O as Operator / user
    participant CP as Control plane
    participant P as Provider (IdP or Pipedream Connect)

    O->>CP: POST /credentials/{id}/connect
    CP->>CP: sign state — a TTL'd JWT with a single-use nonce
    CP-->>O: authorize_url + state
    O->>P: authorize in the browser
    P->>CP: callback with code / account_id + state
    CP->>CP: verify state, consume nonce (replay refused)
    CP->>P: exchange code for tokens (direct_oauth2 only)
    CP->>CP: encrypt tokens before persisting / store account ref
```

Tokens are encrypted with the keyset above before they are persisted; a
completed connect writes an audit entry and a `credential.connected` event,
a failed one a `credential.connection_failed` event.

**The extension seam.** All three implement the `CredentialProvider`
Protocol ([`providers/base.py`](../../src/jentic_one/control/services/credentials/providers/base.py)): `begin_connect` / `complete_connect` /
`refresh`, plus `managed` and `supported_types`, resolved by name through a
`ProviderRegistry`. This Protocol is where an external vault would plug in.
No HashiCorp Vault or AWS Secrets Manager integration ships today — an
integrator implements the Protocol (as `pipedream` does for its external
vault, storing only an account reference locally) and registers it.

## Server-side token refresh

An expired OAuth2 access token is refreshed by the broker mid-call, before
injection ([`broker/services/credentials/refresh.py`](../../src/jentic_one/broker/services/credentials/refresh.py)) — the agent never
handles a refresh token and never sees the refresh happen. The refresh is
lazy and single-flight: a per-credential advisory lock (Postgres) or process
lock (SQLite) plus a double-check after acquiring, so concurrent calls
trigger one upstream refresh. Tokens are considered stale ahead of expiry by
a configurable skew, and the fresh tokens are re-encrypted before persisting.
A revoked grant (`invalid_grant`) maps to `401 credential_needs_reconnect`
with a prompt-human directive; a transient IdP failure maps to `502`.

## What's audited

Two append-only records in the admin DB, browsable in the UI and API — see
[Monitoring → executions and the audit trail](../operations/monitoring.md#what-agents-did-executions-and-the-audit-trail):

- **`audit_entries`** — who changed what: `CREATE` on store, `UPDATE` on a
  completed connect, `ENABLE`/`DISABLE`/`DELETE` on lifecycle changes, and
  `GRANT`/`REVOKE` on every agent-credential bind/unbind, each with actor and
  target (and origin, where the acting surface records one).
- **Events** — every resolve → decrypt → inject emits exactly one
  `credential.accessed` event carrying actor, credential id, provider, wire
  type, and API identity, stamped with the execution's trace id so a
  credential use joins back to the execution that triggered it. Failures are
  typed: `credential.not_provisioned`, `credential.refresh_failed`,
  `credential.undecryptable` (flagged for the Action Inbox),
  `credential.connection_failed`.

Execution records themselves carry the credential used by id and name —
never the secret.

## Lock down the agent side

The platform holds the secrets; these docs close the remaining gaps around
the agent:

- [Deploying Jentic One securely](../security/README.md) — the umbrella
  threat model, deployment tiers, and production checklist.
- [Same-host setups](../security/same-host/README.md) — what changes when
  the agent and the credential store share a machine, and what `jentic run`
  isolation buys.
- [Hardening same-host MCP](../security/same-host/mcp-same-host-hardening.md) —
  the stdio MCP server runs as the desktop user; how to contain it.
- [Identity and authorization](../architecture/identity-and-authorization.md) —
  scoped tokens and default-deny permission rules, so a leaked agent token
  is bounded.
- [Broker execution → egress controls](../architecture/broker-execution.md#egress-controls) —
  SSRF validation and DNS pinning keep the proxy from being turned against
  its own network.
- [Harden a Jentic One install](../agent/harden.md) — the agent-runnable
  hardening runbook.
