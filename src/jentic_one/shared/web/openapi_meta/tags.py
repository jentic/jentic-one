"""The OpenAPI tag catalogue and the Redoc tag groups.

:data:`OPENAPI_TAGS` and :data:`X_TAG_GROUPS` are the tag documentation;
:func:`jentic_one.shared.web.openapi_meta.operation_ids.resolve_tag` assigns
each operation one of these tags when the spec is generated.
"""

# This module is verbatim API documentation prose; the long-line limit is
# intentionally disabled for the metadata strings.
# ruff: noqa: E501

from __future__ import annotations

from typing import Any

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
