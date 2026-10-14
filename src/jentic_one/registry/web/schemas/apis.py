"""Request/response schemas for the APIs endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ApiUpdateRequest(BaseModel):
    """Partial update payload for an API's presentation fields."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(
        default=None, description="Human-readable name to show for the API; null clears it."
    )
    description: str | None = Field(
        default=None, description="Free-text description of the API; null clears it."
    )
    icon_url: Annotated[HttpUrl, Field()] | None = Field(
        default=None, description="URL of an icon image for the API; null clears it."
    )


class ApiSourceUrl(BaseModel):
    """Import source pointing at a fetchable URL."""

    type: Literal["url"] = Field(description="Source kind; always `url` for this variant.")
    url: HttpUrl = Field(description="URL the OpenAPI or Arazzo document is fetched from.")
    vendor: str | None = Field(
        default=None,
        description="Vendor to register the API under; taken from the document when omitted.",
    )
    api_name: str | None = Field(
        default=None,
        description="Name to register the API under; taken from the document title when omitted.",
    )
    version: str | None = Field(
        default=None,
        description="Version to register the API under; taken from the document when omitted.",
    )
    submitted_by: str | None = Field(
        default=None,
        description="Who the resulting revision is attributed to; defaults to the caller.",
    )


class ApiSourceInline(BaseModel):
    """Import source carrying raw OpenAPI/Arazzo content inline."""

    type: Literal["inline"] = Field(description="Source kind; always `inline` for this variant.")
    content: str = Field(description="Raw OpenAPI or Arazzo document as a JSON or YAML string.")
    filename: str = Field(description="File name of the submitted document.")
    vendor: str | None = Field(
        default=None,
        description="Vendor to register the API under; taken from the document when omitted.",
    )
    api_name: str | None = Field(
        default=None,
        description="Name to register the API under; taken from the document title when omitted.",
    )
    version: str | None = Field(
        default=None,
        description="Version to register the API under; taken from the document when omitted.",
    )
    submitted_by: str | None = Field(
        default=None,
        description="Who the resulting revision is attributed to; defaults to the caller.",
    )


ApiSource = Annotated[ApiSourceUrl | ApiSourceInline, Field(discriminator="type")]


class ApiImportRequest(BaseModel):
    """Wrapper for a batch of import sources."""

    sources: list[ApiSource] = Field(
        min_length=1,
        max_length=100,
        description="Documents to import, between 1 and 100 per request.",
    )


class ApiImportLinksResponse(BaseModel):
    """Hypermedia links for an import response."""

    self_link: str = Field(
        serialization_alias="self", description="URL of the import job to poll for progress."
    )


class ApiImportResponse(BaseModel):
    """Acknowledgement payload for an asynchronous import job."""

    job_id: str = Field(description="ID of the import job.")
    status: str = Field(description="Status of the job when accepted; `queued`.")
    links: ApiImportLinksResponse = Field(serialization_alias="_links")


class ApiReferenceResponse(BaseModel):
    """Core API identifier triple plus derived host."""

    vendor: str = Field(description="Registry vendor of the API.")
    name: str = Field(description="Registry name of the API.")
    version: str = Field(description="Registry version of the API.")
    host: str | None = Field(
        description="Host the API is served from, derived from its spec; null if it declares none."
    )


class ApiLinksResponse(BaseModel):
    """Hypermedia links for a (local) API resource."""

    model_config = ConfigDict(populate_by_name=True)

    self_link: str = Field(serialization_alias="self", description="URL of this API.")
    revisions: str = Field(description="URL of the API's revision list.")
    current_revision: str | None = Field(
        default=None,
        description="URL of the API's current revision; null when it has no live revision.",
    )


class ApiResponse(BaseModel):
    """Full API aggregate response.

    ``GET /apis`` is the local registry — every item is an API imported into
    this deployment. The public catalog of importable-but-not-yet-imported APIs
    is a separate surface (``GET /catalog``); the two are not blended.
    """

    model_config = ConfigDict(populate_by_name=True)

    api: ApiReferenceResponse
    # Catalog identity slug this API was imported from (`domain[/sub-api]`,
    # e.g. `nytimes.com/article_search`) — the separable form the UI derives
    # friendly titles from. Null for manual/inline imports.
    catalog_api_id: str | None = Field(
        description=(
            "Catalog identity the API was imported from (`domain[/sub-api]`, for example "
            "`nytimes.com/article_search`); null for manual imports."
        )
    )
    display_name: str | None = Field(
        description="Human-readable name to show for the API; null if not set."
    )
    description: str | None = Field(
        description="Free-text description of the API; null if not set."
    )
    icon_url: str | None = Field(description="URL of an icon image for the API; null if not set.")
    current_revision_id: str | None = Field(
        description=(
            "ID of the revision the API currently serves; null when it has no live revision."
        )
    )
    revision_count: int = Field(description="Number of revisions recorded for the API.")
    operation_count: int = Field(description="Number of operations the API exposes.")
    security_schemes: list[str] = Field(
        description="Distinct security scheme types declared by the current revision."
    )
    origin: str | None = Field(
        default=None,
        description=(
            "Provenance of the current revision: `catalog` (imported from the public "
            "catalog), `overlay` (materialized from a confirmed overlay), or null (manual "
            "import)."
        ),
    )
    source_url: str | None = Field(
        default=None,
        description="Upstream spec URL backing the current revision, when known (catalog linkage).",
    )
    update_available: bool = Field(
        default=False,
        description=(
            "Whether the upstream spec at `source_url` has a notified update this API "
            "hasn't adopted yet (Flow-3). Re-importing clears it."
        ),
    )
    created_at: datetime = Field(description="When the API was created (RFC 3339).")
    updated_at: datetime = Field(
        description=(
            "When the API was last updated (RFC 3339); equals `created_at` if never updated."
        )
    )
    links: ApiLinksResponse = Field(serialization_alias="_links")


class ApiListResponse(BaseModel):
    """Cursor-paginated list of APIs."""

    data: list[ApiResponse] = Field(description="The page of APIs.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )


# --- Revision response schemas ---


class ApiRevisionSourceUrlResponse(BaseModel):
    """Source descriptor for a URL-fetched revision."""

    type: Literal["url"]
    url: str
    submitted_by: str | None


class ApiRevisionSourceInlineResponse(BaseModel):
    """Source descriptor for an inline-submitted revision."""

    type: Literal["inline"]
    filename: str | None
    submitted_by: str | None


ApiRevisionSourceResponse = Annotated[
    ApiRevisionSourceUrlResponse | ApiRevisionSourceInlineResponse,
    Field(discriminator="type"),
]


class ApiRevisionLinksResponse(BaseModel):
    """Hypermedia links for a revision resource."""

    model_config = ConfigDict(populate_by_name=True)

    self_link: str = Field(serialization_alias="self")
    api: str
    promote: str | None = None
    archive: str | None = None


class ApiRevisionResponse(BaseModel):
    """Full API revision response."""

    model_config = ConfigDict(populate_by_name=True)

    revision_id: str
    api: ApiReferenceResponse
    source: ApiRevisionSourceResponse | None
    spec_digest: str | None
    operation_count: int
    submitted_by: str | None
    state: str
    origin: str | None = None
    is_current: bool
    promoted_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    links: ApiRevisionLinksResponse = Field(serialization_alias="_links")


class ApiRevisionListResponse(BaseModel):
    """Cursor-paginated list of API revisions."""

    data: list[ApiRevisionResponse]
    has_more: bool
    next_cursor: str | None = None


# --- Operation response schemas ---


class OperationSummaryLinksResponse(BaseModel):
    """Hypermedia links for an operation summary."""

    inspect: str = Field(description="URL of the operation's full detail via `GET /inspect`.")


class OperationSummaryResponse(BaseModel):
    """Single operation in a paginated list."""

    model_config = ConfigDict(populate_by_name=True)

    operation_id: str = Field(description="Registry ID of the operation.")
    method: str = Field(description="HTTP method of the operation.")
    path: str = Field(description="Path template of the operation as declared in the spec.")
    api: ApiReferenceResponse
    revision_id: str = Field(description="ID of the revision the operation belongs to.")
    name: str | None = Field(
        default=None, description="Summary of the operation from the spec; null if it has none."
    )
    description: str | None = Field(
        default=None, description="Description of the operation; null if the spec gives none."
    )
    tags: list[str] = Field(
        default_factory=list, description="Tags the spec assigns to the operation."
    )
    deprecated: bool = Field(
        default=False, description="Whether the spec marks the operation as deprecated."
    )
    links: OperationSummaryLinksResponse = Field(serialization_alias="_links")


class OperationSummaryListResponse(BaseModel):
    """Cursor-paginated list of operations."""

    data: list[OperationSummaryResponse] = Field(description="The page of operations.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )


# --- Security scheme response schemas ---


class SecuritySchemeFlowResponse(BaseModel):
    """A single OAuth2 flow within a security scheme."""

    flow_type: str = Field(
        description="OAuth2 flow type (for example `authorizationCode` or `clientCredentials`)."
    )
    authorization_url: str | None = Field(
        default=None, description="Authorization endpoint URL; null if the flow has none."
    )
    token_url: str | None = Field(
        default=None, description="Token endpoint URL; null if the flow has none."
    )
    refresh_url: str | None = Field(
        default=None, description="Token refresh URL; null if the flow has none."
    )
    scopes: dict[str, str] | None = Field(
        default=None, description="Available scopes mapped to their descriptions."
    )


class SecuritySchemeResponse(BaseModel):
    """Full security scheme detail for a revision."""

    name: str = Field(description="Name of the security scheme as declared in the spec.")
    type: str = Field(
        description=(
            "Security scheme type (for example `apiKey`, `http`, `oauth2` or `openIdConnect`)."
        )
    )
    scheme: str | None = Field(
        default=None,
        description=(
            "HTTP authentication scheme (for example `bearer`); null unless `type` is `http`."
        ),
    )
    bearer_format: str | None = Field(
        default=None, description="Hint for the bearer token format; null if not declared."
    )
    in_location: str | None = Field(
        default=None,
        description=(
            "Where an API key is sent (`header`, `query` or `cookie`); null for other types."
        ),
    )
    param_name: str | None = Field(
        default=None,
        description="Name of the header, query or cookie parameter carrying the API key.",
    )
    open_id_connect_url: str | None = Field(
        default=None,
        description="OpenID Connect discovery URL; null unless `type` is `openIdConnect`.",
    )
    description: str | None = Field(
        default=None, description="Description of the scheme from the spec; null if none."
    )
    flows: list[SecuritySchemeFlowResponse] = Field(
        default_factory=list,
        description="OAuth2 flows declared by the scheme; empty for other types.",
    )


class SecuritySchemeListResponse(BaseModel):
    """List of security schemes for an API's current revision."""

    data: list[SecuritySchemeResponse] = Field(
        description="Security schemes declared by the API's current revision."
    )
