"""Request/response schemas for the search endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.registry.web.schemas.apis import ApiReferenceResponse


class SearchRequest(BaseModel):
    """POST /search request body."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    apis: list[str] | None = Field(
        default=None,
        description=(
            "Restrict results to these APIs. Each entry is a "
            "'vendor[/name[/version]]' identifier of an imported API "
            "(e.g. 'github-com/api-github-com/1.1.4'); vendor and name are "
            "normalized like ingest, so raw spellings such as 'stripe.com/api' "
            "also resolve. The legacy colon-separated form "
            "'vendor[:name[:version]]' is also accepted."
        ),
    )
    limit: int = Field(default=10, ge=1, le=100)
    cursor: str | None = None
    revision_pins: dict[str, str] | None = Field(
        default=None,
        description=(
            "Pin specific APIs to a revision for this search. Keys are full "
            "'vendor/name/version' identifiers (colon-separated also accepted); "
            "values are revision UUIDs."
        ),
    )


class SearchLinksResponse(BaseModel):
    """Hypermedia links for a search result row."""

    inspect: str


class OperationResultResponse(BaseModel):
    """A single search result matching the OperationResult spec."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["operation"] = "operation"
    api: ApiReferenceResponse
    operation_id: str
    method: str
    url: str
    target: str = Field(
        description=(
            "The value to pass as the operation target to inspect/execute "
            "(CLI argument; MCP operation_id argument). METHOD:url when url is "
            "absolute; the registry operation_id when url is host-relative "
            "(the spec declares no servers, or only a relative one) — such a "
            "target is inspect-only: with no upstream host there is nothing "
            "for the broker to proxy, so execute refuses it."
        ),
    )
    name: str | None = None
    description: str | None = None
    relevance_score: float
    links: SearchLinksResponse = Field(serialization_alias="_links")


class SearchResponse(BaseModel):
    """Cursor-paginated search results page."""

    data: list[OperationResultResponse]
    has_more: bool
    next_cursor: str | None = None
