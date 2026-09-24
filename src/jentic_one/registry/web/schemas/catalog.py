"""Request/response schemas for the catalog (Discover) endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CatalogSnoozeRequest(BaseModel):
    """Body for ``POST /catalog/{api_id}:snooze`` (C1, #925).

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
    )


class CatalogEntryLinksResponse(BaseModel):
    """Hypermedia links for a catalog entry."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "self": "/catalog/stripe.com",
                    "operations": "/catalog/stripe.com/operations",
                    "import": "/catalog/stripe.com:import",
                    "github": "https://github.com/jentic/jentic-public-apis/tree/main/apis/openapi/stripe.com",
                }
            ]
        },
    )

    self_link: str = Field(
        serialization_alias="self", description="Canonical URL of this catalog entry."
    )
    operations: str = Field(description="URL of the entry's operation preview.")
    import_link: str = Field(
        serialization_alias="import",
        description="URL of the catalog import action (`POST /catalog/{api_id}:import`).",
    )
    github: str | None = Field(
        default=None, description="Human-facing GitHub tree URL for the entry, when known."
    )


class CatalogEntryResponse(BaseModel):
    """A single browsable catalog entry."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "api_id": "stripe.com",
                    "vendor": "stripe.com",
                    "path": "apis/openapi/stripe.com",
                    "spec_url": "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi/stripe.com/main/2024-01-01/openapi.json",
                    "registered": False,
                    "_links": {
                        "self": "/catalog/stripe.com",
                        "operations": "/catalog/stripe.com/operations",
                        "import": "/catalog/stripe.com:import",
                    },
                }
            ]
        },
    )

    api_id: str = Field(
        description="Catalog identity of the API (manifest domain, e.g. `stripe.com`)."
    )
    vendor: str | None = Field(
        description="Registrable-domain vendor derived from `api_id` (e.g. `stripe.com`)."
    )
    path: str | None = Field(description="Manifest path of the entry within the public-APIs repo.")
    spec_url: str | None = Field(
        description="Fetchable OpenAPI spec URL the entry resolves to (used for import + coverage)."
    )
    registered: bool = Field(
        description=(
            "Whether this entry is already imported locally — its `spec_url` backs a "
            "non-archived revision in `GET /apis`."
        )
    )
    update_available: bool = Field(
        default=False,
        description=(
            "Whether this (registered) entry has an upstream spec update the local "
            "revision hasn't adopted yet. Always false for unregistered entries."
        ),
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
                    "manifest_age_seconds": 3600,
                    "has_more": True,
                    "next_cursor": "eyJpZCI6ICJzdHJpcGUuY29tIn0=",
                }
            ]
        }
    )

    data: list[CatalogEntryResponse] = Field(description="The page of catalog entries.")
    catalog_total: int = Field(
        description="Total entries in the whole manifest (pre-filter, pre-page)."
    )
    registered_count: int = Field(
        description="Count of whole-manifest entries already imported locally."
    )
    outdated_count: int = Field(
        default=0,
        description=(
            "Count of whole-manifest registered entries with an upstream update available."
        ),
    )
    manifest_age_seconds: int | None = Field(
        default=None,
        description="Age of the cached manifest in seconds, or null when the cache is empty.",
    )
    has_more: bool = Field(default=False, description="Whether another page follows.")
    next_cursor: str | None = Field(
        default=None, description="Opaque keyset cursor for the next page (null when done)."
    )


class CatalogRefreshResponse(BaseModel):
    """Acknowledgement of a manifest refresh."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"count": 5231, "status": "refreshed"}]}
    )

    count: int = Field(description="Number of entries in the freshly fetched manifest.")
    status: str = Field(default="refreshed", description="Refresh outcome marker.")


class PreviewParameterResponse(BaseModel):
    """A slimmed parameter in an operation preview."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {"name": "limit", "in": "query", "required": False, "description": "Page size."}
            ]
        },
    )

    name: str = Field(description="Parameter name.")
    location: str = Field(serialization_alias="in", description="OpenAPI parameter location.")
    required: bool = Field(description="Whether the parameter is required.")
    description: str = Field(description="Parameter description (empty string when absent).")


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

    method: str = Field(description="Upper-case HTTP method.")
    path: str = Field(description="Operation path template.")
    summary: str = Field(description="Operation summary (empty string when absent).")
    description: str = Field(description="Operation description (empty string when absent).")
    operation_id: str | None = Field(description="OpenAPI operationId, when declared.")
    parameters: list[PreviewParameterResponse] = Field(
        description="Merged path- and operation-level parameters."
    )
    security: list[str] = Field(description="Flattened names of the security schemes that apply.")
    tags: list[str] = Field(description="Operation tags.")


class PreviewInfoResponse(BaseModel):
    """The `info` block fields surfaced in a preview."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"title": "Stripe", "version": "2024-01-01", "description": "Payments."}]
        }
    )

    title: str | None = Field(description="Spec title.")
    version: str | None = Field(description="Spec version.")
    description: str | None = Field(description="Spec description.")


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
                }
            ]
        }
    )

    data: list[PreviewOperationResponse] = Field(description="The page of previewed operations.")
    total: int = Field(description="Total operations in the spec (pre-page).")
    offset: int = Field(description="Offset of the returned window.")
    truncated: bool = Field(description="Whether more operations follow this window.")
    info: PreviewInfoResponse = Field(description="The spec's `info` block.")
    security_schemes: dict[str, dict[str, object]] = Field(
        description="Slimmed `components.securitySchemes` projection."
    )
