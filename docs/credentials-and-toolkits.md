# Credentials and bindings

How stored credentials relate to the APIs they authenticate and the
agent-credential bindings that grant their use, and how the Broker resolves
exactly one credential — or refuses loudly — on every execution.

> The filename keeps its historical `credentials-and-toolkits` name so
> existing links resolve; toolkits themselves are retired — access is granted
> per agent-credential binding.

## Model

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
  (`control/web/schemas/permission_rules.py`).
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
in this order (`broker/services/credentials/resolver.py`):

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
  problem body carries an agent directive: file
  `jentic access request --api <vendor/name>` when a credential already serves
  the API, or `--provision` when nothing serves it yet. When a *bound*
  credential is a near-miss (its identity does not cover this operation), the
  refusal is `403 credential_identity_mismatch` instead — fix the credential,
  don't file a request.
- **1 winner → use it.** The response carries `Jentic-Credential-Id` and
  `Jentic-Credential-Name` so every execution attributes the credential used.
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

## Deleting an API deactivates its credentials

Because the databases share no referential integrity, deleting an API from the
registry does not delete the control-plane credentials that reference it. To
avoid stranding them — a later re-import plus a new credential would collide
in resolution — the API delete **deactivates** the matching credentials
(`active = false`; `registry/repos/control_credential_boundary_repo.py`). The
rows are preserved (the operator can still see and rotate them) and their
bindings survive, but a deactivated credential no longer participates in
resolution, so a re-import starts clean.
