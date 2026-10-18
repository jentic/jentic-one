"""Request/response schemas for the catalog (Discover) endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.web import openapi_examples as ex

# Example-only values shared by several fields below.
_EX_ENTRY_ID = ex.CATALOG_ENTRY_ID
_EX_ENTRY_URL = f"{ex.BASE_URL}/catalog/{_EX_ENTRY_ID}"
_EX_HUMAN_URL = (
    f"https://github.com/jentic/jentic-public-apis/tree/main/apis/openapi/{ex.CATALOG_API_ID}"
)


class CatalogSnoozeRequest(BaseModel):
    """Body for ``POST /catalog/{entry_id}:snooze``.

    ``snoozed_until`` is optional: omit or send ``null`` to mute-until-newer (the primary
    per-API affordance — the badge re-lights only when a *newer* upstream digest lands);
    provide an ISO-8601 timestamp for a time-boxed snooze that lapses at that instant.
    """

    model_config = ConfigDict(populate_by_name=True)

    snoozed_until: datetime | None = Field(
        default=None,
        description=(
            "Optional expiry for the snooze (ISO-8601). Null = mute until a newer upstream "
            "digest is observed."
        ),
        examples=["2026-02-01T00:00:00Z"],
    )


class CatalogEntryLinksResponse(BaseModel):
    """Hypermedia links for a catalog entry."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "self": "/catalog/public/stripe.com",
                    "operations": "/catalog/public/stripe.com/operations",
                    "import": "/catalog/public/stripe.com:import",
                    "human": _EX_HUMAN_URL,
                    "alternates": [],
                }
            ]
        },
    )

    self_link: str = Field(
        serialization_alias="self",
        description="Canonical URL of this catalog entry.",
        examples=[_EX_ENTRY_URL],
    )
    operations: str = Field(
        description="URL of the entry's operation preview.",
        examples=[f"{_EX_ENTRY_URL}/operations"],
    )
    import_link: str = Field(
        serialization_alias="import",
        description="URL of the catalog import action (`POST /catalog/{entry_id}:import`).",
        examples=[f"{_EX_ENTRY_URL}:import"],
    )
    human: str | None = Field(
        default=None,
        description="Human-facing page for the entry at its source, when known.",
        examples=[_EX_HUMAN_URL],
    )
    alternates: list[str] = Field(
        default_factory=list,
        description=(
            "URLs of the visible entries with the same `catalog_api_id` collapsed into "
            "this one (empty when nothing was collapsed)."
        ),
        examples=[[f"{ex.BASE_URL}/catalog/internal/stripe.com"]],
    )


class CatalogEntryResponse(BaseModel):
    """A single browsable catalog entry."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "entry_id": "public/stripe.com",
                    "source_slug": "public",
                    "catalog_api_id": "stripe.com",
                    "vendor": "stripe.com",
                    "display_vendor": "stripe.com",
                    "title": "Stripe API",
                    "version": "2024-01-01",
                    "tags": [],
                    "human_url": _EX_HUMAN_URL,
                    "has_spec": True,
                    "registered": False,
                    "update_available": False,
                    "alternates": 0,
                    "_links": {
                        "self": "/catalog/public/stripe.com",
                        "operations": "/catalog/public/stripe.com/operations",
                        "import": "/catalog/public/stripe.com:import",
                        "human": _EX_HUMAN_URL,
                        "alternates": [],
                    },
                }
            ]
        },
    )

    entry_id: str = Field(
        description=(
            "Catalog entry identity, `{source_slug}/{external_id}`; the key every "
            "`/catalog/{entry_id}` route takes."
        ),
        examples=[_EX_ENTRY_ID],
    )
    source_slug: str = Field(
        description="Slug of the catalog source the entry comes from.", examples=["public"]
    )
    catalog_api_id: str = Field(
        description=(
            "Canonical, cross-source API identity (`domain[/sub-api]`, or "
            "`{source_slug}~{id}` when namespaced). Credentials match on it."
        ),
        examples=[ex.CATALOG_API_ID],
    )
    vendor: str | None = Field(
        description="Identity vendor derived from `catalog_api_id` (credentials match on it).",
        examples=[ex.API_VENDOR],
    )
    display_vendor: str | None = Field(
        default=None,
        description="Vendor for grouping entries in a UI; never used for identity.",
        examples=[ex.API_VENDOR],
    )
    title: str | None = Field(default=None, description="Entry title.", examples=["Stripe API"])
    version: str | None = Field(
        default=None,
        description="Entry version, when the source lists one.",
        examples=[ex.API_VERSION],
    )
    tags: list[str] = Field(
        default_factory=list, description="Entry tags from the source.", examples=[["payments"]]
    )
    human_url: str | None = Field(
        default=None,
        description="Human-facing page for the entry at its source, when known.",
        examples=[_EX_HUMAN_URL],
    )
    has_spec: bool = Field(
        default=True,
        description="Whether the entry has an importable spec (false for a listing-only card).",
        examples=[True],
    )
    registered: bool = Field(
        description="Whether this entry is already imported into the local registry.",
        examples=[True],
    )
    update_available: bool = Field(
        default=False,
        description=(
            "Whether this (registered) entry has an upstream spec update the local "
            "revision hasn't adopted yet. Always false for unregistered entries."
        ),
        examples=[False],
    )
    alternates: int = Field(
        default=0,
        description=(
            "How many visible entries with the same `catalog_api_id` are collapsed into "
            "this one (see `_links.alternates`)."
        ),
        examples=[0],
    )
    links: CatalogEntryLinksResponse = Field(serialization_alias="_links")


class CatalogListResponse(BaseModel):
    """List of catalog entries plus status fields for the Discover status row."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "data": [],
                    "catalog_total": 5231,
                    "registered_count": 12,
                    "outdated_count": 1,
                    "oldest_source_sync_age_seconds": 3600,
                    "has_more": True,
                    # base64 of {"id": "public/stripe.com"}
                    "next_cursor": (
                        "eyJpZCI6ICJwdWJsaWMvc3RyaXBlLmNvbSJ9"  # pragma: allowlist secret
                    ),
                }
            ]
        }
    )

    data: list[CatalogEntryResponse] = Field(description="The page of catalog entries.")
    catalog_total: int = Field(
        description="Entries visible to the caller after collapsing (pre-filter, pre-page).",
        examples=[5231],
    )
    registered_count: int = Field(
        description="Count of visible entries already imported locally (pre-filter).",
        examples=[12],
    )
    outdated_count: int = Field(
        default=0,
        description=(
            "Count of visible registered entries with an upstream update available (pre-filter)."
        ),
        examples=[1],
    )
    oldest_source_sync_age_seconds: int | None = Field(
        default=None,
        description=(
            "Seconds since the least recently synced visible source last synced, or null "
            "when none has synced."
        ),
        examples=[3600],
    )
    has_more: bool = Field(
        default=False, description="Whether another page follows.", examples=[True]
    )
    # Catalog keyset cursor: base64 ``{"id": <last entry_id>}``, not the ``{t, id}`` form.
    next_cursor: str | None = Field(
        default=None,
        description="Opaque keyset cursor for the next page (null when done).",
        examples=["eyJpZCI6ICJwdWJsaWMvc3RyaXBlLmNvbSJ9"],  # pragma: allowlist secret
    )


class CatalogSourceReadersResponse(BaseModel):
    """Who may read a ``restricted`` source (operator view only)."""

    permissions: list[str] = Field(
        description="Any one of these permissions grants read access.",
        examples=[["catalog:read:partner"]],
    )
    actor_ids: list[str] = Field(
        description="These actors may read the source.", examples=[["agent_abc123"]]
    )


class CatalogSourceWarningResponse(BaseModel):
    """An operator-facing problem with one source's mapping or rows."""

    code: str = Field(
        description=(
            "`mapping_unmatched`: a mapping row matches no curated id, so it is ignored; "
            "`ownership_conflict`: an entry claims a local API another source owns."
        ),
        examples=["mapping_unmatched"],
    )
    external_id: str = Field(description="The source's id for the entry.", examples=["stripe"])
    catalog_api_id: str = Field(
        description="The catalog API the warning concerns.", examples=[ex.CATALOG_API_ID]
    )
    local_api: str | None = Field(
        default=None,
        description="`vendor/name/version` of the local API, for an ownership conflict.",
        examples=["stripe.com/stripe/2024-06-20"],
    )


class CatalogSourceResponse(BaseModel):
    """One configured catalog source and its sync state.

    The operator fields (``enabled`` to ``last_error_code``) are present only
    for an ``org:admin`` caller.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "slug": "public",
                    "kind": "jentic-public-apis",
                    "visibility": "public",
                    "status": "ok",
                    "last_sync_at": "2026-01-15T10:30:00Z",
                    "live_entries": 5231,
                    "consecutive_failures": 0,
                    "sync_pending": False,
                }
            ]
        }
    )

    slug: str = Field(description="Source slug, the `entry_id` prefix.", examples=["public"])
    kind: str = Field(description="Source kind.", examples=["jentic-public-apis"])
    visibility: str = Field(
        description="`public` or `restricted` (readable only by its readers).",
        examples=["public"],
    )
    status: str = Field(description="Outcome of the last sync.", examples=["ok"])
    last_sync_at: datetime | None = Field(
        description="When the last successful sync finished.",
        examples=["2026-01-15T10:30:00Z"],
    )
    live_entries: int = Field(description="Entries the source currently lists.", examples=[5231])
    consecutive_failures: int = Field(
        description="Failed syncs since the last success.", examples=[0]
    )
    sync_pending: bool = Field(
        description="A sync is requested or wanted and has not started yet.", examples=[False]
    )
    enabled: bool | None = Field(
        default=None, description="Whether the source is synced (operator only).", examples=[True]
    )
    trust: str | None = Field(
        default=None, description="Source trust level (operator only).", examples=["curated"]
    )
    precedence: int | None = Field(
        default=None,
        description="Rank when sources list the same API; lower wins (operator only).",
        examples=[0],
    )
    readers: CatalogSourceReadersResponse | None = Field(
        default=None, description="Readers of a restricted source (operator only)."
    )
    warnings: list[CatalogSourceWarningResponse] | None = Field(
        default=None, description="Mapping and ownership problems (operator only)."
    )
    last_error_code: str | None = Field(
        default=None,
        description=(
            "While `status` is `failed`, why the last sync run failed (operator only), "
            "e.g. `listing_too_large`, `listing_timeout` or `source_unavailable`."
        ),
        examples=["listing_too_large"],
    )


class CatalogSourceListResponse(BaseModel):
    """The configured catalog sources the caller can see."""

    data: list[CatalogSourceResponse] = Field(description="Sources, ordered by slug.")


class CatalogSourceNotifyRequest(BaseModel):
    """Optional body for ``POST /catalog/sources/{slug}:notify``."""

    external_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
        description=(
            "Ids the upstream says changed (at most 100). A hint only: the sync still "
            "lists the whole source."
        ),
        examples=[["stripe"]],
    )


class CatalogSourceSyncResponse(BaseModel):
    """Acknowledgement of a requested sync; the sync runs in the background."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "slug": "public",
                    "status": "queued",
                    "forced": False,
                    "due_at": "2026-01-15T10:30:00Z",
                }
            ]
        }
    )

    slug: str = Field(description="Source slug.", examples=["public"])
    status: str = Field(
        description=(
            "`queued`: this call requested the sync; `coalesced`: it joined a sync already pending."
        ),
        examples=["queued"],
    )
    forced: bool = Field(description="The sync overrides the mass-removal guard.", examples=[False])
    due_at: datetime | None = Field(
        description=(
            "Earliest time the sync may start (a run in progress finishes first); null "
            "once the sync is already wanted."
        ),
        examples=["2026-01-15T10:30:00Z"],
    )


class PreviewParameterResponse(BaseModel):
    """A slimmed parameter in an operation preview."""

    # Its ``{name, in, ...}`` shape makes OpenAPI linters treat it as a
    # Parameter Object: a plural ``examples`` array is then misread as Example
    # Objects (``oas3-examples-value-or-externalValue``), while no example at
    # all trips ``params-must-include-examples``. The singular ``example``
    # keyword satisfies both. The parent ``PreviewOperationResponse`` example
    # keeps ``parameters`` empty for the same reason.
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "name": "limit",
                "in": "query",
                "required": False,
                "description": "Page size.",
            }
        },
    )

    name: str = Field(description="Parameter name.", examples=["limit"])
    location: str = Field(
        serialization_alias="in", description="OpenAPI parameter location.", examples=["query"]
    )
    required: bool = Field(description="Whether the parameter is required.", examples=[False])
    description: str = Field(
        description="Parameter description (empty string when absent).", examples=["Page size."]
    )


class PreviewOperationResponse(BaseModel):
    """A slimmed operation in a catalog spec preview."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "method": "GET",
                    "path": "/v1/charges",
                    "summary": "List charges",
                    "description": "",
                    "operation_id": "listCharges",
                    "parameters": [],
                    "security": ["BearerAuth"],
                    "tags": ["charges"],
                }
            ]
        }
    )

    method: str = Field(description="Upper-case HTTP method.", examples=["GET"])
    path: str = Field(description="Operation path template.", examples=["/v1/charges"])
    summary: str = Field(
        description="Operation summary (empty string when absent).", examples=["List charges"]
    )
    description: str = Field(
        description="Operation description (empty string when absent).",
        examples=["Returns a list of charges you've previously created, newest first."],
    )
    operation_id: str | None = Field(
        description="OpenAPI operationId, when declared.", examples=[ex.OPERATION_ID]
    )
    parameters: list[PreviewParameterResponse] = Field(
        description="Merged path- and operation-level parameters."
    )
    security: list[str] = Field(
        description="Flattened names of the security schemes that apply.",
        examples=[["BearerAuth"]],
    )
    tags: list[str] = Field(description="Operation tags.", examples=[["charges"]])


class PreviewInfoResponse(BaseModel):
    """The `info` block fields surfaced in a preview."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"title": "Stripe", "version": "2024-01-01", "description": "Payments."}]
        }
    )

    title: str | None = Field(description="Spec title.", examples=["Stripe"])
    version: str | None = Field(description="Spec version.", examples=[ex.API_VERSION])
    description: str | None = Field(description="Spec description.", examples=["Payments."])


class OperationPreviewListResponse(BaseModel):
    """Capped, offset-paginated operation preview for a catalog entry.

    Unlike list endpoints (cursor-paginated), the preview uses simple
    offset/limit pagination deliberately: it reads a single, already-fetched spec
    document and is hard-capped at ``PREVIEW_MAX_OPERATIONS`` operations, so there
    is no large/mutating result set that would justify keyset cursors.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "data": [],
                    "total": 42,
                    "offset": 0,
                    "truncated": True,
                    "info": {
                        "title": "Stripe",
                        "version": "2024-01-01",
                        "description": "Payments.",
                    },
                    "security_schemes": {},
                    "server_variables": {},
                    "required_schemes": [],
                }
            ]
        }
    )

    data: list[PreviewOperationResponse] = Field(description="The page of previewed operations.")
    total: int = Field(description="Total operations in the spec (pre-page).", examples=[42])
    offset: int = Field(description="Offset of the returned window.", examples=[0])
    truncated: bool = Field(
        description="Whether more operations follow this window.", examples=[True]
    )
    info: PreviewInfoResponse = Field(description="The spec's `info` block.")
    security_schemes: dict[str, dict[str, object]] = Field(
        description=(
            "Slimmed `components.securitySchemes` projection. An `oauth2` scheme lists "
            "its flow names in `flows` and each flow's `authorizationUrl`, `tokenUrl`, "
            "`refreshUrl` and `scopes` in `flow_details`."
        ),
        examples=[{"BearerAuth": {"type": "http", "scheme": "bearer"}}],
    )
    server_variables: dict[str, dict[str, object]] = Field(
        default_factory=dict,
        description=(
            "The first server's URL variables (`servers[0].variables`), each with its "
            "`default`, `enum` and `description` when the spec sets them."
        ),
        examples=[{"tenant": {"default": "acme", "description": "Your tenant subdomain."}}],
    )
    required_schemes: list[str] = Field(
        default_factory=list,
        description=(
            "Every security scheme some operation in the spec requires, over the whole "
            "spec (not just this window or filter)."
        ),
        examples=[["BearerAuth"]],
    )
