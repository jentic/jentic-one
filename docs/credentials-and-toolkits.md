# Credentials and toolkits

How stored credentials relate to the APIs they authenticate and the toolkits
that expose them, and the invariants the platform enforces to keep credential
resolution unambiguous.

## Model

- A **credential** stores the secret for one API, keyed by the API identity
  `(api_vendor, api_name, api_version)` (control DB, `credentials`).
- A **toolkit** groups the credentials an agent may use. A
  **toolkit-credential binding** (`toolkit_credential_bindings`) associates a
  toolkit with a credential.
- At execution time the Broker resolves the **single active** credential for the
  requested API and injects its secret — the secret never reaches the agent.

Registry (the `apis` table) and Control (`credentials`,
`toolkit_credential_bindings`) are **separate databases** with no foreign key
between them; the link is the API identity tuple carried on the credential.

## Invariant: one active credential per API within a toolkit

Within a single toolkit there is **at most one active credential per API
identity**. Two active credentials for the same API in the same toolkit make
resolution ambiguous — the Broker cannot tell which secret to inject, so it
refuses with `409 ambiguous_credential`. The platform prevents that state at
bind time: binding a second active credential for an API a toolkit already
covers is rejected with `409 conflicting_api_binding`. Unbind the existing
credential first to replace it.

A credential may be **reused across toolkits** (e.g. a broad read-only key in
one toolkit and a scoped key in another) — the one-per-API rule is scoped to a
single toolkit, not global.

## Deleting an API deactivates its credentials

Because the two databases share no referential integrity, deleting an API from
the registry does not delete the control-plane credentials that reference it.
To avoid stranding them — a later re-import plus a new credential would collide
with `409 ambiguous_credential` — the API delete **deactivates** the matching
credentials (`active = false`). The rows are preserved (the operator can still
see and rotate them) but no longer participate in resolution, so a re-import
starts clean.

## When ambiguity is genuinely surfaced

If two active credentials for one API are ever resolved (the loud, correct
refusal), the `409 ambiguous_credential` body lists the candidates so the caller
can pick which to remove. Each candidate carries `id`, `name`, `last4` (the tail
of the non-secret credential id — never the secret), and `created_at`, so two
similarly-named credentials remain distinguishable.
