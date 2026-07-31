"""Request/response schemas for the APIs endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ApiUpdateRequest(BaseModel):
    """Partial update payload for an API's presentation fields."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    description: str | None = None
    icon_url: Annotated[HttpUrl, Field()] | None = None


class ApiSourceUrl(BaseModel):
    """Import source pointing at a fetchable URL."""

    type: Literal["url"]
    url: HttpUrl
    vendor: str | None = None
    api_name: str | None = None
    version: str | None = None
    submitted_by: str | None = None


class ApiSourceInline(BaseModel):
    """Import source carrying raw OpenAPI/Arazzo content inline."""

    type: Literal["inline"]
    content: str
    filename: str
    vendor: str | None = None
    api_name: str | None = None
    version: str | None = None
    submitted_by: str | None = None


ApiSource = Annotated[ApiSourceUrl | ApiSourceInline, Field(discriminator="type")]


class ApiImportRequest(BaseModel):
    """Wrapper for a batch of import sources."""

    sources: list[ApiSource] = Field(min_length=1, max_length=100)


class ApiImportLinksResponse(BaseModel):
    """Hypermedia links for an import response."""

    self_link: str = Field(serialization_alias="self")


class ApiImportResponse(BaseModel):
    """Acknowledgement payload for an asynchronous import job."""

    job_id: str
    status: str
    links: ApiImportLinksResponse = Field(serialization_alias="_links")


class ApiReferenceResponse(BaseModel):
    """Core API identifier triple plus derived host."""

    vendor: str
    name: str
    version: str
    host: str | None


class ApiLinksResponse(BaseModel):
    """Hypermedia links for a (local) API resource."""

    model_config = ConfigDict(populate_by_name=True)

    self_link: str = Field(serialization_alias="self")
    revisions: str
    current_revision: str | None = None


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
    catalog_api_id: str | None
    display_name: str | None
    description: str | None
    icon_url: str | None
    current_revision_id: str | None
    revision_count: int
    operation_count: int
    security_schemes: list[str]
    created_at: datetime
    updated_at: datetime
    links: ApiLinksResponse = Field(serialization_alias="_links")


class ApiListResponse(BaseModel):
    """Cursor-paginated list of APIs."""

    data: list[ApiResponse]
    has_more: bool
    next_cursor: str | None = None


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

    inspect: str


class OperationSummaryResponse(BaseModel):
    """Single operation in a paginated list."""

    model_config = ConfigDict(populate_by_name=True)

    operation_id: str
    method: str
    path: str
    api: ApiReferenceResponse
    revision_id: str
    name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    deprecated: bool = False
    links: OperationSummaryLinksResponse = Field(serialization_alias="_links")


class OperationSummaryListResponse(BaseModel):
    """Cursor-paginated list of operations."""

    data: list[OperationSummaryResponse]
    has_more: bool
    next_cursor: str | None = None


# --- Security scheme response schemas ---


class SecuritySchemeFlowResponse(BaseModel):
    """A single OAuth2 flow within a security scheme."""

    flow_type: str
    authorization_url: str | None = None
    token_url: str | None = None
    refresh_url: str | None = None
    scopes: dict[str, str] | None = None


class SecuritySchemeResponse(BaseModel):
    """Full security scheme detail for a revision."""

    name: str
    type: str
    scheme: str | None = None
    bearer_format: str | None = None
    in_location: str | None = None
    param_name: str | None = None
    open_id_connect_url: str | None = None
    description: str | None = None
    flows: list[SecuritySchemeFlowResponse] = Field(default_factory=list)


class SecuritySchemeListResponse(BaseModel):
    """List of security schemes for an API's current revision."""

    data: list[SecuritySchemeResponse]
