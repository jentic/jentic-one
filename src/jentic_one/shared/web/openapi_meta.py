"""Static OpenAPI metadata for the Jentic Control Plane API.

This module is the single source of truth for the document-level metadata that
FastAPI cannot infer from type hints and docstrings: the ``info`` block (title,
summary, the long marketing/architecture description, contact, license),
``servers``, the tag catalogue (with descriptions), the Redoc ``x-tagGroups``
extension, and the global ``BearerAuth`` security scheme.

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
from jentic_one.shared.web.endpoint_scopes import build_operation_auth_map
from jentic_one.shared.web.openapi_responses import PROBLEM_JSON, STATUS_EXAMPLES

_HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}

API_TITLE = "Jentic Control Plane API"

API_SUMMARY = (
    "HTTP surface of the Jentic platform's control plane — Core / Access "
    "(credentials, toolkits), Registry (registered APIs, operations, search), "
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
- **[Core / Access](#tag/Credentials)** — credentials, toolkits,
  and PBAC grants and runtime params. Owns *who can call what, with which secret*.
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
Registration + RFC 7523 JWT-bearer assertions) and service-account
client credentials — every authenticated operation expects
`Authorization: Bearer <token>` (`BearerAuth`, an opaque `at_`
token) except `GET /health`, `POST /auth/login`,
`POST /users:create-admin`, and `POST /users:redeem-invite`.
Human tokens are issued by `POST /auth/login` with a fixed 1-hour
TTL; agents and service accounts obtain tokens from
`POST /oauth/token` (see `BearerAuth`). The `permissions` claim on a
token is a snapshot at issue time; permission changes take effect at
the next re-issue (≤ 1 hour with the default TTL).

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
  from it. To walk all executions under one logical request, use
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
  | `tk_` | Toolkit ID | Public; appears in URLs and logs. **Not** the toolkit secret. |
  | `ck_` | Toolkit-key record | One toolkit can hold many keys; each `ck_…` is a key record (label, IP allowlist, revoked flag). The plaintext key value is `jntc_live_…`. |
  | `cred_` | Credential ID | |
  | `exec_` | Execution record | Returned in the `Jentic-Execution-Id` response header on every brokered call. |
  | `job_` | Async job | UUIDs also accepted on inputs for backward compatibility. |
  | `evt_` | Event | ULID-shaped. |
  | `op_` | Registered operation | |
  | `rev_` | API revision | ULID-shaped. |
  | `usr_` | User | Org member. Resolves via `GET /users/{user_id}`. Used in `acknowledged_by`, `decided_by`, and similar audit references. |
  | `inv_` | Invite token | One-time token issued at user creation. Plaintext value shown **once** at issue / re-issue; `:redeem-invite` consumes it. |
  | `areq_` | Access request | Per-toolkit human-approval ticket; lives on the `Access Requests` sub-tag of `Toolkits`. |
  | `note_` | Note | ULID-shaped. Free-form annotation attached to a registry resource — see the `Notes` tag. |
  | `ovr_` | Overlay | ULID-shaped. OpenAPI Overlay 1.0 document attached to an `Api` aggregate — see the `Overlays` tag. |
  | `jntc_live_` | Plaintext toolkit API key value | The secret. Returned **once** at toolkit creation / key issue. |

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
        "name": "Toolkits",
        "description": (
            "Part of the **Core / Access** bounded context — a `Toolkit` is a scoped bundle "
            "of credentials and permissions issued to an agent or service. This tag covers "
            "**toolkit lifecycle**: list, create, read, update, delete. Sub-resources (keys, "
            "credential bindings, per-binding permission rules) live under the sibling tags "
            "`Toolkit Keys`, `Toolkit Credentials`, and `Toolkit Permissions`.\n\n"
            "Two distinct values are involved: the **toolkit ID** is `tk_…` (public; appears "
            "in URLs, logs, and `Jentic-Toolkit-Id` headers), and the **toolkit secret** is "
            "`jntc_live_…` (private; the plaintext API key shown exactly once at toolkit "
            "creation or when a new key is issued)."
        ),
    },
    {
        "name": "Toolkit Keys",
        "description": (
            "Sub-resource of **Toolkits** (Core / Access bounded context). A toolkit can hold "
            "many API keys at once — different agents, machines, or environments typically "
            "each get their own. Each key record (`ck_…`) carries a label, optional CIDR "
            "allowlist, and a revoked flag. The plaintext value (`jntc_live_…`) is shown "
            "exactly once at creation and never returned afterwards (only `key_preview` "
            "exposes the last few characters).\n\n"
            "Rotation is do-and-then-revoke: issue a fresh key, switch callers, then `DELETE` "
            "the old key. There is no in-place rotate operation — multi-key rotation is always "
            "do-and-then-revoke so live callers never see a window where every key is invalid."
        ),
    },
    {
        "name": "Toolkit Credentials",
        "description": (
            "Sub-resource of **Toolkits** (Core / Access bounded context). A toolkit doesn't "
            "own credentials directly — it **binds** to credentials that already exist in "
            "`/credentials`. The binding is the link record that authorises the Broker to "
            "inject a specific credential on calls made under this toolkit. The same "
            "credential can be bound to many toolkits.\n\n"
            "Bindings can carry initial fine-grained permission rules inline via "
            "`ToolkitCredentialBindRequest.permissions[]`; subsequent changes go through the "
            "`Toolkit Permissions` sub-resource."
        ),
    },
    {
        "name": "Toolkit Permissions",
        "description": (
            "Sub-resource of **Toolkits** (Core / Access bounded context). The fine-grained "
            "PBAC tier — per-`(toolkit, credential)` allow / deny / require-approval rules "
            "evaluated server-side. Rules use a priority model: `deny` > `require-approval` > "
            "`allow` — the strictest matching rule wins. Absence of a matching rule is an "
            "implicit deny. System rules (`_system: true`) participate in the same priority "
            "pool as user rules.\n\n"
            "The coarse JWT-embedded scope tier on `Toolkit.permissions` "
            "(`capabilities:execute`, `apis:read`) is checked separately and composes with "
            "these rules — both must allow."
        ),
    },
    {
        "name": "Access Requests",
        "description": (
            "Actor-agnostic, multi-item access-request surface (Core / Access bounded "
            "context). When an agent's brokered call is rejected by a permission rule — or "
            "the agent needs access it doesn't yet have — the agent files an `AccessRequest` "
            "containing one or more line items (`AccessRequestItem`). The request enters "
            "`pending` state and surfaces an `approve_url` that the agent presents to a human "
            "reviewer.\n\n"
            "Reviewers `:decide` individual items (approve or deny each); filers can `:amend` "
            "pending items (adjust rules or target) or `:withdraw` the entire request. Each "
            "item transitions independently; the envelope status reflects the aggregate "
            "(`pending` while any item is pending, `partially_approved` when some items are "
            "decided but others remain, terminal once all items resolve).\n\n"
            "Pending requests carry a TTL (default 7 days, configurable). Envelope lifecycle: "
            "`pending → partially_approved → approved | denied | withdrawn | expired`.\n\n"
            "**Identity scoping.** List and get operations are identity-scoped: filers see "
            "their own requests, reviewers see their inbox, `org:admin` sees all. Non-owners "
            "receive `404` (not `403`) to avoid leaking existence.\n\n"
            "**Security.** Mutating operations (`POST`, `:decide`, `:amend`, `:withdraw`) "
            "require `agents:write`; read operations require `agents:read`."
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
            "list with `?trace_id={trace_id}`."
        ),
    },
    {
        "name": "Jobs",
        "description": (
            "In-flight async-job tracker for both Registry import jobs and Broker "
            "async-execution jobs — one uniform `Job` shape keyed by `kind`. Lifecycle only — "
            "type-specific result payloads (the imported revisions for `import`, upstream "
            "response body for `execution`) live under `/jobs/{job_id}/result`. Result "
            "availability follows the organisation-level retention policy (one-shot or TTL); "
            "once that expires, both `/jobs/{job_id}` and `/jobs/{job_id}/result` `404`."
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
            "filter."
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
            "subsequent `POST /auth/login`. **Existing JWTs keep working until they expire** "
            "(≤ 1 hour with the platform's default TTL). This is a deliberate trade-off; "
            "sub-minute revocation would require a server-side session table."
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
            "The same vocabulary is used for `User.permissions` and for `Toolkit.permissions` "
            "— coarse JWT-embedded scopes — so the one catalogue covers both. Per-binding "
            "fine-grained `PermissionRule[]` (the inner PBAC tier) lives separately under the "
            "`Toolkit Permissions` tag."
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
            "Identity introspection for the calling principal (human, agent, or service "
            "account) — `GET /me` returns the resolved subject, scopes, and permissions behind "
            "the presented token."
        ),
    },
    {
        "name": "Agents",
        "description": (
            "Agent actors — autonomous principals that call the Broker. Covers the agent "
            "lifecycle (list, read, approve / deny, enable / disable, archive) and the "
            "toolkits bound to each agent."
        ),
    },
    {
        "name": "Service Accounts",
        "description": (
            "Machine principals for non-interactive integrations — lifecycle (create, list, "
            "read, approve / deny, enable / disable, archive) mirroring the agent surface."
        ),
    },
    {
        "name": "OAuth",
        "description": (
            "OAuth 2.0 / OIDC endpoints exposed by the platform authorization server — the "
            "authorize, token, introspection, revocation, and assertion-mint endpoints plus "
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
            "(users, agents, service accounts). Returns ID-to-name mappings for UI cache "
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
]

# Redoc tag groups (vendor extension). Tags not listed here still render; this
# only controls the left-nav grouping.
X_TAG_GROUPS: list[dict[str, Any]] = [
    {
        "name": "Core / Access",
        "tags": [
            "Credentials",
            "Toolkits",
            "Toolkit Keys",
            "Toolkit Credentials",
            "Toolkit Permissions",
            "Access Requests",
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
            "Jobs",
            "Events",
            "Audit",
            "Monitoring",
            "Configuration",
        ],
    },
    {
        "name": "Platform Actors",
        "tags": [
            "Identity",
            "Agents",
            "Service Accounts",
            "OAuth",
            "Agent Registration",
            "Discovery",
        ],
    },
    {
        "name": "Operations",
        "tags": ["System"],
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
            "- **Service accounts** — `grant_type=client_credentials` with "
            "`client_id` + `client_secret`.\n"
            "- **Users** — `grant_type=authorization_code` (interactive) or "
            "`grant_type=password`; refresh either with `grant_type=refresh_token`.\n\n"
            "Service accounts can also mint short-lived, scope-narrowed task tokens "
            "for agents via `POST /oauth/mint`.\n\n"
            "Per-endpoint scope and actor-type requirements are not modelled in this "
            "document (OpenAPI cannot faithfully express the OR-of-scopes / "
            "`org:admin` bypass / service-layer enforcement); see "
            "`GET /reference/endpoints.json` for the authoritative authorization "
            "reference."
        ),
    }
}

# Operations that are intentionally unauthenticated and must NOT inherit the
# global BearerAuth requirement.
PUBLIC_OPERATION_IDS: frozenset[str] = frozenset(
    {
        # Health probes (root + per-surface) are dependency-free liveness checks.
        "getHealth",
        # Backend-identity probe: unauthenticated, self-describing, no secrets.
        "getInstance",
        "health",
        "controlHealth",
        "adminHealth",
        "registryHealth",
        "authHealth",
        # Unauthenticated auth/onboarding flows.
        "login",
        "redeemInvite",
        # First-run setup: create the first admin. Self-closes (410) once any
        # user exists, so it is safe to leave unauthenticated.
        "createAdmin",
        # Token + authorize + dynamic-registration endpoints: the caller has no
        # platform token yet (you call these to *get* one / to bootstrap a client).
        "tokenEndpoint",
        "authorizeEndpoint",
        "registerEndpoint",
        # OAuth redirect callbacks (bound by a signed state param, not a session).
        "oauthCallback",
        "authorizeOauthCallback",
        # Browser-facing OAuth error page (no auth; just renders an error code).
        "errorPage",
        # Unauthenticated discovery metadata.
        "jwks",
        "oauthAuthorizationServer",
    }
)


#: Operations that **do** authenticate, but not via the global ``BearerAuth``
#: platform token, so they must not advertise that security requirement. Unlike
#: :data:`PUBLIC_OPERATION_IDS` these are *not* public: they still reject bad
#: credentials with ``401``, so the documented error responses are kept intact.
#: Their ``security`` is dropped to ``[]`` (no platform bearer requirement); the
#: real credential is described in the endpoint reference's ``auth_note`` (see
#: ``NON_IDENTITY_AUTH`` in ``endpoint_scopes.py``).
NON_BEARER_AUTH_OPERATION_IDS: frozenset[str] = frozenset(
    {
        # RFC 7592 registration-status poll: authenticated by the
        # Registration-Access-Token issued at registration, not a platform bearer
        # token. It still returns 401 on a missing/invalid/expired RAT
        # (RegistrationAccessDeniedError -> 401), so the 401 response stays.
        "pollStatusEndpoint",
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
    (re.compile(r"^/admin/config"), "Configuration"),
    (re.compile(r"^/credentials"), "Credentials"),
    (re.compile(r"^/toolkits/[^/]+/keys"), "Toolkit Keys"),
    (re.compile(r"^/toolkits/[^/]+/credentials/[^/]+/permissions"), "Toolkit Permissions"),
    (re.compile(r"^/toolkits/[^/]+/credentials"), "Toolkit Credentials"),
    (re.compile(r"^/toolkits"), "Toolkits"),
    (re.compile(r"^/access-requests"), "Access Requests"),
    (re.compile(r"^/apis/.+/overlays"), "Overlays"),
    (re.compile(r"^/apis/.+/operations$"), "API Operations"),
    (re.compile(r"^/apis/.+/openapi$"), "API Spec"),
    (re.compile(r"^/apis"), "APIs"),
    # Catalog (Discover) — browse/preview/import + the :refresh verb. Broad
    # ^/catalog is safe: no other surface shares the prefix.
    (re.compile(r"^/catalog"), "Catalog"),
    (re.compile(r"^/search"), "Search"),
    (re.compile(r"^/inspect"), "Inspect"),
    (re.compile(r"^/lookup"), "Lookup"),
    (re.compile(r"^/notes"), "Notes"),
    (re.compile(r"^/monitoring"), "Monitoring"),
    (re.compile(r"^/executions"), "Executions"),
    (re.compile(r"^/jobs"), "Jobs"),
    (re.compile(r"^/events"), "Events"),
    (re.compile(r"^/permissions"), "Permissions"),
    (re.compile(r"^/actors"), "Actors"),
    (re.compile(r"^/users"), "Users"),
    (re.compile(r"^/auth/login"), "Users"),
    (re.compile(r"^/audit"), "Audit"),
    # Platform-actor surfaces (superset, not in the original reference).
    (re.compile(r"^/agents"), "Agents"),
    (re.compile(r"^/service-accounts"), "Service Accounts"),
    (re.compile(r"^/oauth"), "OAuth"),
    (re.compile(r"^/authorize"), "OAuth"),
    (re.compile(r"^/error"), "OAuth"),
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


def fastapi_metadata_kwargs() -> dict[str, Any]:
    """Keyword arguments to spread into ``FastAPI(...)`` for document metadata."""
    return {
        "title": API_TITLE,
        "summary": API_SUMMARY,
        "description": API_DESCRIPTION,
        "version": API_VERSION,
        "servers": SERVERS,
        "contact": CONTACT,
        "license_info": LICENSE_INFO,
        "openapi_tags": OPENAPI_TAGS,
        "generate_unique_id_function": generate_operation_id,
    }


def _normalise_error_responses(responses: dict[str, Any]) -> None:
    """Render error responses as ``application/problem+json`` with examples.

    FastAPI emits error bodies (``model=ProblemDetail``) under
    ``application/json``. This rewrites each 4xx/5xx response to the RFC 9457
    media type and attaches the canonical per-status example, matching the
    platform error contract.
    """
    for code, response in responses.items():
        if not isinstance(response, dict) or code not in STATUS_EXAMPLES:
            continue
        content = response.get("content")
        if not isinstance(content, dict):
            continue
        media = content.pop("application/json", None)
        if media is None:
            media = content.get(PROBLEM_JSON, {})
        if not isinstance(media, dict):
            media = {}
        media["example"] = STATUS_EXAMPLES[code]
        content[PROBLEM_JSON] = media


def _stamp_scope_metadata(
    method: str,
    path: str,
    operation: dict[str, Any],
    operation_auth: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Stamp the operation's ``security`` from its recovered identity dependency.

    The OpenAPI document models only the real authentication mechanism —
    ``BearerAuth`` (an opaque bearer token, not a JWT). The per-operation scope/actor-type join
    is *not* expressed here: OpenAPI's ``security`` model cannot faithfully carry
    our OR-of-scopes semantics, the ``org:admin`` superuser bypass, the
    typical-caller hint, or the fact that many scopes are enforced in the service
    layer rather than at the gateway. Encoding it as a fabricated OAuth2 flow
    would misrepresent enforcement, so that richer authorization reference lives
    in the endpoint reference (:mod:`jentic_one.shared.web.endpoint_reference`,
    served at ``GET /reference/endpoints.json``), which the CLI and docs SPA
    consume.

    Always sets an explicit per-operation ``security`` so downstream consumers
    never fall back to the global document-level requirement to decide whether an
    operation authenticates: an operation with no recovered identity dependency is
    stamped ``security: []`` (public); an authenticated one is stamped
    ``[{"BearerAuth": []}]``. This keeps the public/authenticated boundary derived
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
    operation["security"] = [{"BearerAuth": []}]


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

        schema["security"] = [{"BearerAuth": []}]
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
                    # credential. It never reaches the scope/authz layer, so the
                    # 403 (which only the permission gate raises) is dropped.
                    operation["security"] = []
                    operation.get("responses", {}).pop("403", None)
                else:
                    _stamp_scope_metadata(method, path, operation, operation_auth)
                _normalise_error_responses(operation.get("responses", {}))

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
