"""
Pydantic request / response models for Jentic Mini.
Input models (Create/Patch/Register) and all response models are here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.validators import NormModel


# ── Credentials (input) ───────────────────────────────────────────────────────


class CredentialCreate(NormModel):
    label: str
    value: str = ""
    """Plain-text secret; encrypted before storage. Always the primary credential — token, password, API key.
    May be empty string for no-auth APIs where the credential exists only to carry server_variables."""
    identity: str | None = None
    """Optional identity field — username, client ID, account SID etc.
    Required for http/basic and http/digest schemes (username + password).
    For compound apiKey schemes (overlay uses canonical 'Secret'/'Identity' names), the
    Identity scheme header is injected from this field.
    Leave null for Bearer tokens, single-value API keys, and GitHub PAT-style BasicAuth."""
    api_id: str | None = None
    """API this credential belongs to (e.g. 'techpreneurs.ie'). Required for broker injection."""
    server_variables: dict[str, str] | None = None
    """Resolved values for OpenAPI server URL template variables.

    Required for self-hosted and multi-tenant APIs whose server URL contains
    template variables (e.g. ``{host}``, ``{tenant}``, ``{port}``).
    The broker substitutes these values at routing time before forwarding requests.

    Example for Discourse at ``https://{host}``:
        ``{"host": "forum.acme.com"}``

    Example for a multi-tenant SaaS at ``https://{tenant}.example.com/api``:
        ``{"tenant": "acme"}``

    Omit (or set ``null``) for public SaaS APIs whose server URL is fixed.
    Use ``GET /apis/{api_id}`` to see which variables the spec declares.
    """
    auth_type: Literal["bearer", "basic", "apiKey", "none"] | None = Field(
        default=None,
        examples=["bearer"],
        description=(
            "How this credential maps to the upstream API's authentication scheme. "
            "The broker uses this to find the right security scheme in the spec — "
            "it resolves by type, not by the bespoke scheme name in the overlay.\n\n"
            "| Value | Injects as | When to use |\n"
            "|---|---|---|\n"
            "| `bearer` | `Authorization: Bearer {value}` | REST APIs, OAuth access tokens, JWTs. GitHub REST API, Deepgram, Slack, etc. |\n"
            "| `basic` | `Authorization: Basic base64({identity??'token'}:{value})` | HTTP Basic auth, git-over-HTTPS. Set `identity` to the username; omit for GitHub PATs (any username accepted). |\n"
            "| `apiKey` | Custom header or query param `= {value}` | API key in a named header (X-API-Key, Api-Key, X-Auth-Key, etc.). For **compound** schemes (e.g. Discourse Api-Key + Api-Username) where the overlay uses canonical `Secret`/`Identity` scheme names, set `identity` to the username/account — a single credential covers both headers. |\n"
            "| `none` | *(nothing injected)* | No-auth APIs where the credential exists only to carry `server_variables` for routing. |"
        ),
    )
    scheme: dict | None = Field(
        default=None,
        description=(
            "Self-describing injection rule. When set, the broker injects the credential "
            "directly from this blob without looking up the API spec or overlay at runtime. "
            'Format: {"in": "header", "name": "Authorization", "prefix": "Bearer "} '
            'or {"in": "header", "name": "X-Api-Key"}. '
            'Supports encode=base64 for Basic auth: {"in": "header", "name": "Authorization", "prefix": "Basic ", "encode": "base64"}. '
            'For compound schemes: {"secret": {"in": "header", ...}, "identity": {"in": "header", ...}}.'
        ),
    )
    routes: list[str] | None = Field(
        default=None,
        description=(
            "Hostnames or host+path patterns this credential should be injected into. "
            "Each entry is stored as (host, path_prefix) in credential_routes. "
            'Example: ["github.com", "api.github.com"]. '
        ),
    )


class CredentialPatch(NormModel):
    label: str | None = None
    value: str | None = None
    identity: str | None = None
    """Update the identity (username / client ID) for this credential."""
    api_id: str | None = None
    auth_type: Literal["bearer", "basic", "apiKey", "none"] | None = Field(
        default=None,
        description="Update the auth type for this credential. See `POST /credentials` for valid values and semantics.",
    )
    server_variables: dict[str, str] | None = None
    """Update the resolved server variable values for this credential."""
    scheme: dict | None = Field(
        default=None,
        description="Update the self-describing injection rule. See POST /credentials for format.",
    )
    routes: list[str] | None = Field(
        default=None,
        description="Update the host+path routing patterns for this credential.",
    )


# ── Pagination wrapper ────────────────────────────────────────────────────────


class Page(BaseModel):
    """Generic paginated envelope."""

    page: int = Field(examples=[1], description="Current page number (1-indexed)")
    limit: int = Field(examples=[50], description="Results per page")
    total: int = Field(examples=[247], description="Total number of items matching query")
    total_pages: int = Field(examples=[5], description="Total pages available")
    has_more: bool = Field(
        examples=[True], description="True if more pages exist after current page"
    )
    model_config = {"extra": "allow"}


# ── APIs (output) ─────────────────────────────────────────────────────────────


class ApiOut(BaseModel):
    """API provider metadata including ID, name, vendor, and base URL."""

    id: str = Field(examples=["api.github.com"], description="API ID (typically the base domain)")
    name: str | None = Field(
        default=None,
        examples=["GitHub REST API"],
        description="Human-readable API name from spec info.title",
    )
    vendor: str | None = Field(
        default=None, examples=["GitHub"], description="API vendor or maintainer organization"
    )
    description: str | None = Field(
        default=None,
        examples=["GitHub's REST API for managing repositories, issues, and pull requests"],
        description="API description from spec info.description",
    )
    base_url: str | None = Field(
        default=None,
        examples=["https://api.github.com"],
        description="Primary base URL from spec servers array",
    )
    created_at: float | None = Field(
        default=None, examples=[1672531200.0], description="Unix timestamp when API was imported"
    )
    model_config = {"extra": "allow"}


class OperationOut(BaseModel):
    """A single API operation. id encodes method/host/path (capability ID format)."""

    id: str = Field(
        examples=["GET/api.github.com/repos/{owner}/{repo}/issues"],
        description="Capability ID in METHOD/host/path format",
    )
    summary: str | None = Field(
        default=None,
        examples=["List repository issues"],
        description="Short description of what this operation does",
    )
    description: str | None = Field(
        default=None,
        examples=[
            "List issues in a repository. Only issues assigned to the authenticated user are returned."
        ],
        description="Detailed description from the OpenAPI spec",
    )
    model_config = {"extra": "allow"}


class ApiListPage(Page):
    """Paginated list of API providers registered in the catalog."""

    data: list[ApiOut] = Field(description="Array of API records for this page")


class OperationListPage(Page):
    """Paginated list of API operations with method, path, and summary."""

    data: list[OperationOut] = Field(description="Array of operation records for this page")


# ── Search (output) ───────────────────────────────────────────────────────────


class SearchResult(BaseModel):
    """A search result from the BM25 index — either an operation or workflow capability.

    The BM25 search index covers both API operations (parsed from OpenAPI specs) and
    workflows (parsed from Arazzo documents). Results are ranked by relevance score.
    Use GET /inspect/{id} to get the full schema for a result before calling it.
    """

    type: str = Field(
        examples=["operation"],
        description="Result type: 'operation' for API endpoints, 'workflow' for multi-step Arazzo workflows",
    )
    id: str = Field(
        examples=["GET/api.github.com/repos/{owner}/{repo}/issues"],
        description="Capability ID in METHOD/host/path format",
    )
    slug: str | None = Field(
        default=None,
        examples=["github-list-issues"],
        description="Workflow slug (workflows only) — used as path segment in POST /workflows/{slug}",
    )
    summary: str | None = Field(
        default=None,
        examples=["List repository issues"],
        description="Short description of what this capability does",
    )
    description: str | None = Field(
        default=None,
        examples=["List issues in a repository"],
        description="Detailed description from the OpenAPI operation or Arazzo workflow",
    )
    score: float = Field(
        examples=[0.85],
        description="BM25 relevance score (0.0-1.0) — higher is more relevant to the search query",
    )
    involved_apis: list[str] = Field(
        default_factory=list,
        examples=[["api.github.com"]],
        description="List of upstream API hosts involved in this capability (for workflows, may list multiple)",
    )
    matched_on: list[str] | None = Field(
        default=None,
        examples=[["operation_summary"]],
        description=(
            "Which fields the query matched against — at least one of "
            "`name`, `operation_summary`, `description`, `tag`. Computed via "
            "cheap substring checks post-rank; intentionally pragmatic rather "
            "than reflecting BM25's internal matched fields."
        ),
    )
    match_snippet: str | None = Field(
        default=None,
        examples=["…create a \u0001payment\u0001 intent on the Stripe…"],
        description=(
            "Short text snippet (~80 chars) around the matched substring from "
            "the highest-priority field that matched (priority order: "
            "`name > operation_summary > description > tag`). The matched "
            "span is wrapped in `\\u0001` sentinel characters so the client "
            "can render its own highlight without an XSS-prone HTML payload. "
            "Null when the result was a BM25 hit without an exact substring "
            "match in any field."
        ),
    )
    model_config = {"extra": "allow"}


# ── Capability / inspect (output) ─────────────────────────────────────────────


class CapabilityOut(BaseModel):
    id: str = Field(
        examples=["GET/api.github.com/repos/{owner}/{repo}/issues"],
        description="Capability ID in METHOD/host/path format",
    )
    type: str | None = Field(
        default=None,
        examples=["operation"],
        description="Capability type: 'operation' for API endpoints, 'workflow' for multi-step Arazzo workflows",
    )
    summary: str | None = Field(
        default=None,
        examples=["List repository issues"],
        description="Short description of what this capability does",
    )
    description: str | None = Field(
        default=None,
        examples=[
            "List issues in a repository. Only issues assigned to the authenticated user are returned."
        ],
        description="Detailed description from the OpenAPI spec or Arazzo workflow",
    )
    method: str | None = Field(
        default=None, examples=["GET"], description="HTTP method (operations only)"
    )
    path: str | None = Field(
        default=None,
        examples=["/repos/{owner}/{repo}/issues"],
        description="URL path template (operations only)",
    )
    parameters: list[dict] | None = Field(
        default=None,
        examples=[[{"name": "owner", "in": "path", "required": True}]],
        description="Parameter schemas from OpenAPI spec (operations only)",
    )
    request_body: dict | None = Field(
        default=None,
        examples=[None],
        description="Request body schema from OpenAPI spec (operations only)",
    )
    responses: dict | None = Field(
        default=None,
        examples=[{"200": {"description": "Success"}}],
        description="Response schemas by status code from OpenAPI spec (operations only)",
    )
    security: list[dict] | None = Field(
        default=None,
        examples=[[{"bearer": []}]],
        description="Security requirement objects from OpenAPI spec (operations only)",
    )
    model_config = {"extra": "allow"}


# ── Credentials (output) ──────────────────────────────────────────────────────


class CredentialOut(BaseModel):
    """Upstream API credential metadata. Secret values are never returned after creation."""

    model_config = {"extra": "ignore"}
    id: str
    label: str
    identity: str | None = None
    """Identity field (username, client ID, etc.) — returned so clients can confirm what was stored."""
    api_id: str | None = None
    auth_type: str | None = None
    server_variables: dict[str, str] | None = None
    """Resolved server variable values stored with this credential."""
    scheme: dict | None = None
    """Self-describing injection rule — when set, broker injects directly without API spec lookup."""
    routes: list[str] | None = None
    """Host+path patterns this credential matches — stored in credential_routes table."""
    created_at: float | None = None
    updated_at: float | None = None
    account_id: str | None = None
    app_slug: str | None = None
    synced_at: float | None = None


# ── Toolkits (output) ─────────────────────────────────────────────────────────


class ToolkitKeyOut(BaseModel):
    """Toolkit API key metadata. The full key value is only returned at creation time."""

    id: str = Field(examples=["ck_a1b2c3d4"], description="Key ID (format: ck_{8chars})")
    name: str | None = Field(
        default=None,
        examples=["Production agent key"],
        description="User-assigned key name for identification",
    )
    prefix: str | None = Field(
        default=None, examples=["tk_"], description="Key prefix (always 'tk_' for toolkit keys)"
    )
    allowed_ips: list[str] | None = Field(
        default=None,
        examples=[["192.168.1.0/24"]],
        description="IP CIDR ranges allowed to use this key (null = no IP restriction)",
    )
    revoked: bool = Field(
        default=False,
        examples=[False],
        description="True if this key has been revoked and can no longer authenticate",
    )
    created_at: float | None = Field(
        default=None, examples=[1672531200.0], description="Unix timestamp when key was created"
    )
    model_config = {"extra": "allow"}


class ToolkitKeyCreated(ToolkitKeyOut):
    """Returned only at key creation — includes the full key value (never returned again)."""

    key: str = Field(
        examples=["tk_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"],
        description="Full API key value (format: tk_{32chars}) - only shown once at creation",
    )


class CredentialBindingOut(BaseModel):
    """Credential bound to a toolkit with access control rules. Includes label and API binding info."""

    credential_id: str = Field(
        examples=["cred_abc123xyz"], description="Credential ID (format: cred_{12chars})"
    )
    label: str | None = Field(
        default=None,
        examples=["GitHub PAT for jentic-mini"],
        description="User-assigned credential label",
    )
    api_id: str | None = Field(
        default=None, examples=["api.github.com"], description="API ID this credential is for"
    )
    auth_type: str | None = Field(
        default=None,
        examples=["bearer"],
        description="Auth scheme type: bearer, basic, apiKey, oauth2, etc",
    )
    model_config = {"extra": "allow"}


class ToolkitOut(BaseModel):
    """Toolkit configuration with scoped credentials and access control policies."""

    id: str = Field(examples=["default"], description="Toolkit ID")
    name: str = Field(examples=["Default Toolkit"], description="Human-readable toolkit name")
    description: str | None = Field(
        default=None,
        examples=["Default toolkit for general-purpose API access"],
        description="Optional description of this toolkit's purpose",
    )
    created_at: float | None = Field(
        default=None, examples=[1672531200.0], description="Unix timestamp when created"
    )
    disabled: bool = Field(
        default=False,
        examples=[False],
        description="If true, all API keys for this toolkit are revoked",
    )
    key_count: int | None = Field(
        default=None, examples=[3], description="Number of API keys issued for this toolkit"
    )
    credential_count: int | None = Field(
        default=None, examples=[5], description="Number of credentials bound to this toolkit"
    )
    keys: list[ToolkitKeyOut] = Field(
        default_factory=list, examples=[[]], description="API keys for this toolkit (if expanded)"
    )
    credentials: list[CredentialBindingOut] = Field(
        default_factory=list,
        examples=[[]],
        description="Credentials bound to this toolkit (if expanded)",
    )
    permissions: list[dict] = Field(
        default_factory=list,
        examples=[[{"effect": "allow", "methods": ["GET"]}]],
        description="Access control rules for this toolkit",
    )
    model_config = {"extra": "allow"}


# ── Permission rules ──────────────────────────────────────────────────────────


class PermissionRule(BaseModel):
    """A single access control rule. All fields are optional; conditions are AND-combined.
    First matching rule wins. Agent rules are evaluated before system safety rules.

    **`effect`** — `"allow"` or `"deny"` (required)

    **`methods`** — list of HTTP methods to match, e.g. `["GET", "POST"]`.
    Omit to match all methods.

    **`path`** — Python regex matched against the **path component only** of the upstream
    request URL. The host and query string are never included. Matching uses `re.search()`
    (Python), which means:

    - It is always a **regex** — not a glob, not a prefix string.
    - It is **case-insensitive**.
    - It is a **substring match by default** — the pattern can match anywhere in the path
      unless you anchor it with `^` and/or `$`.
    - `|` is regex OR (matches either side).

    **Anchoring guide:**

    | Intent | Pattern | Matches | Does NOT match |
    |--------|---------|---------|----------------|
    | Substring (any path containing word) | `"issues"` | `/repos/x/issues`, `/v1/issues/7` | (nothing — too broad for deny rules) |
    | Prefix (everything under a path) | `"^/repos/jentic/jentic-mini/"` | `/repos/jentic/jentic-mini/issues/34` | `/repos/other/repo/issues` |
    | Exact endpoint | `"^/v1/voices$"` | `/v1/voices` | `/v1/voices/123` |
    | One endpoint + subresources | `"^/repos/jentic/jentic-mini/issues/[0-9]+/comments$"` | `/repos/jentic/jentic-mini/issues/34/comments` | `/repos/jentic/jentic-mini/issues` |
    | Block any sensitive word | `"admin\\|billing\\|pay"` | `/v1/admin/users`, `/billing/invoice` | n/a |

    **Tip for agents generating rules:** always anchor with `^` to avoid unintentionally
    matching longer paths, and use `$` to prevent prefix over-permission. An unanchored
    pattern like `"comments"` would also match `/v1/my-comments-service/admin`.

    **`operations`** — list of regexes matched against the operation ID via `re.search()`.
    E.g. `["tts", "speech"]` matches any operation whose ID contains "tts" or "speech".

    System safety rules (always active, cannot be removed) are marked `_system: true` in
    `GET .../permissions` responses (see `PermissionRuleOut`). They deny sensitive paths
    and write methods by default. The `_system` and `_comment` fields are response-only
    and will be rejected in request bodies.

    **Examples:**
    ```json
    {"effect": "allow", "methods": ["POST"], "path": "^/v1/text-to-speech$"}
    {"effect": "allow", "methods": ["POST"], "path": "^/repos/jentic/jentic-mini/issues/[0-9]+/comments$"}
    {"effect": "allow", "methods": ["GET", "POST"], "path": "^/repos/jentic/jentic-mini/"}
    {"effect": "deny",  "path": "admin|billing|pay"}
    {"effect": "allow", "operations": ["^github_get_repo$"]}
    ```
    """

    effect: Literal["allow", "deny"] = Field(description='`"allow"` or `"deny"`')
    methods: list[str] | None = Field(
        default=None,
        description='HTTP methods to match, e.g. `["GET", "POST"]`. Omit to match all methods.',
    )
    path: str | None = Field(
        default=None,
        description=(
            "Python regex matched with `re.search()` against the **path component only** of the upstream URL "
            "(no host, no query string). Matching is case-insensitive and substring by default — "
            "use `^`/`$` to anchor. `|` is regex OR. "
            'Example: `"^/repos/jentic/jentic-mini/issues/[0-9]+/comments$"` matches only that exact endpoint; '
            "omitting anchors would also match any path containing that substring."
        ),
    )
    operations: list[str] | None = Field(
        default=None,
        description='List of regexes matched against the operation ID. E.g. `["tts", "speech"]`.',
    )
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {"effect": "allow", "methods": ["POST"], "path": "text-to-speech"},
                {"effect": "deny", "path": "admin|billing|pay"},
                {"effect": "allow", "operations": ["^github_get_repo$"]},
            ]
        },
    }


class PermissionRuleOut(PermissionRule):
    """Permission rule as returned by the API — includes read-only server fields."""

    system: bool | None = Field(
        default=None,
        alias="_system",
        description="True if this is a system safety rule (cannot be removed)",
    )
    comment: str | None = Field(
        default=None,
        alias="_comment",
        description="Human-readable explanation of this rule's purpose (system rules only)",
    )
    model_config = {
        "extra": "allow",
        "populate_by_name": True,
    }


class PermissionsPatch(BaseModel):
    """Body for PATCH .../permissions — incremental rule updates."""

    add: list[PermissionRule] = Field(
        default_factory=list, description="Rules to append (deduplicated by exact match)"
    )
    remove: list[PermissionRule] = Field(
        default_factory=list, description="Rules to remove by exact match"
    )


# ── Access requests (output) ──────────────────────────────────────────────────


class AccessRequestOut(BaseModel):
    """An access request filed by an agent and awaiting human approval.

    The `payload` shape depends on `type`:

    **`grant`** — bind a new upstream credential to this toolkit (optionally with rules):
    ```json
    { "type": "grant", "payload": { "credential_id": "api.github.com", "rules": [...] }, "reason": "..." }
    ```

    **`modify_permissions`** — update permission rules on an already-bound credential:
    ```json
    { "type": "modify_permissions", "payload": { "credential_id": "api.github.com", "rules": [...] }, "reason": "..." }
    ```
    """

    id: str = Field(examples=["areq_abc123xyz"], description="Unique request ID (areq_xxxxxxxx)")
    toolkit_id: str = Field(examples=["default"], description="The toolkit this request belongs to")
    type: Literal["grant", "modify_permissions", "add_scope"] = Field(
        examples=["grant"],
        description=(
            "`grant` — bind a new upstream API credential to this toolkit (and optionally set permission rules). "
            "`modify_permissions` — update the permission rules on a credential already bound to this toolkit. "
            "`add_scope` — legacy alias for `grant` (deprecated)."
        ),
    )
    payload: dict = Field(
        default_factory=dict,
        examples=[
            {"credential_id": "api.github.com", "rules": [{"effect": "allow", "methods": ["GET"]}]}
        ],
        description=(
            "Request-type-specific data. "
            "For `grant`: `{credential_id, rules?, api_id?}`. "
            "For `modify_permissions`: `{credential_id, rules}`."
        ),
    )
    status: Literal["pending", "approved", "denied"] = Field(
        examples=["pending"],
        description="Current approval state. Poll until `approved` or `denied`.",
    )
    reason: str | None = Field(
        default=None,
        examples=["Need GitHub API access to list repository issues"],
        description="Human-readable explanation from the agent",
    )
    description: str | None = Field(
        default=None,
        examples=["Grant access to api.github.com with GET permissions"],
        description="Auto-generated summary of what the agent is requesting",
    )
    approve_url: str | None = Field(
        default=None,
        examples=["http://localhost:8900/approve/areq_abc123xyz"],
        description="URL for the human to review and approve/deny",
    )
    created_at: float | None = Field(
        default=None, examples=[1672531200.0], description="Unix timestamp when filed"
    )
    resolved_at: float | None = Field(
        default=None, examples=[1672531500.0], description="Unix timestamp when approved or denied"
    )
    applied_effects: list[str] | None = Field(
        default=None,
        examples=[["Bound credential api.github.com to toolkit default"]],
        description="Side-effects applied on approval (credential bound, rules set, etc.)",
    )
    model_config = {"extra": "allow"}


# ── Jobs (output) ─────────────────────────────────────────────────────────────


class JobOut(BaseModel):
    """Async job handle for operations that couldn't complete synchronously. Poll for status and result."""

    id: str = Field(examples=["job_abc123xyz"], description="Job ID (format: job_{12chars})")
    kind: str | None = Field(
        default=None, examples=["workflow"], description="Job type: 'workflow' or 'broker'"
    )
    slug_or_id: str | None = Field(
        default=None, examples=["github-create-issue"], description="Workflow slug or capability ID"
    )
    toolkit_id: str | None = Field(
        default=None, examples=["default"], description="Toolkit that initiated this job"
    )
    status: str = Field(
        examples=["completed"],
        description="Job status: pending, running, complete, failed, or upstream_async",
    )
    result: Any = Field(
        default=None,
        examples=[{"issue_number": 42, "url": "https://github.com/jentic/jentic-mini/issues/42"}],
        description="Job result (only present when status is complete or upstream_async)",
    )
    error: str | None = Field(
        default=None,
        examples=[None],
        description="Error message (only present when status is failed)",
    )
    http_status: int | None = Field(
        default=None, examples=[201], description="HTTP status code from upstream API"
    )
    upstream_async: bool = Field(
        default=False,
        examples=[False],
        description="True if upstream API itself returned 202 (async)",
    )
    upstream_job_url: str | None = Field(
        default=None,
        examples=[None],
        description="Upstream job polling URL (when upstream_async is true)",
    )
    trace_id: str | None = Field(
        default=None, examples=["trace_xyz789"], description="Execution trace ID for this job"
    )
    created_at: float | None = Field(
        default=None, examples=[1672531200.0], description="Unix timestamp when job was created"
    )
    completed_at: float | None = Field(
        default=None, examples=[1672531205.0], description="Unix timestamp when job finished"
    )
    model_config = {"extra": "allow"}


class JobListPage(Page):
    """Paginated list of async job handles with status, capability, and timing info."""

    data: list[JobOut] = Field(description="Array of job records for this page")


# ── Traces (output) ───────────────────────────────────────────────────────────


class TraceStepOut(BaseModel):
    """Individual workflow step execution details including inputs, outputs, and timing."""

    id: str | None = Field(default=None, examples=["step_1"], description="Internal step record ID")
    step_id: str | None = Field(
        default=None, examples=["getRepo"], description="Step identifier from the Arazzo workflow"
    )
    operation: str | None = Field(
        default=None,
        examples=["GET/api.github.com/repos/{owner}/{repo}"],
        description="Operation capability ID executed in this step",
    )
    status: str | None = Field(
        default=None, examples=["success"], description="Step status: success or failed"
    )
    http_status: int | None = Field(
        default=None, examples=[200], description="HTTP status code from this step's API call"
    )
    output: Any = Field(
        default=None,
        examples=[{"name": "jentic-mini", "stars": 42}],
        description="Step output data",
    )
    detail: Any = Field(
        default=None, examples=[None], description="Additional step metadata or runner context"
    )
    error: str | None = Field(
        default=None, examples=[None], description="Error message if step failed"
    )
    started_at: float | None = Field(
        default=None, examples=[1672531200.0], description="Unix timestamp when step started"
    )
    completed_at: float | None = Field(
        default=None, examples=[1672531201.0], description="Unix timestamp when step completed"
    )
    model_config = {"extra": "allow"}


class TraceOut(BaseModel):
    """Complete execution trace with status, timing, and step-by-step results for debugging."""

    id: str = Field(examples=["trace_abc123xyz"], description="Trace ID (format: exec_{12chars})")
    toolkit_id: str | None = Field(
        default=None, examples=["default"], description="Toolkit that executed this capability"
    )
    agent_id: str | None = Field(
        default=None,
        examples=["agnt_abc123"],
        description="Agent client_id when the call used an agent access token (at_…)",
    )
    operation_id: str | None = Field(
        default=None,
        examples=["GET/api.github.com/repos/{owner}/{repo}"],
        description="Operation capability ID (for single API calls)",
    )
    workflow_id: str | None = Field(
        default=None,
        examples=[None],
        description="Workflow capability ID (for multi-step workflows)",
    )
    spec_path: str | None = Field(
        default=None,
        examples=["api.github.com/openapi.json"],
        description="Path to the OpenAPI spec or Arazzo workflow file",
    )
    status: str = Field(
        examples=["success"], description="Execution status: success, failed, or pending"
    )
    http_status: int | None = Field(
        default=None, examples=[200], description="Final HTTP status code from upstream"
    )
    duration_ms: int | None = Field(
        default=None, examples=[1234], description="Total execution duration in milliseconds"
    )
    error: str | None = Field(
        default=None, examples=[None], description="Error message if execution failed"
    )
    created_at: float | None = Field(
        default=None, examples=[1672531200.0], description="Unix timestamp when execution started"
    )
    completed_at: float | None = Field(
        default=None, examples=[1672531201.0], description="Unix timestamp when execution completed"
    )
    steps: list[TraceStepOut] = Field(
        default_factory=list,
        examples=[[]],
        description="Step-by-step execution log (for workflows)",
    )
    model_config = {"extra": "allow"}


class TraceListPage(BaseModel):
    """Paginated list of execution traces for auditing recent broker and workflow calls."""

    total: int = Field(examples=[247], description="Total number of traces matching the query")
    limit: int = Field(examples=[50], description="Maximum traces returned in this response")
    offset: int = Field(examples=[0], description="Starting offset for pagination (0-indexed)")
    traces: list[TraceOut] = Field(
        examples=[[]], description="Array of trace records for this page"
    )


# ── Workflows (output) ────────────────────────────────────────────────────────


class WorkflowStepOut(BaseModel):
    id: str | None = Field(
        default=None,
        examples=["getRepo"],
        description="Step ID from the Arazzo workflow definition",
    )
    operation: str | None = Field(
        default=None,
        examples=["GET/api.github.com/repos/{owner}/{repo}"],
        description="Capability ID of the operation called by this step",
    )
    description: str | None = Field(
        default=None,
        examples=["Fetch repository metadata"],
        description="Step description from the Arazzo workflow",
    )
    model_config = {"extra": "allow"}


class WorkflowOut(BaseModel):
    """Multi-step workflow parsed from an Arazzo document.

    Workflows compose multiple API operations into reusable sequences. Each workflow
    is registered in the catalog with a slug and can be executed via POST /workflows/{slug}.
    The workflow runner automatically routes all HTTP calls through the broker for
    credential injection, tracing, and policy enforcement.
    """

    id: str = Field(
        examples=["POST/localhost:8900/workflows/github-create-issue"],
        description="Capability ID in format POST/{host}/workflows/{slug}",
    )
    url: str | None = Field(
        default=None,
        examples=["http://localhost:8900/workflows/github-create-issue"],
        description="Absolute URL to execute this workflow via POST",
    )
    slug: str = Field(
        examples=["github-create-issue"],
        description="URL-safe workflow identifier used in /workflows/{slug} endpoints",
    )
    name: str | None = Field(
        default=None,
        examples=["Create GitHub Issue"],
        description="Human-readable workflow name from Arazzo info.title or workflow.summary",
    )
    description: str | None = Field(
        default=None,
        examples=["Create a new issue in a GitHub repository"],
        description="Workflow description from Arazzo info.description or workflow.description",
    )
    steps_count: int = Field(
        default=0, examples=[3], description="Number of steps in this workflow"
    )
    involved_apis: list[str] = Field(
        default_factory=list,
        examples=[["api.github.com"]],
        description="List of upstream API hosts called by this workflow's steps",
    )
    created_at: float | None = Field(
        default=None,
        examples=[1672531200.0],
        description="Unix timestamp when this workflow was imported",
    )
    model_config = {"extra": "allow"}


class WorkflowDetail(WorkflowOut):
    steps: list[WorkflowStepOut] = Field(
        default_factory=list,
        examples=[[]],
        description="Sequence of workflow steps from the Arazzo definition",
    )
    input_schema: dict | None = Field(
        default=None,
        examples=[{"type": "object", "properties": {"title": {"type": "string"}}}],
        description="JSON Schema for workflow input parameters (from Arazzo workflow.inputs)",
    )


# ── Import (output) ───────────────────────────────────────────────────────────


class ImportOut(BaseModel):
    """Result of importing an OpenAPI spec or Arazzo workflow into the catalog.

    The import endpoint (POST /import) accepts specs from URLs, local file paths, or
    inline content. It parses the document, indexes operations/workflows in BM25,
    and stores metadata for broker execution. Returns the registered ID and count
    of indexed operations.
    """

    status: str = Field(
        examples=["imported"],
        description="Import status: 'ok' if all sources succeeded, 'partial' if some failed, 'failed' if all failed",
    )
    id: str | None = Field(
        default=None,
        examples=["api.github.com"],
        description="Registered API ID (for OpenAPI specs) or workflow slug (for Arazzo)",
    )
    name: str | None = Field(
        default=None,
        examples=["GitHub REST API"],
        description="Display name extracted from spec (info.title for APIs, workflow.summary for workflows)",
    )
    operations_indexed: int | None = Field(
        default=None,
        examples=[247],
        description="Number of operations parsed and indexed in BM25 (OpenAPI specs only)",
    )
    type: str | None = Field(
        default=None,
        examples=["api"],
        description="Import type: 'api' for OpenAPI specs, 'workflow' for Arazzo documents",
    )
    model_config = {"extra": "allow"}


# ── Default API key (output) ──────────────────────────────────────────────────


class DefaultKeyOut(BaseModel):
    key: str = Field(
        examples=["tk_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"],
        description="Generated API key for the default toolkit (format: tk_{32chars})",
    )
    toolkit_id: str = Field(examples=["default"], description="Toolkit ID this key is bound to")
    setup_url: str | None = Field(
        default=None,
        examples=["http://localhost:8900/setup"],
        description="URL for first-time setup wizard (if applicable)",
    )
    message: str | None = Field(
        default=None,
        examples=["First-time setup key generated. Save this key securely."],
        description="Human-readable message about key generation",
    )
    model_config = {"extra": "allow"}


# ── User / session (input/output) ────────────────────────────────────────────


class TokenRequest(BaseModel):
    """OAuth2 password grant request body for POST /user/token."""

    grant_type: str = Field(
        default="password",
        examples=["password"],
        description="OAuth2 grant type (only 'password' supported)",
    )
    username: str = Field(examples=["admin"], description="User account username")
    password: str = Field(examples=["changeme"], description="User account password")
    scope: str = Field(
        default="", examples=[""], description="OAuth2 scopes (unused, present for spec compliance)"
    )


class UserOut(BaseModel):
    """Current session status including authentication method and context (human session, agent key, or trusted subnet)."""

    logged_in: bool = Field(
        default=False, examples=[True], description="True if valid session exists"
    )
    username: str | None = Field(
        default=None,
        examples=["admin"],
        description="Username of authenticated user, null if not logged in",
    )
    is_admin: bool = Field(
        default=False, examples=[True], description="True if user has admin privileges"
    )
    toolkit_id: str | None = Field(
        default=None,
        examples=["default"],
        description="Associated toolkit ID for this user, null if admin",
    )
    trusted_subnet: bool = Field(
        default=False,
        examples=[True],
        description="True if request originated from trusted subnet (127.0.0.0/8 or 10.0.0.0/8)",
    )
    model_config = {"extra": "allow"}
