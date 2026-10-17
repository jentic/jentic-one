"""Static OpenAPI metadata for the Jentic Control Plane API.

This module is the single source of truth for the document-level metadata that
FastAPI cannot infer from type hints and docstrings: the ``info`` block (title,
summary, the long marketing/architecture description, contact, license),
``servers``, the tag catalogue (with descriptions), the Redoc ``x-tagGroups``
extension, and the global ``BearerAuth`` / ``OAuth2`` security schemes.

It was ported from the hand-curated ``openapi/control/control.openapi.yaml`` so
the generated spec carries the same richness. Wire it into a FastAPI app via
:func:`install_openapi_metadata`.
"""
# This module is verbatim API documentation prose (info description + tag docs);
# the long-line limit is intentionally disabled for the metadata strings.
# ruff: noqa: E501

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from jentic_one import __version__
from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, OIDC_PASSTHROUGH_SCOPES
from jentic_one.shared.web.endpoint_permissions import build_operation_auth_map
from jentic_one.shared.web.openapi_responses import PROBLEM_JSON, STATUS_EXAMPLES

_HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}

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
- **Append-only audit.** Audit entries, execution records and events
  are never mutated after the fact. They — and finished jobs and their
  results — are kept indefinitely: there is no API or setting to prune
  them yet, so trim them at the database level. Approved-execution
  results are the exception: they expire after
  `held_executions.result_retention_seconds`.
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
  header. Control-plane reads (`Search`, `Inspect`,
  `Lookup`) accept matching `revision_id` / `revision_pins`
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
  | `rev_` | API revision | ULID-shaped. |
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

# Tag names and descriptions, ported verbatim from the reference spec. Order
# here is the display order Redoc uses within each tag group.
OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "Credentials",
        "description": (
            "Part of the **Core / Access** bounded context — the authoritative record of "
            '"who can call what, with which secret". A `Credential` is the auth secret + '
            "header configuration the Broker injects on the way out to an upstream API. "
            "Credentials reference registry content via the loose `(vendor, name, version)` "
            "identity tuple (see `APIReference`) — there is no foreign key between Core and "
            "Registry, by design. Cleartext secret material is returned **exactly once** at "
            "creation (and on rotation via `PATCH`). Read paths return a redacted projection "
            "— last-N characters, hints, never the secret itself."
        ),
    },
    {
        "name": "Permission Rule Sets",
        "description": (
            "Part of the **Core / Access** bounded context — a `PermissionRuleSet` is a "
            "named, shareable ordered rule list (first-match-wins, default-deny) that many "
            "direct agent↔credential bindings can point at via their `rule_set_id`, so a "
            "policy edit or `permissions:test` stays a single-place operation. A binding "
            "with no `rule_set_id` uses its own inline rules instead. Rule sets carry "
            "policy, not secrets; deleting one that bindings still reference is refused "
            "(409 `rule_set_in_use`)."
        ),
    },
    {
        "name": "Vendors",
        "description": (
            "Part of the **Core / Access** bounded context — the platform-curated catalog "
            "of verified vendors that support agent-initiated OAuth connect flows. Each "
            "entry describes the OAuth flows available (device_authorization, "
            "authorization_code) and the scope catalog with read/write classification. "
            "Vendor entries are seeded from operator config, not user-managed — the API "
            "surfaces them read-only so the SPA and agents can discover which vendors "
            "are available and pick a flow at ``:connect`` time."
        ),
    },
    {
        "name": "Integrations",
        "description": (
            "Part of the **Core / Access** bounded context — agent-initiated OAuth "
            "connect sessions. A caller (agent or UI) begins a session via "
            "``POST /integrations:connect``, choosing a vendor and requested scope set; "
            "the platform provisions a pending credential, initiates the vendor's OAuth "
            "flow (device_authorization or authorization_code depending on the vendor), "
            "and returns a session id + human-facing challenge (device user_code / "
            "authorize_url).\n\n"
            "The initiator polls ``GET /connect-sessions/{id}/status`` until the session "
            "reaches ``connected`` (tokens vaulted) or a terminal failure. Sessions are "
            "capped by a TTL; unfinished sessions can be cancelled via "
            "``POST /connect-sessions/{id}:cancel``. The ``:confirm`` step is a UI-only "
            "hand-off that binds any pre-declared permission rules to the resulting "
            "credential."
        ),
    },
    {
        "name": "APIs",
        "description": (
            "The `Api` aggregate root — what APIs are registered for this deployment, "
            "addressable by the loose `(vendor, name, version)` tuple. Every successful import "
            "lands a new immutable `ApiRevision`; the `Api` carries the live "
            "`current_revision_id` plus presentation metadata (`display_name`, `description`, "
            "`icon_url`).\n\n"
            "Mutation surfaces:\n\n"
            "- `POST /apis` — async import (url / inline). Returns 202 + `Job (kind=import)`.\n"
            "- `PATCH /apis/{vendor}/{name}/{version}` — metadata-only updates (display name, "
            "description, icon). Cannot mutate revisions or spec content.\n"
            "- `POST /apis/{vendor}/{name}/{version}/revisions/{revision_id}:promote` — make a "
            "draft revision live.\n"
            "- `POST /apis/{vendor}/{name}/{version}/revisions/{revision_id}:archive` — retire "
            "a draft revision.\n"
            "- `DELETE /apis/{vendor}/{name}/{version}` — deregister the API and all its "
            "revisions.\n\n"
            "Newly imported revisions are **never auto-promoted** — callers must explicitly "
            "`:promote` to flip the live pointer. Hot-path Broker calls can pin to a specific "
            "revision via the `Jentic-Revision` request header for testing without flipping "
            "the pointer.\n\n"
            "Sub-resources of an `Api` live under sibling tags: `API Operations` enumerates "
            "the operations exposed by a registered revision; `API Spec` returns the "
            "underlying OpenAPI document for tooling."
        ),
    },
    {
        "name": "Governed Hosts",
        "description": (
            "The caller's own governed host set — the upstream hosts reachable through its "
            "credential bindings (admin bindings → credential scopes, suspended or inactive "
            "included → registered APIs), deduplicated and sorted. **Always self-scoped**: "
            "the set is derived for the authenticated identity; there is no cross-actor or "
            "admin variant.\n\n"
            "The response carries a content-derived SHA-256 `digest` (also emitted as a "
            "strong `ETag`), so integrators — e.g. a native gate scoping traffic "
            'interception per agent — poll with `If-None-Match: "<digest>"` and receive '
            "an empty `304` until the host set actually changes. Deliberately unpaginated: "
            "the set is bounded by the caller's own bindings and the digest covers it "
            "atomically."
        ),
    },
    {
        "name": "API Operations",
        "description": (
            "Sub-resource of **APIs** (Registry bounded context). Enumerate the operations "
            "exposed by a registered API — either the live (`current_revision_id`) view or a "
            "specific revision. Complements `Search` (ranked discovery) and `Inspect` "
            "(single-operation detail) by giving callers the deterministic list of every "
            "registered operation under one API without ranking, paging through `Search`, or "
            "parsing the spec.\n\n"
            "Each row is slim — the structural detail (parameters, response schema, security "
            "scheme) lives behind `_links.inspect` for callers that need it."
        ),
    },
    {
        "name": "API Spec",
        "description": (
            "Sub-resource of **APIs** (Registry bounded context). Download the underlying "
            "OpenAPI document for a registered API — either the live (`current_revision_id`) "
            "view or a specific revision. Supports `application/json`, "
            "`application/openapi+yaml`, and `application/yaml` via `Accept`-header content "
            'negotiation, with `Content-Disposition: attachment; filename="..."` so browser '
            "downloads work cleanly.\n\n"
            "By default the response includes any deployment-local overlays merged on top of "
            "the imported base. Pass `?overlays=false` to download the raw imported spec "
            "instead."
        ),
    },
    {
        "name": "Catalog",
        "description": (
            "The public API **catalog** — a registry-side browse/preview/import surface over "
            "the Jentic public-APIs GitHub manifest. Powers the Discover experience: list "
            "importable APIs (`GET /catalog`, keyset cursor-paginated), preview an entry's "
            "operations (`GET /catalog/{api_id}/operations`), refresh the cached manifest "
            "(`POST /catalog:refresh`, `org:admin`), and import an entry into the local "
            "registry (`POST /catalog/{api_id}:import`).\n\n"
            "The catalog is a cache of an upstream document, distinct from the local "
            "registry (`GET /apis`): `/apis` is what this deployment has imported, `/catalog` "
            "is what it *could* import. Each entry's `registered` flag reflects whether its "
            "`spec_url` already backs a local API revision. Import resolves to a plain "
            "fetchable spec URL and reuses the standard async import job — no catalog identity "
            "ever crosses into the importer."
        ),
    },
    {
        "name": "Search",
        "description": (
            "Lexical (full-text) search across registered operations, ranked by relevance. "
            "Returns a slim row per operation; each row carries `_links.inspect` pointing "
            "into the `Inspect` tag for full structural detail."
        ),
    },
    {
        "name": "Inspect",
        "description": (
            "Resolve a single registered operation to its full structural detail — "
            "parameters, response schema, security scheme, server URL. Heavier than `Lookup` "
            "(which is the Broker hot path) and separate from `Search` (which only returns "
            "ranked summaries). Supports JSON, Markdown, and OpenAPI YAML response formats via "
            "`Accept`-header content negotiation."
        ),
    },
    {
        "name": "Lookup",
        "description": (
            "The Broker's read interface into Registry — fast, narrow lookup from a real-world "
            "URL to a Jentic `operation_id`. Hot path; not for human use. The capability map "
            "names this as the Execution → Registry read interface."
        ),
    },
    {
        "name": "Notes",
        "description": (
            "Free-form annotations attached to registry resources — an `Api`, an "
            "`ApiRevision`, a registered `operation_id`, an `ExecutionRecord`, or a "
            '`Credential`. Used to capture human or agent observations about quirks: *"this '
            'endpoint silently truncates email addresses to 64 chars"*, *"this credential '
            'rejects calls outside US business hours"*, *"this operation needs `Accept: '
            "application/json` even though the spec doesn't say so\"*.\n\n"
            "Notes are an information surface for agents — they accumulate over time as the "
            "platform learns from real executions and surface back through `_links.notes` on "
            "the resources they annotate (woven in by individual surfaces as they need it).\n\n"
            "**Edit model.** Notes are editable via `PATCH`; each successful edit bumps a "
            "monotonic `revision` counter on the `Note`. Use the `If-Match` header on `PATCH` "
            "and `DELETE` to guard against concurrent modification.\n\n"
            "**Confidence promotion.** Notes carry both a `confidence` value (`observed | "
            "suspected | verified`) and a `confidence_source` (`client | platform_promoted`). "
            "Today only `client` is written by callers; the `platform_promoted` value is "
            "reserved for a future surface that auto-promotes `observed` notes to `verified` "
            "once enough matching `execution_feedback` accumulates.\n\n"
            "**Authentication.** Any authenticated principal (agent or human) can post any "
            "note type. Per-type policy (e.g. only humans may file `correction`) is enforced "
            "platform-side and not modelled in the spec."
        ),
    },
    {
        "name": "Overlays",
        "description": (
            "OpenAPI Overlay 1.0 (`https://spec.openapis.org/overlay/v1.0.0`) documents that "
            "correct an imported spec without rewriting it. Overlays capture wrong required "
            "fields, missing security schemes, undocumented headers, silent payload "
            "truncation, and other places where the upstream spec disagrees with the upstream "
            "API's actual behaviour — without forking the spec or waiting for an upstream "
            "fix.\n\n"
            "Overlays attach to the `Api` aggregate (not to a specific `ApiRevision`) and "
            "continue to apply across revision bumps until materially superseded. The optional "
            "`target_revision_id` records the revision the overlay was authored against, for "
            "auditing.\n\n"
            "**Lifecycle.** New overlays start in `pending`. The Broker calls `POST "
            "/apis/{vendor}/{name}/{version}/overlays/{overlay_id}:confirm` after the first "
            "successful upstream execution that exercised an overlay-patched operation, "
            "transitioning the overlay to `confirmed`. This is the platform's only documented "
            "data-plane → control-plane writeback. Once `confirmed`, an overlay stays "
            "`confirmed` — repeated `:confirm` calls return `200` with the existing record "
            "unchanged.\n\n"
            "**Edit model.** Overlay documents are editable via `PATCH` only while `pending`. "
            "Once `confirmed` (or `deprecated`), the document is immutable — to change a "
            "confirmed overlay, submit a new one and let the old one age out via `DELETE` "
            "(which soft-deprecates).\n\n"
            "**Deletion.** `DELETE` is a **soft-deprecation** — the row is preserved with "
            "`status: deprecated` for audit. Deprecated overlays no longer apply to "
            "spec-download merges (`?overlays=false` becomes the default for that overlay) but "
            "stay queryable via `GET`. There is no hard-delete in this surface.\n\n"
            "**Authentication.** Any authenticated principal (agent or human) can submit, "
            "edit, confirm, or deprecate overlays. Platform-side rate limits gate abuse."
        ),
    },
    {
        "name": "Executions",
        "description": (
            "Append-only audit log of every Broker call (sync or async) — timing, status, "
            "trace IDs, and the upstream API reference (`vendor:name:version`). Bodies are not "
            "stored. Records are written by the Broker; this surface is read-only. To walk the "
            "history of a single logical request that fanned out into multiple upstream calls, "
            "list with `?trace_id={trace_id}`. An execution is visible only to the actor "
            "that ran it, the owner of the agent that ran it, and `org:admin`; to any other "
            "caller it is indistinguishable from a missing execution (`404`)."
        ),
    },
    {
        "name": "Jobs",
        "description": (
            "In-flight async-job tracker for both Registry import jobs and Broker "
            "async-execution jobs — one uniform `Job` shape keyed by `kind`. Lifecycle only — "
            "type-specific result payloads (the imported revisions for `import`, upstream "
            "response body for `execution`) live under `/jobs/{job_id}/result`. Results stay "
            "readable indefinitely; only an approved execution's result expires (after "
            "`held_executions.result_retention_seconds`), after which `/jobs/{job_id}/result` "
            "answers `410 job_result_expired` while the job itself remains. "
            "A job is visible only to the actor that created it, the owner of the agent "
            "that created it, and `org:admin`; to any other caller it is indistinguishable "
            "from a missing job (`404`), including on `:cancel`."
        ),
    },
    {
        "name": "Held Executions",
        "description": (
            "Executions held for human approval. When a call matches a permission rule with "
            "`effect: require-approval`, the broker does not run it: it answers `202` with a "
            "held execution (`held_…`) and a review link. A reviewer, the agent's owner or any "
            "`org:admin`, calls `POST /executions/holds/{id}:decide` to approve or deny it.\n\n"
            "An approved call runs once; `GET /executions/holds/{id}` reports where it stands and "
            "`GET /executions/holds/{id}/result` returns the call's execution document, or the problem "
            "explaining why it never ran (denied, expired or refused at run time). A held execution nobody "
            "decides expires after the configured window. Agents may never decide; the agent "
            "that filed one may withdraw it. Each caller sees only the held executions it may "
            "review or filed."
        ),
    },
    {
        "name": "Events",
        "description": (
            "Curated, severity-tagged event stream surfaced to dashboards and operators. Each "
            "event is either **informational** (no action required, e.g. `import.completed`) "
            "or **actionable** (requires operator follow-up, e.g. `credential.expiring_soon`, "
            "`execution.repeated_failure`). Events reference the underlying `ExecutionRecord` "
            "or `Job` via `_links` and share `trace_id` for correlation. Subscribe live via "
            "`GET /events/stream` (Server-Sent Events) or poll `GET /events` with a `since=` "
            "filter. An event is visible only to the actor it names (`actor_id`), to the "
            "subject it is about (for example the owner of the credential or the agent "
            "concerned), to the human owner of either when that is an agent, and to "
            "`org:admin`; system events with no subject are visible only to `org:admin`. To "
            "any other caller an event is indistinguishable from a missing one (`404`)."
        ),
    },
    {
        "name": "Users",
        "description": (
            "Part of the **Admin / Audit** bounded context — the human roster of the "
            "organisation. One organisation, many users; admin surfaces here cover create / "
            "list / read / update / delete plus the AIP-style action verbs (`:disable`, "
            "`:enable`, `:reissue-invite`). Self-service is intentionally minimal: `GET "
            "/users/me` to introspect, `POST /users/me:change-password` to rotate (which "
            "re-mints and returns a fresh token, clearing any `must_change_password` gate so "
            "the caller need not re-login).\n\n"
            "**First-run setup (no default credentials).** The platform ships with an empty "
            "users table — there is no seeded `admin@local` account. On a fresh install `GET "
            "/health` reports `setup_required: true` (with `next_step: create_admin`). The "
            "operator creates the first administrator via `POST /users:create-admin "
            "{email, password}`: a one-time, unauthenticated endpoint that succeeds only while "
            "no user exists, grants `org:admin`, and returns an auto-login `LoginResponse` with "
            "`must_change_password: false` (the operator chose the password, so there is no "
            "forced rotation). It **self-closes** — once any user exists it returns `410 "
            "setup_already_complete`, so it is safe to leave exposed during first boot; a "
            "single-row setup sentinel is the concurrency backstop against racing callers "
            "(even ones using different emails). After the "
            "first user exists, `setup_required` flips to `false`.\n\n"
            "**Onboarding flow.** Admins create new users via `POST /users` (no password "
            "supplied). The response carries a one-time `inv_…` invite token shown **once**; "
            "the admin hands it off out-of-band (Slack, 1Password, email — there is no "
            "platform-side email infrastructure in this revision). The new user redeems via "
            "`POST /users:redeem-invite {invite_token, password}` and is auto-logged-in via "
            "the returned `LoginResponse`. Tokens default to a 7-day TTL and are single-use; "
            "`:reissue-invite` issues a fresh one if the original is lost or expired.\n\n"
            "**Authentication.** Today only local (username + password) identity ships. The "
            "`User` schema carries `auth_provider: local | external` and an "
            "`external_subject_id` field reserved for future SSO integrations (Okta, Cognito, "
            "Azure AD); when SSO arrives, those users will be created via a separate "
            "provider-specific router. The on-the-wire token contract — `Authorization: Bearer "
            "<jwt>` — is unchanged either way.\n\n"
            "**Disabling and revocation.** `:disable` flips `active=false` and rejects "
            "subsequent `POST /auth/login` **and** `POST /auth/refresh`. **Existing JWTs keep "
            "working until they expire** (≤ 1 hour with the platform's default TTL). This is a "
            "deliberate trade-off; sub-minute revocation would require a server-side session "
            "table.\n\n"
            "**Session lifetime.** Login JWTs carry a 1-hour TTL. The UI keeps an active "
            "session alive by calling `POST /auth/refresh` before expiry (sliding session); "
            "refresh re-reads permissions and the `must_change_password` gate from the "
            "database and is refused with 401 `session_expired` once the original "
            "authentication is older than the absolute window "
            "(`admin.auth.session_ttl_seconds`, 12 hours by default), forcing a fresh login."
        ),
    },
    {
        "name": "Permissions",
        "description": (
            "Part of the **Admin / Audit** bounded context — the catalogue of grantable "
            "permission strings. Permissions are namespaced `resource:action` strings "
            "(`users:write`, `capabilities:execute`, `credentials:read`, …) gating every "
            "other surface in the API. The set is platform-defined and small; `GET "
            "/permissions` returns it in full, with each entry describing what it does, what "
            "it implies (the static implication map; e.g. `capabilities:execute` implies "
            "`apis:read` and `executions:read`), and whether the calling user is authorised to "
            "grant it.\n\n"
            "One reserved superpower short-circuits individual checks in code: `org:admin` "
            "(full deployment-wide access, granted via direct DB action). It is not enumerated "
            "to non-holders by `GET /permissions`, and is rejected by `PUT "
            "/users/{user_id}/permissions` from any caller who doesn't already hold it.\n\n"
            "The same vocabulary is used for `User.permissions` — the coarse set embedded in "
            "the JWT — "
            "so the catalogue below covers user assignment. The per-binding fine-grained "
            "`PermissionRule[]` (the inner PBAC tier) lives separately on the direct "
            "agent↔credential bindings under the `Credentials` tag."
        ),
    },
    {
        "name": "System",
        "description": (
            "Operational endpoints for service health and platform tooling. Not part of any "
            "bounded context — surfaced here so orchestrators and load balancers have a stable "
            "probe target."
        ),
    },
    # --- Platform-actor surfaces (not in the original hand-curated reference; the
    # live app is a superset). Tagged here so the generated spec is coherent. ---
    {
        "name": "Identity",
        "description": (
            "Identity introspection for the calling principal (human or agent) — `GET /me` "
            "returns the resolved subject and its permissions behind the presented token."
        ),
    },
    {
        "name": "Agents",
        "description": (
            "Agent actors — autonomous principals that call the Broker. Covers the agent "
            "lifecycle (list, read, approve / deny, enable / disable, archive) and the "
            "credentials directly bound to each agent."
        ),
    },
    {
        "name": "OAuth",
        "description": (
            "OAuth 2.0 / OIDC endpoints exposed by the platform authorization server — the "
            "authorize, token, introspection, and revocation endpoints plus "
            "the redirect callback."
        ),
    },
    {
        "name": "Agent Registration",
        "description": (
            "Dynamic agent registration (RFC 7591-style) — register a new agent client, poll "
            "registration status, update, or delete a registration."
        ),
    },
    {
        "name": "Discovery",
        "description": (
            "Unauthenticated metadata discovery — the JWKS document and OAuth authorization "
            "server metadata under `/.well-known/*`."
        ),
    },
    {
        "name": "Actors",
        "description": (
            "Unified actor directory — a lightweight read-only view across all actor types "
            "(users and agents). Returns ID-to-name mappings for UI cache "
            "hydration so dashboards can display friendly names wherever an `actor_id` appears."
        ),
    },
    {
        "name": "Audit",
        "description": (
            "Append-only administrative audit trail — who did what, when. Distinct from "
            "`Executions` (brokered upstream calls); this records control-plane mutations."
        ),
    },
    {
        "name": "Monitoring",
        "description": (
            "Dashboard-oriented aggregation endpoints — execution volume, success/failure "
            "ratios, and top operations over a bounded time window. Results are cached "
            "in-process (TTL ~120 s) so concurrent dashboard viewers never stampede the "
            "database."
        ),
    },
    {
        "name": "Configuration",
        "description": (
            "Runtime, DB-backed platform configuration. Lets an operator set, read, and list "
            "configuration that previously required hand-editing backend YAML and restarting "
            "the server — starting with credential provider configs (e.g. Pipedream). A "
            "successful write rebuilds the in-process provider registry so the change takes "
            "effect without a restart. Secret fields (e.g. `client_secret`) are encrypted at "
            "rest and redacted on read. **Topology note:** in the combined (single-process) "
            "deployment a write takes effect immediately for all surfaces. In a multi-process "
            "deployment the control/broker processes pick up the new config at their next "
            "boot, not the instant the admin write lands; cross-process propagation is a "
            "tracked follow-up."
        ),
    },
    {
        "name": "OAuth Clients",
        "description": (
            "Admin-managed registry of third-party OAuth clients (confidential, secret-bearing). "
            "Registered clients integrate with Jentic One via the standard Authorization Code + "
            "PKCE flow. Admins can create, list, update, rotate secrets, and disable clients. "
            "Disabling a client immediately invalidates all tokens issued through it."
        ),
    },
    {
        "name": "OAuth App Registrations",
        "description": (
            "Admin-managed OAuth application registrations that end users on the "
            "instance can SSO through. Each row holds a client_id, endpoints, and "
            "(for authorization-code flows) an encrypted client secret. Registrations "
            "are per-vendor and per-flow-kind; users then start a connect session "
            "against a registration to mint their own tokens."
        ),
    },
    {
        "name": "MCP",
        "description": (
            "MCP (Model Context Protocol) transport reporting. The `jentic mcp` stdio "
            "server terminates all MCP protocol traffic locally; the control plane only "
            "sees plain HTTP. This tag covers the small reporting surface behind it — "
            "today, the config-registration report `jentic setup`/`jentic skill init` "
            "send after writing an MCP server entry for a detected agent runtime, which "
            "feeds the config-written → first-session → first-execute adoption funnel."
        ),
    },
]

# Redoc tag groups (vendor extension). Tags not listed here still render; this
# only controls the left-nav grouping.
X_TAG_GROUPS: list[dict[str, Any]] = [
    {
        "name": "Core / Access",
        "tags": [
            "Credentials",
            "Permission Rule Sets",
            "Vendors",
            "Integrations",
        ],
    },
    {
        "name": "Registry",
        "tags": [
            "APIs",
            "API Operations",
            "API Spec",
            "Catalog",
            "Search",
            "Inspect",
            "Lookup",
            "Notes",
            "Overlays",
        ],
    },
    {
        "name": "Admin / Audit",
        "tags": [
            "Users",
            "Actors",
            "Permissions",
            "Executions",
            "Held Executions",
            "Jobs",
            "Events",
            "Audit",
            "Monitoring",
            "Configuration",
            "OAuth Clients",
            "OAuth App Registrations",
        ],
    },
    {
        "name": "Platform Actors",
        "tags": [
            "Identity",
            "Agents",
            "OAuth",
            "Agent Registration",
            "Discovery",
        ],
    },
    {
        "name": "Operations",
        "tags": ["System", "MCP"],
    },
]

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


# Operations that are intentionally unauthenticated and must NOT inherit the
# global BearerAuth requirement.
#
# Every entry carries its own ``#`` justification comment on the line(s)
# directly above it — what it returns and what binds or limits it in place of a
# platform token. ``tests/arch/test_route_auth_gate.py`` enforces this, fails
# on entries that match no real route, and fails on any route that neither
# authenticates nor is declared here.
PUBLIC_OPERATION_IDS: frozenset[str] = frozenset(
    {
        # Root liveness probe of the combined app: dependency-free, returns only
        # a status string and the running version.
        "getHealth",
        # Backend-identity probe: unauthenticated, self-describing, no secrets.
        "getInstance",
        # Admin-surface health probe (``/admin/health`` combined, ``/health``
        # standalone): liveness plus the setup_required first-run flag the SPA
        # and CLI read before any user exists. No tenant data.
        "health",
        # Control-surface liveness probe: dependency-free, returns only the
        # surface name and status.
        "controlHealth",
        # Registry-surface liveness probe: dependency-free, returns only the
        # surface name and status.
        "registryHealth",
        # Auth-surface liveness probe: dependency-free, returns only the
        # surface name and status.
        "authHealth",
        # Password login: the caller has no token yet; the credential is the
        # request body, verified by AuthService.authenticate.
        "login",
        # Invite redemption: the caller has no token yet; bound by the
        # single-use invite token in the request body.
        "redeemInvite",
        # First-run setup: create the first admin. Self-closes (410) once any
        # user exists, so it is safe to leave unauthenticated.
        "createAdmin",
        # OAuth token endpoint: the caller has no platform token yet (it calls
        # this to *get* one); authenticated by the grant itself (code + PKCE,
        # refresh token, client assertion). Rate limited.
        "tokenEndpoint",
        # OAuth /authorize entry point: a browser starting the flow has no
        # platform token yet; bound by the registered client and its redirect
        # URI, and carries the flow forward in signed state. Rate limited.
        "authorizeEndpoint",
        # Agent dynamic registration (RFC 7591): the agent has no platform
        # token yet; it receives a pending registration that an admin must
        # approve before any token is issued.
        "registerEndpoint",
        # Anonymous OAuth-client DCR front door: flagship MCP
        # clients register anonymously; the boundary is admin approval +
        # consent, not registration. Rate limited and config-gated instead.
        "registerOauthClientEndpoint",
        # Credential-connect OAuth redirect callback: the upstream provider
        # redirects the browser here; bound by a signed state param, not a session.
        "oauthCallback",
        # IdP login redirect callback on the /authorize flow: bound by the
        # signed authorize state param, not a session. Rate limited.
        "authorizeOauthCallback",
        # Approval-pending status poll: anonymous by design (the polling
        # browser has no platform token yet), bound by the signed
        # approval-state blob minted at /authorize — not a session and not a
        # bare client_id. Returns only the pending/approved/denied tri-state;
        # rate limited in its own bucket instead.
        "approvalStatusEndpoint",
        # OAuth consent screen (presented after IdP login, before issuing the
        # code): bound by the consent handle from the signed flow state.
        "consentPage",
        # OAuth consent form submission: same browser-mid-flow caller as the
        # consent page; bound by the consent handle and rate limited. Mints the
        # auth code only on approval.
        "consentSubmit",
        # Inline first-agent creation on the zero-agents consent page (P4):
        # same browser-mid-flow caller as the consent submit (no platform
        # token yet); bound by the consent handle + a signed single-use
        # agent-create blob, rate limited like the other consent endpoints.
        "consentAgentCreate",
        # Pending-agent status poll (P4 hybrid awaiting page): anonymous by
        # design like approvalStatusEndpoint — the polling browser has no
        # platform token — and keyed by the signed agent-status blob the
        # consent flow minted for one specific agent, never a bare agent id.
        # Returns only the pending/approved/denied tri-state; rate limited in
        # the approval-status bucket instead.
        "consentAgentStatus",
        # Local-account login form on the /authorize flow: the caller is a
        # browser mid-authorization with no token yet. Config-gated
        # (auth.local_login.enabled → 404) and rate limited instead.
        "loginPage",
        # Local-account login form submission: the credential is the request
        # body; bound by the signed authorize state and a single-use CSRF
        # nonce. Config-gated (404) and rate limited like the login page.
        "loginSubmit",
        # Browser-facing OAuth error page (no auth; just renders an error code).
        "errorPage",
        # JWKS: the public signing keys clients need to verify tokens; public
        # key material only, unauthenticated by spec.
        "jwks",
        # RFC 8414 authorization-server metadata: endpoint URLs and supported
        # algorithms only, unauthenticated by spec.
        "oauthAuthorizationServer",
        # /mcp-scoped RFC 8414 discovery document for the path-scoped issuer.
        # Unauthenticated by spec; config-gated (404) instead.
        "mcpOauthAuthorizationServer",
        # RFC 9728 protected-resource metadata for /mcp. Unauthenticated by
        # spec; config-gated (404) instead.
        "mcpOauthProtectedResource",
        # Root-path alias of the RFC 9728 /mcp protected-resource document.
        # Unauthenticated by spec; config-gated (404) instead.
        "mcpOauthProtectedResourceRootAlias",
        # Public IdP-login capability hint (enabled flag + provider name only;
        # no secrets). The SPA reads this pre-login to render the SSO button.
        "authIdpDescriptor",
    }
)


#: Operations that **do** authenticate, but not via the global ``BearerAuth``
#: platform token, so they must not advertise that security requirement. Unlike
#: :data:`PUBLIC_OPERATION_IDS` these are *not* public: they still reject bad
#: credentials with ``401``, so the documented error responses are kept intact.
#: Their ``security`` is dropped to ``[]`` (no platform bearer requirement); the
#: real credential is described in the endpoint reference's ``auth_note`` (see
#: ``NON_IDENTITY_AUTH`` in ``endpoint_permissions.py``).
NON_BEARER_AUTH_OPERATION_IDS: frozenset[str] = frozenset(
    {
        # RFC 7592 registration-status poll: authenticated by the
        # Registration-Access-Token issued at registration, not a platform bearer
        # token. It still returns 401 on a missing/invalid/expired RAT
        # (RegistrationAccessDeniedError -> 401), so the 401 response stays.
        "pollStatusEndpoint",
        # RFC 7009 token revocation (G11): dual-arm client authentication. The
        # form-encoded arm authenticates by OAuth client_id lineage binding
        # (public clients, auth method "none" — no platform bearer); the JSON
        # arm keeps the pre-G11 platform-bearer contract and still 401s on a
        # bad bearer, so the 401 response stays while the blanket BearerAuth
        # requirement is dropped.
        "revokeEndpoint",
    }
)


#: Operations whose request-validation failures are reshaped at the router into
#: their governing spec's error dialect, so the FastAPI 422 can never be
#: returned and the auto-generated 422 response is dropped from the spec:
#: the DCR door's RFC 7591 §3.2.2 ``400 {"error": "invalid_client_metadata"}``
#: (``_Rfc7591Route`` in ``auth/web/routers/oauth_client_registration.py``) and
#: the token endpoint's RFC 6749 §5.2 dialect (``_TokenRoute`` in
#: ``auth/web/routers/oauth.py`` — body parsing runs entirely inside
#: ``_parse_token_request``, which answers §5.2 ``invalid_request``). The
#: route-specific error responses are documented on the route decorators.
ROUTER_RESHAPED_422_OPERATION_IDS: frozenset[str] = frozenset(
    {
        "registerOauthClientEndpoint",
        "tokenEndpoint",
    }
)


# Ordered (regex, tag) rules mapping a request path to its fine-grained tag.
# First match wins, so more specific patterns precede broader ones. This is the
# single source of operation tagging: it overwrites whatever coarse tags the
# routers were registered with, reconciling everything to the reference names.
_TAG_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/health$"), "System"),
    (re.compile(r"^/[^/]+/health$"), "System"),
    (re.compile(r"^/instance$"), "System"),
    (re.compile(r"^/system/version$"), "System"),
    (re.compile(r"^/admin/config"), "Configuration"),
    (re.compile(r"^/credentials"), "Credentials"),
    (re.compile(r"^/permission-rule-sets"), "Permission Rule Sets"),
    (re.compile(r"^/integrations"), "Integrations"),
    (re.compile(r"^/connect-sessions"), "Integrations"),
    (re.compile(r"^/vendors"), "Vendors"),
    (re.compile(r"^/apis/.+/overlays"), "Overlays"),
    (re.compile(r"^/apis/.+/operations$"), "API Operations"),
    (re.compile(r"^/apis/.+/openapi$"), "API Spec"),
    (re.compile(r"^/apis"), "APIs"),
    # Identity-scoped host digest for integrators (#1278).
    (re.compile(r"^/governed-hosts$"), "Governed Hosts"),
    # Catalog (Discover) — browse/preview/import + the :refresh verb. Broad
    # ^/catalog is safe: no other surface shares the prefix.
    (re.compile(r"^/catalog"), "Catalog"),
    (re.compile(r"^/search"), "Search"),
    (re.compile(r"^/inspect"), "Inspect"),
    (re.compile(r"^/lookup"), "Lookup"),
    (re.compile(r"^/notes"), "Notes"),
    (re.compile(r"^/monitoring"), "Monitoring"),
    (re.compile(r"^/executions/holds"), "Held Executions"),
    (re.compile(r"^/executions"), "Executions"),
    (re.compile(r"^/jobs"), "Jobs"),
    (re.compile(r"^/events"), "Events"),
    (re.compile(r"^/mcp"), "MCP"),
    (re.compile(r"^/permissions"), "Permissions"),
    (re.compile(r"^/actors"), "Actors"),
    (re.compile(r"^/users"), "Users"),
    (re.compile(r"^/auth/(login|refresh)"), "Users"),
    (re.compile(r"^/auth/idp"), "Discovery"),
    (re.compile(r"^/audit"), "Audit"),
    (re.compile(r"^/admin/oauth-clients"), "OAuth Clients"),
    # Grant listings/revoke share the OAuth tag with the grant kill switch.
    (re.compile(r"^/admin/oauth-grants"), "OAuth"),
    # Anonymous DCR front door — before the broader ^/oauth rule below.
    (re.compile(r"^/oauth-clients"), "OAuth Clients"),
    # Admin-managed shared OAuth application registrations — before ^/oauth.
    (re.compile(r"^/oauth-app-registrations"), "OAuth App Registrations"),
    # Platform-actor surfaces (superset, not in the original reference).
    (re.compile(r"^/agents"), "Agents"),
    (re.compile(r"^/oauth"), "OAuth"),
    (re.compile(r"^/authorize"), "OAuth"),
    (re.compile(r"^/error"), "OAuth"),
    (re.compile(r"^/login$"), "OAuth"),
    (re.compile(r"^/register"), "Agent Registration"),
    (re.compile(r"^/\.well-known"), "Discovery"),
    (re.compile(r"^/me$"), "Identity"),
]


def resolve_tag(path: str) -> str | None:
    """Return the fine-grained tag for a request path, or ``None`` if unmatched."""
    for pattern, tag in _TAG_RULES:
        if pattern.match(path):
            return tag
    return None


def _camelize(name: str) -> str:
    """Convert a snake_case route function name to camelCase."""
    parts = [p for p in name.split("_") if p]
    if not parts:
        return name
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def generate_operation_id(route: APIRoute) -> str:
    """Derive an operationId from the handler name (camelCase).

    Used as FastAPI's ``generate_unique_id_function`` so every operation gets a
    stable, reference-style camelCase id (``create_credential`` -> "createCredential")
    instead of FastAPI's default ``create_credential_credentials_post``. Routes
    that pass an explicit ``operation_id=`` bypass this entirely, which is how
    collision-prone names (e.g. the per-surface ``health`` handlers) stay unique.
    """
    return _camelize(route.name)


def deployment_servers(public_base_url: str = "") -> list[dict[str, str]]:
    """The live document's ``servers``: the deployment's public origin, else ``SERVERS``.

    ``server.public_base_url`` names the origin clients reach the control
    surfaces on, so a spec downloaded from a configured deployment stays
    callable outside the browser. Unset, the same-origin relative server is the
    only honest value.
    """
    if public_base_url:
        return [{"url": public_base_url, "description": "This deployment"}]
    return SERVERS


def fastapi_metadata_kwargs(public_base_url: str = "") -> dict[str, Any]:
    """Keyword arguments to spread into ``FastAPI(...)`` for document metadata."""
    return {
        "title": API_TITLE,
        "summary": API_SUMMARY,
        "description": API_DESCRIPTION,
        "version": API_VERSION,
        "servers": deployment_servers(public_base_url),
        "contact": CONTACT,
        "license_info": LICENSE_INFO,
        "openapi_tags": OPENAPI_TAGS,
        "generate_unique_id_function": generate_operation_id,
    }


_PROBLEM_DETAIL_REF = "#/components/schemas/ProblemDetail"


def _is_problem_detail_media(media: Any) -> bool:
    return (
        isinstance(media, dict) and (media.get("schema") or {}).get("$ref") == _PROBLEM_DETAIL_REF
    )


def _normalise_error_responses(responses: dict[str, Any]) -> None:
    """Render Problem Details error responses as ``application/problem+json``.

    FastAPI files an error ``model=ProblemDetail`` under the route's response
    media type: ``application/json`` by default, but ``text/html`` for an
    HTML-page route (``response_class=HTMLResponse``). This moves whichever
    entry carries the ``ProblemDetail`` schema to the RFC 9457 media type and
    attaches the canonical per-status example, matching the platform error
    contract. Entries in another dialect (e.g. the RFC 6749 §5.2 OAuth error
    body, or the framework's plain route-not-found body) keep their
    ``application/json`` media type.
    """
    for code, response in responses.items():
        if not isinstance(response, dict) or not code.startswith(("4", "5")):
            continue
        content = response.get("content")
        if not isinstance(content, dict):
            continue
        # An error ``model=`` on an HTML-page route lands under ``text/html``;
        # error bodies are always JSON, so fold it into ``application/json``.
        html = content.get("text/html")
        if isinstance(html, dict) and html.get("schema"):
            content.pop("text/html")
            json_media = content.setdefault("application/json", {})
            json_media.setdefault("schema", html["schema"])
        if code not in STATUS_EXAMPLES:
            continue
        problem_media_types = [mt for mt, m in content.items() if _is_problem_detail_media(m)]
        if not problem_media_types:
            continue
        media = content[problem_media_types[0]]
        for mt in problem_media_types:
            content.pop(mt, None)
        # A schema-less problem+json placeholder (an earlier pass, or a route
        # that pre-declared the media type) is superseded by the real entry.
        content.pop(PROBLEM_JSON, None)
        media["example"] = STATUS_EXAMPLES[code]
        content[PROBLEM_JSON] = media


def _add_default_problem_response(responses: dict[str, Any]) -> None:
    """Document the RFC 9457 catch-all as the operation's ``default`` response.

    Any status the operation does not list (an unmapped 5xx, a proxy-injected
    error) still arrives as Problem Details: ``unhandled_exception_handler``
    guarantees it for an unclaimed exception, so every operation — the bare
    public probes included — gets the same ``default``.
    """
    if "default" not in responses:
        responses["default"] = {
            "description": "Unexpected error, as RFC 9457 Problem Details.",
            "content": {
                PROBLEM_JSON: {
                    "schema": {"$ref": "#/components/schemas/ProblemDetail"},
                    "example": STATUS_EXAMPLES["500"],
                }
            },
        }


def _stamp_permission_metadata(
    method: str,
    path: str,
    operation: dict[str, Any],
    operation_auth: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Stamp the operation's ``security`` from its recovered identity dependency.

    The OpenAPI document models only the real authentication mechanism — an
    opaque bearer token (not a JWT), described as ``BearerAuth`` and, for the
    interactive authorization-code flow that issues it, ``OAuth2``. The per-operation
    permission/actor-type join is *not* expressed here: OpenAPI's ``security`` model
    cannot faithfully carry our OR-of-permissions semantics, the ``org:admin``
    superuser bypass, the typical-caller hint, or the fact that many permissions are
    enforced in the service layer rather than at the gateway. Encoding it as a
    fabricated OAuth2 flow
    would misrepresent enforcement, so that richer authorization reference lives
    in the endpoint reference (:mod:`jentic_one.shared.web.endpoint_reference`,
    served at ``GET /reference/endpoints.json``), which the CLI and docs SPA
    consume.

    Always sets an explicit per-operation ``security`` so downstream consumers
    never fall back to the global document-level requirement to decide whether an
    operation authenticates: an operation with no recovered identity dependency is
    stamped ``security: []`` (public); an authenticated one is stamped
    ``[{"BearerAuth": []}, {"OAuth2": []}]`` (either scheme, no per-operation
    scopes). This keeps the public/authenticated boundary derived
    from the actual route dependency rather than inferred.
    """
    info = operation_auth.get((method.upper(), path))
    if info is None or not info["authenticated"]:
        # No identity dependency recovered for this operation: it does not
        # authenticate. Mark it explicitly public so the boundary is unambiguous.
        operation["security"] = []
        responses = operation.get("responses", {})
        for code in ("401", "403"):
            responses.pop(code, None)
        return
    operation["security"] = authenticated_security()


def install_openapi_metadata(app: FastAPI) -> None:
    """Install a custom ``openapi()`` that augments FastAPI's generated document.

    FastAPI populates paths, operations, and component schemas from the routes
    and models; this wrapper layers on the static document metadata that cannot
    be inferred: the ``BearerAuth`` security scheme, a global security
    requirement (relaxed for public endpoints), and the Redoc ``x-tagGroups``
    extension. Title/summary/description/tags/servers are passed to the
    ``FastAPI(...)`` constructor and surface through ``get_openapi`` here.
    """

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
            contact=CONTACT,
            license_info=LICENSE_INFO,
        )

        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes.update(BEARER_SECURITY_SCHEME)
        security_schemes.update(OAUTH2_SECURITY_SCHEME)

        schema["security"] = authenticated_security()
        schema["x-tagGroups"] = X_TAG_GROUPS

        operation_auth = build_operation_auth_map(app)

        for path, path_item in schema.get("paths", {}).items():
            resolved = resolve_tag(path)
            for method, operation in path_item.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                if resolved is not None:
                    operation["tags"] = [resolved]
                op_id = operation.get("operationId")
                if op_id in PUBLIC_OPERATION_IDS:
                    operation["security"] = []
                    # Public endpoints don't authenticate, so 401/403 don't apply.
                    responses = operation.get("responses", {})
                    for code in ("401", "403"):
                        responses.pop(code, None)
                elif op_id in NON_BEARER_AUTH_OPERATION_IDS:
                    # Authenticates by a non-bearer credential (e.g. an RFC 7592
                    # Registration-Access-Token): drop the platform BearerAuth
                    # requirement but keep the 401 it genuinely returns on a bad
                    # credential. It never reaches the permission/authz layer, so the
                    # 403 (which only the permission gate raises) is dropped.
                    operation["security"] = []
                    operation.get("responses", {}).pop("403", None)
                else:
                    _stamp_permission_metadata(method, path, operation, operation_auth)
                if op_id in ROUTER_RESHAPED_422_OPERATION_IDS:
                    # Validation failures are reshaped to the governing spec's
                    # dialect at the router; the framework 422 can never be
                    # returned.
                    operation.get("responses", {}).pop("422", None)
                _normalise_error_responses(operation.get("responses", {}))
                _add_default_problem_response(operation.get("responses", {}))
                _summarise_parameter_examples(operation)
                _describe_request_body(operation, components.get("schemas", {}))
            _summarise_path_item(path_item)
            _describe_path_item(path_item)

        _compose_component_examples(components.get("schemas", {}))
        _propagate_examples(schema.get("paths", {}), components.get("schemas", {}))
        _add_oneof_discriminators(schema, components.get("schemas", {}))

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def _summarise_parameter_examples(operation: dict[str, Any]) -> None:
    """Give every parameter example a ``summary``.

    ``openapi_examples.example()`` labels the canonical values itself; any other
    value (a page size, a status filter) is labelled from its parameter here,
    because only the spec knows which parameter an example is attached to.
    """
    for param in operation.get("parameters") or []:
        for entry in (param.get("examples") or {}).values():
            if isinstance(entry, dict) and not entry.get("summary"):
                entry["summary"] = f"Example {param['in']} value for `{param['name']}`"


def _summarise_path_item(path_item: dict[str, Any]) -> None:
    """Set the path item's ``summary`` from its operations' summaries.

    OpenAPI's path-level ``summary`` applies to every operation on the path;
    FastAPI never emits one. Joining the operation summaries in spec order
    (``"List agents; Register agent"``) states exactly what the path offers.
    """
    if path_item.get("summary"):
        return
    summaries = [
        op["summary"]
        for method, op in path_item.items()
        if method.lower() in _HTTP_METHODS and isinstance(op, dict) and op.get("summary")
    ]
    if summaries:
        path_item["summary"] = "; ".join(summaries)


def _describe_path_item(path_item: dict[str, Any]) -> None:
    """Set the path item's ``description`` to a method-by-method index.

    The joined ``summary`` says what the path offers; the description adds which
    HTTP method does what (``"`GET`: List agents. `POST`: Register agent."``),
    so a reader of the path alone can pick the right operation.
    """
    if path_item.get("description"):
        return
    entries = [
        f"`{method.upper()}`: {op['summary'].rstrip('.')}."
        for method, op in path_item.items()
        if method.lower() in _HTTP_METHODS and isinstance(op, dict) and op.get("summary")
    ]
    if entries:
        path_item["description"] = " ".join(entries)


def _describe_request_body(operation: dict[str, Any], schemas: dict[str, Any]) -> None:
    """Give the operation's ``requestBody`` a ``description``.

    FastAPI never emits one. Prefer the body model's own description (its
    docstring, already public on the component); otherwise fall back to naming
    the operation the payload is for.
    """
    body = operation.get("requestBody")
    if not isinstance(body, dict) or body.get("description"):
        return
    for media in (body.get("content") or {}).values():
        schema = media.get("schema") or {}
        ref = schema.get("$ref", "")
        target = schemas.get(ref.rsplit("/", 1)[-1], {}) if ref else schema
        if target.get("description"):
            body["description"] = target["description"]
            return
    op_id = operation.get("operationId")
    if op_id:
        body["description"] = f"Request payload for the `{op_id}` operation."


_MISSING = object()


def _example_for(schema: Any, schemas: dict[str, Any], stack: tuple[str, ...]) -> Any:
    """One example for ``schema``, or ``_MISSING`` if it cannot be built from leaves.

    Uses the schema's own ``examples``/``example``, else follows a ``$ref`` to the
    component (composing it if needed), else the first ``anyOf``/``oneOf`` branch
    that yields one, else builds an array from ``items`` or an object from its
    properties. ``stack`` stops recursion through self-referencing models.
    """
    if not isinstance(schema, dict):
        return _MISSING
    if schema.get("examples"):
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if name in stack or name not in schemas:
            return _MISSING
        return _component_example(name, schemas, stack)
    for key in ("anyOf", "oneOf"):
        for branch in schema.get(key) or []:
            if isinstance(branch, dict) and branch.get("type") == "null":
                continue
            value = _example_for(branch, schemas, stack)
            if value is not _MISSING:
                return value
    if schema.get("type") == "array":
        item = _example_for(schema.get("items"), schemas, stack)
        return _MISSING if item is _MISSING else [item]
    if "properties" in schema:
        return _object_example(schema, schemas, stack)
    return _MISSING


def _object_example(schema: dict[str, Any], schemas: dict[str, Any], stack: tuple[str, ...]) -> Any:
    """An object example from its properties; ``_MISSING`` if a required one has none."""
    required = set(schema.get("required") or [])
    value: dict[str, Any] = {}
    for prop, prop_schema in (schema.get("properties") or {}).items():
        example = _example_for(prop_schema, schemas, stack)
        if example is _MISSING:
            if prop in required:
                return _MISSING
            continue
        value[prop] = example
    return value


def _component_example(name: str, schemas: dict[str, Any], stack: tuple[str, ...]) -> Any:
    component = schemas[name]
    if component.get("examples"):
        return component["examples"][0]
    if "example" in component:
        return component["example"]
    if "properties" not in component:
        return _example_for(component, schemas, (*stack, name))
    return _object_example(component, schemas, (*stack, name))


def _compose_component_examples(schemas: dict[str, Any]) -> None:
    """Give object components and their nested-model properties a composed example.

    Field-level ``examples=`` sit on leaf properties; a property typed as another
    model (``$ref``, ``list[Model]``, ``Model | None``) and the component as a
    whole have none. Composing them from the leaves keeps one source of truth —
    a composed value is valid exactly when its leaves are, which the arch tests
    check — while giving readers (and agents) a complete, realistic object.
    Components that already declare ``examples`` are left as written.
    """
    for name, component in schemas.items():
        if not isinstance(component, dict) or "properties" not in component:
            continue
        for prop_schema in component["properties"].values():
            if not isinstance(prop_schema, dict) or "examples" in prop_schema:
                continue
            if "example" in prop_schema:
                continue
            value = _example_for(prop_schema, schemas, (name,))
            if value is not _MISSING:
                prop_schema["examples"] = [value]
        if "examples" not in component and "example" not in component:
            value = _object_example(component, schemas, (name,))
            if value is not _MISSING and value:
                component["examples"] = [value]


def _variant_tag(ref: str, schemas: dict[str, Any], prop: str) -> str | None:
    """The single constant value ``prop`` takes in the ``$ref``'d variant, if any."""
    variant = schemas.get(ref.rsplit("/", 1)[-1]) or {}
    spec = (variant.get("properties") or {}).get(prop) or {}
    if "const" in spec:
        return str(spec["const"])
    enum = spec.get("enum")
    return str(enum[0]) if isinstance(enum, list) and len(enum) == 1 else None


def _add_oneof_discriminators(node: Any, schemas: dict[str, Any]) -> None:
    """Declare the discriminator Pydantic omits for a callable-discriminated union.

    A union discriminated by a callable (``Discriminator(fn)``) — used where one
    variant may omit the tag and is selected by default — renders as a bare
    ``oneOf`` of ``$ref`` schemas. When every variant pins the same property to a
    distinct constant, that property *is* the discriminator; declare it (with
    the explicit mapping) so clients can deserialise without trial-matching.
    """
    if isinstance(node, list):
        for item in node:
            _add_oneof_discriminators(item, schemas)
        return
    if not isinstance(node, dict):
        return
    for value in node.values():
        _add_oneof_discriminators(value, schemas)
    variants = node.get("oneOf")
    if "discriminator" in node or not isinstance(variants, list) or len(variants) < 2:
        return
    refs = [v["$ref"] for v in variants if isinstance(v, dict) and isinstance(v.get("$ref"), str)]
    if len(refs) != len(variants):
        return
    first = schemas.get(refs[0].rsplit("/", 1)[-1]) or {}
    for prop in first.get("properties") or {}:
        tags = [_variant_tag(r, schemas, prop) for r in refs]
        if all(tags) and len(set(tags)) == len(tags):
            node["discriminator"] = {
                "propertyName": prop,
                "mapping": dict(zip(tags, refs, strict=True)),
            }
            return


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _matches_type(value: Any, branch: dict[str, Any]) -> bool:
    """Whether ``value`` has the JSON type ``branch`` declares (bool is not a number)."""
    kinds = _JSON_TYPES.get(branch.get("type", ""))
    if kinds is None or (isinstance(value, bool) and bool not in kinds):
        return False
    return isinstance(value, kinds)


def _seed_schema_example(target: dict[str, Any], value: Any) -> None:
    """Put ``value`` on a parameter schema and on each typed branch it satisfies."""
    target.setdefault("examples", [value])
    for key in ("anyOf", "oneOf"):
        for branch in target.get(key) or []:
            if isinstance(branch, dict) and _matches_type(value, branch):
                _seed_schema_example(branch, value)
    items = target.get("items")
    if isinstance(items, dict) and isinstance(value, list) and value:
        _seed_schema_example(items, value[0])


def _first_example(holder: dict[str, Any]) -> Any:
    """The first example value declared on a parameter or schema, else ``_MISSING``."""
    if "example" in holder:
        return holder["example"]
    examples = holder.get("examples")
    if isinstance(examples, dict) and examples:
        first = next(iter(examples.values()))
        return first.get("value", _MISSING) if isinstance(first, dict) else _MISSING
    if isinstance(examples, list) and examples:
        return examples[0]
    return _MISSING


def _propagate_examples(paths: dict[str, Any], schemas: dict[str, Any]) -> None:
    """Surface existing examples where clients look for them.

    A parameter's example lives on the parameter (a named map for the docs UI);
    JSON-Schema-driven tooling reads ``schema.examples`` instead, so mirror it
    there. A JSON request/response that references a component with an example
    gets that example on the media type, so it renders without resolution.
    Nothing is invented: every value is one the spec already declares.
    """
    for path_item in paths.values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for param in operation.get("parameters") or []:
                value = _first_example(param)
                if value is not _MISSING and isinstance(param.get("schema"), dict):
                    _seed_schema_example(param["schema"], value)
            bodies = [operation.get("requestBody") or {}]
            bodies += list((operation.get("responses") or {}).values())
            for body in bodies:
                for media_type, media in (body.get("content") or {}).items():
                    if "json" not in media_type or "example" in media or "examples" in media:
                        continue
                    media_schema = media.get("schema") or {}
                    ref = media_schema.get("$ref")
                    source = (
                        schemas.get(ref.rsplit("/", 1)[-1], {})
                        if isinstance(ref, str)
                        else media_schema
                    )
                    value = _first_example(source)
                    if value is not _MISSING:
                        media["example"] = value
