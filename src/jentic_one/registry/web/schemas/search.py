"""Request/response schemas for the search endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.registry.web.schemas.apis import ApiReferenceResponse
from jentic_one.shared.web import openapi_examples as ex

# Example-only values shared by several fields below.
_EX_OPERATION_URL = "https://api.stripe.com/v1/charges"
# Search keyset cursor: base64 ``{"d": <distance>, "id": <operation id>}``
# (``encode_search_cursor``), not the ``{t, id}`` form of ``ex.CURSOR``.
_EX_SEARCH_CURSOR = (
    # pragma: allowlist nextline secret
    "eyJkIjogMC4yODE2LCAiaWQiOiAib3BfZjkyZDY5ZjE2MjFmMzgxYmNlOGFhZmY0NzA1M2M2MWIwNjlmZmUzNCJ9"
)


class SearchRequest(BaseModel):
    """POST /search request body."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        description="Free-text search query; must not be empty.",
        examples=["list recent charges"],
    )
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
        examples=[[ex.API_VENDOR_NAME]],
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return (1-100).",
        examples=[10],
    )
    cursor: str | None = Field(
        default=None,
        description="Opaque cursor from a previous page's `next_cursor`; omit for the first page.",
        examples=[_EX_SEARCH_CURSOR],
    )
    revision_pins: dict[str, str] | None = Field(
        default=None,
        description=(
            "Pin specific APIs to a revision for this search. Keys are full "
            "'vendor/name/version' identifiers (colon-separated also accepted); "
            "values are revision UUIDs."
        ),
        examples=[{f"{ex.API_VENDOR}/{ex.API_NAME}/{ex.API_VERSION}": ex.REVISION_ID}],
    )


class SearchLinksResponse(BaseModel):
    """Hypermedia links for a search result row."""

    inspect: str = Field(
        description="URL of the operation's full detail via `GET /inspect`.",
        examples=[f"{ex.BASE_URL}/inspect?id=GET%20https%3A%2F%2Fapi.stripe.com%2Fv1%2Fcharges"],
    )


class OperationResultResponse(BaseModel):
    """A single search result matching the OperationResult spec."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["operation"] = Field(
        default="operation", description="Result kind; always `operation`.", examples=["operation"]
    )
    api: ApiReferenceResponse
    operation_id: str = Field(
        description="Registry ID of the operation.", examples=[ex.REGISTRY_OPERATION_ID]
    )
    method: str = Field(description="HTTP method of the operation.", examples=["GET"])
    url: str = Field(
        description=(
            "URL of the operation, built from the spec's server and path; host-relative "
            "when the spec declares no absolute server."
        ),
        examples=[_EX_OPERATION_URL],
    )
    target: str = Field(
        description=(
            "The value to pass as the operation target to inspect/execute "
            "(CLI argument; MCP operation_id argument). METHOD:url when url is "
            "absolute; the registry operation_id when url is host-relative "
            "(the spec declares no servers, or only a relative one) — such a "
            "target is inspect-only: with no upstream host there is nothing "
            "for the broker to proxy, so execute refuses it."
        ),
        examples=[f"GET:{_EX_OPERATION_URL}"],
    )
    name: str | None = Field(
        default=None,
        description="Summary of the operation from the spec; null if it has none.",
        examples=["List all charges"],
    )
    description: str | None = Field(
        default=None,
        description="Description of the operation; null if the spec gives none.",
        examples=["Returns a list of charges you've previously created, newest first."],
    )
    relevance_score: float = Field(
        description="Relevance of the result to the query, from 0 to 1; higher is more relevant.",
        examples=[0.87],
    )
    links: SearchLinksResponse = Field(serialization_alias="_links")


class SearchResponse(BaseModel):
    """Cursor-paginated search results page."""

    data: list[OperationResultResponse] = Field(description="The page of matching operations.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[_EX_SEARCH_CURSOR],
    )
