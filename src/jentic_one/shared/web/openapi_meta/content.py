"""Document-level OpenAPI content for the Jentic Control Plane API.

The ``info`` block (title, summary, the long architecture description,
contact, license), ``servers``, and the global ``BearerAuth`` / ``OAuth2``
security schemes: the static metadata FastAPI cannot infer from type hints
and docstrings. It mirrors the hand-curated reference spec, so the generated
document carries the same richness.
"""

# This module is verbatim API documentation prose; the long-line limit is
# intentionally disabled for the metadata strings.
# ruff: noqa: E501

from __future__ import annotations

from jentic_one import __version__
from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, OIDC_PASSTHROUGH_SCOPES

#: The OpenAPI path-item keys that name an operation (the rest are path-level fields).
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}

API_TITLE = "Jentic Control Plane API"

API_SUMMARY = (
    "HTTP surface of the Jentic platform's control plane — Core / Access "
    "(credentials, direct agent bindings), Registry (registered APIs, operations, search), "
    "and Admin / Audit (execution telemetry, async jobs)."
)

API_VERSION = __version__

CONTACT = {
    "name": "Jentic Platform Team",
    "url": "https://jentic.com",
    "email": "support@jentic.com",
}

LICENSE_INFO = {
    "name": "Apache-2.0",
    "identifier": "Apache-2.0",
}

SERVERS = [{"url": "/", "description": "Same-origin (relative)"}]

# The checked-in artefact (``openapi/control/control.openapi.yaml``) is consumed
# outside any deployment (API catalogues, client generators), where a relative
# server is meaningless. It carries placeholder hosts mirroring the Broker spec;
# the live app serves ``deployment_servers`` (its real origin, else same-origin).
PUBLISHED_SERVERS = [
    {
        "url": "https://control.your-instance.example",
        "description": "Production (placeholder — set to your deployment's Control Plane host)",
    },
    {
        "url": "https://control-dev.your-instance.example",
        "description": "Development (placeholder — set to your deployment's Control Plane host)",
    },
]

API_DESCRIPTION = """## Overview ##
The **Jentic Control Plane API** is the unified HTTP surface of the
Jentic platform's control plane — every administrative and
metadata-facing endpoint outside the Broker data plane.

The control plane is the administrative counterpart to the
[Broker](../broker/swagger) data plane: where the Broker executes
upstream API calls on behalf of agents, the control plane is where
humans, operators, and agents configure *what* the Broker is
allowed to do, *for whom*, *with which credentials*, *against
which registered APIs*, and *with what telemetry retained*.

## Components ##
High-level components for the control plane API.
- **[Core / Access](#tag/Credentials)** — credentials, direct
  agent↔credential bindings, and PBAC grants and runtime params. Owns *who can
  call what, with which secret*.
- **[Registry](#tag/APIs)** — registered APIs, immutable
  revisions, operations, search, lookup. Owns *what APIs are
  registered for this deployment and what they look like at any
  given revision*.
- **[Admin / Audit](#tag/Users)** — users, permissions,
  execution telemetry, async-job tracker, and dashboard event
  stream. Owns *who can sign in with what permissions* together
  with *what actually happened* — the human roster, the
  append-only records of brokered executions, the unified
  `Job` resource that both Registry imports and Broker
  async-execution calls feed into, and a curated `/events`
  surface for dashboards and operators.

## Authentication ##

Today the platform ships a local username + password identity
provider for human users, alongside agent identity (Dynamic Client
Registration + RFC 7523 JWT-bearer assertions) — every authenticated operation expects
`Authorization: Bearer <token>` (`BearerAuth`, an opaque `at_`
token) except `GET /health`, `POST /auth/login`,
`POST /users:create-admin`, and `POST /users:redeem-invite`.
Human tokens are issued by `POST /auth/login` with a fixed 1-hour
TTL and can be re-minted before expiry via `POST /auth/refresh`
(sliding session, bounded by an absolute window —
`admin.auth.session_ttl_seconds`, 12 hours by default); agents
obtain tokens from `POST /oauth/token` (see
`BearerAuth`). The `permissions` claim on a token is a snapshot at
issue time; permission changes take effect at the next re-issue —
the next refresh or re-login (≤ 1 hour with the default TTL).

The platform ships **no default credentials**. On a fresh
install the users table is empty, so `GET /health` returns
`setup_required: true` with `next_step: create_admin`. The
operator creates the first administrator with `POST
/users:create-admin {email, password}` — a one-time,
unauthenticated, self-closing endpoint: it succeeds only while
the users table is empty and returns `410 setup_already_complete`
once any user exists (a single-row setup sentinel is the
concurrency backstop, so two racing callers — even with different
emails — cannot both bootstrap). It grants `org:admin`, sets the operator's chosen
password (so `must_change_password` is **false** — no forced
rotation), and returns a ready-to-use `LoginResponse`
(auto-login). After the first user exists `setup_required` flips
to `false`. See the **Users** tag for the full onboarding
walkthrough.

External SSO providers (Okta, AWS Cognito, Auth0, Azure AD)
plug in via the `User.auth_provider: external` hook plus an
`external_subject_id` mapping; no concrete provider ships in
this revision. Agent identity (separate from human users) uses
Dynamic Client Registration (`POST /register`) with the agent's
JWKS, then RFC 7523 JWT-bearer assertions exchanged at
`POST /oauth/token` for an opaque access token.

## Cross-cutting conventions

- Every API reference uses the loose
  `APIReference(vendor, name, version)` data structure. There is no
  foreign key between Core, Registry, and Admin / Audit — the
  tuple is the only join.
- **Error shape.** Every error response is RFC 9457 Problem
  Details (`application/problem+json`) using the shared
  `ProblemDetails` schema from
  [jentic/api-problem-details](https://github.com/jentic/api-problem-details).
  Field-level validation failures (`422`) populate `errors[]`;
  transport-level conditions reuse the same shape with status-
  and `type`-specific payloads.
- **JSON casing.** Field names in request and response bodies are
  `snake_case` throughout, including discriminator values
  (`type: api_key`, `type: bearer_token`, …). The one historical
  camelCase exception is the OpenAPI keyword `apiKey` itself when
  it appears in re-exported `securitySchemes.type` values; that's
  an OpenAPI artefact, not platform style.
- **Pagination.** List endpoints are cursor-paginated with opaque
  `next_cursor` round-trips. `has_more=false` means done. List
  envelopes are intentionally slim — `{data, has_more,
  next_cursor}` — and **do not** carry HAL-style `_links` (no
  `self`, `next`, or `prev`); cursors are forward-only and
  `next_cursor` already encodes the next page. Individual resource
  responses still use `_links` where it carries non-trivial
  information (e.g. `Job._links.result`, `AsyncQueuedResponse._links.self`,
  `Event._links`).
- **Authentication.** All operations require `Authorization:
  Bearer <token>` — an opaque access token (prefixed `at_`, not a
  JWT), obtained from `POST /oauth/token` (see `BearerAuth` for the
  per-actor grants) — except the unauthenticated
  `GET /health`, `POST /auth/login`, `POST /users:create-admin`,
  and `POST /users:redeem-invite`. See the `Users` tag for the
  first-run setup and invite-redemption flows.
- **Header namespace.** All Jentic-specific HTTP headers — request
  and response, broker and control-plane — live under the
  `Jentic-*` namespace **without** the legacy `X-` prefix
  (RFC 6648 deprecates `X-` for new protocols). `Jentic-*` is
  unambiguously a platform header; anything else on a brokered
  response belongs to the upstream API. Standard headers
  (`Authorization`, `Prefer`, `Idempotency-Key`, `Accept`,
  `traceparent`, `tracestate`) keep their RFC names.
- **Append-only audit.** Admin / Audit records are never mutated
  after the fact; retention is enforced via purge policy, not
  edit.
- **Trace correlation.** Every Broker call records a W3C
  `trace_id` on its `ExecutionRecord` and on any `Event` derived
  from it. To walk the executions you can see under one logical request, use
  `GET /executions?trace_id=…`.
- **Event stream.** The dashboard / operator UI consumes the
  `/events` resource (paginated list + SSE stream). Some events
  are informational, some require action — see the `Events` tag.
- **Revision pinning.** Every `Api` carries one `published`
  revision (`current_revision_id`) plus zero or more `draft` and
  `archived` siblings. The Broker's hot path resolves against
  `current_revision_id` by default; agents can target a specific
  `draft` revision per call via the Broker's `Jentic-Revision`
  header (`vendor:name:version=<revision_id>`, with the
  `revision_id` from the revision list). `revision_id` is an unprefixed UUID;
  the header also accepts the equivalent `rev_<revision_id without hyphens>`
  label, a header-only encoding rather than a resource ID. Control-plane
  reads (`Search`, `Inspect`, `Lookup`) accept matching `revision_id` / `revision_pins`
  parameters so dashboards can preview an unpromoted revision
  before flipping `:promote`.
- **ID prefixes.** Resource identifiers use a stable short prefix.
  Treat the prefix as part of the ID — never strip it. The
  complete table:

  | Prefix | Resource | Notes |
  |---|---|---|
  | `tk_` | Toolkit ID | Retired (theme-5): the toolkit surface and its tables are gone (Phase 6b). Ids still appear in historical records (execution attribution, audit). |
  | `ck_` | Toolkit-key record | Retired (theme-5): key records are gone with the toolkit tables (Phase 6b). Ids survive only in audit history. |
  | `cred_` | Credential ID | |
  | `exec_` | Execution record | Returned in the `Jentic-Execution-Id` response header on every brokered call. |
  | `job_` | Async job | UUIDs also accepted on inputs for backward compatibility. |
  | `evt_` | Event | ULID-shaped. |
  | `op_` | Registered operation | |
  | `usr_` | User | Org member. Resolves via `GET /users/{user_id}`. Used in `decided_by` and similar audit references. |
  | `inv_` | Invite token | One-time token issued at user creation. Plaintext value shown **once** at issue / re-issue; `:redeem-invite` consumes it. |
  | `areq_` | Access request (retired) | Retired (theme 7): the access-request flow is gone. Ids still appear in stored audit/event records. |
  | `note_` | Note | ULID-shaped. Free-form annotation attached to a registry resource — see the `Notes` tag. |
  | `ovr_` | Overlay | ULID-shaped. OpenAPI Overlay 1.0 document attached to an `Api` aggregate — see the `Overlays` tag. |
  | `jntc_live_` | Plaintext toolkit API key value (retired) | Never issued anymore. A value migrated before Phase 6b keeps authenticating — as its successor agent — until the deprecation window closes (no earlier than 2026-12-01); rotate holders to the successor agent's `jak_` key. |
  | `sak_` | Plaintext service-account API key value (retired) | Retired in 0.41: refused with `401` (the detail names the retirement). Each service account was migrated to an agent; mint a `jak_` key for that agent. |

  Surfaces still being designed (agent identity, OAuth brokers)
  will add their own prefixes when they land.
"""

BEARER_SECURITY_SCHEME = {
    "BearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "Opaque bearer access token (prefixed `at_`), sent as "
            "`Authorization: Bearer <token>`. It is **not** a JWT — it is a random "
            "string validated server-side by lookup, so it cannot be decoded by the "
            "client.\n\n"
            "Obtain one from `POST /oauth/token` using the grant for your actor type:\n"
            "- **Agents** — `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` "
            "with a JWT assertion signed by the key registered via `POST /register` "
            "(the JWT is the *assertion*, not the resulting access token).\n"
            "- **Users** — `grant_type=authorization_code` (interactive) or "
            "`grant_type=password`; refresh either with `grant_type=refresh_token`.\n\n"
            "Per-endpoint permission and actor-type requirements are not modelled in "
            "this document (OpenAPI cannot faithfully express the OR-of-permissions / "
            "`org:admin` bypass / service-layer enforcement); see "
            "`GET /reference/endpoints.json` for the authoritative authorization "
            "reference."
        ),
    }
}

_OIDC_SCOPE_DESCRIPTIONS = {
    "openid": "Issue an OpenID Connect ID token identifying the user.",
    "email": "Include the user's email address in the ID token.",
    "profile": "Include the user's display name in the ID token.",
}

#: The interactive OAuth 2.0 flow this deployment's own authorization server runs
#: (the one ``/.well-known/oauth-authorization-server`` advertises). It yields the
#: same opaque ``at_`` access token ``BearerAuth`` describes, so operations accept
#: either requirement. It carries no per-operation scopes: what a token may do is
#: decided by the actor's permissions (see the ``BearerAuth`` description), and a
#: scope only narrows a grant to a subset of them.
OAUTH2_SECURITY_SCHEME = {
    "OAuth2": {
        "type": "oauth2",
        "description": (
            "OAuth 2.0 authorization code flow with PKCE (`S256` only), run by this "
            "deployment's authorization server. The resulting access token is the same "
            "opaque bearer token `BearerAuth` describes, sent as "
            "`Authorization: Bearer <token>`.\n\n"
            "Scopes are permission names, plus the OpenID Connect identity scopes. A "
            "request for scopes beyond the client's allowed scopes is rejected with "
            "`invalid_scope`, and a token is never enforced beyond its client's allowed "
            "scopes or the consent grant. Agents normally authenticate with the "
            "JWT-bearer grant instead — see `BearerAuth`."
        ),
        "flows": {
            "authorizationCode": {
                "authorizationUrl": "/authorize",
                "tokenUrl": "/oauth/token",
                "refreshUrl": "/oauth/token",
                "scopes": {
                    **{
                        scope: _OIDC_SCOPE_DESCRIPTIONS[scope]
                        for scope in sorted(OIDC_PASSTHROUGH_SCOPES)
                    },
                    **{
                        name: f"{perm.description}."
                        if not perm.description.endswith(".")
                        else perm.description
                        for name, perm in sorted(ALL_PERMISSIONS.items())
                    },
                },
            }
        },
    }
}


def authenticated_security() -> list[dict[str, list[str]]]:
    """Security requirement for an authenticated operation: either scheme.

    A fresh list per call, so the YAML export never emits anchors/aliases for a
    shared object.
    """
    return [{"BearerAuth": []}, {"OAuth2": []}]
