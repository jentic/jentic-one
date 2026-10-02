"""Search router — POST /search endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from jentic.problem_details import ProblemDetailException

from jentic_one.registry.services.errors import (
    ArchivedRevisionPinError,
    InvalidApiFilterError,
    SearchUnavailableError,
)
from jentic_one.registry.services.search_service import SearchService
from jentic_one.registry.web.schemas.apis import ApiReferenceResponse
from jentic_one.registry.web.schemas.search import (
    OperationResultResponse,
    SearchLinksResponse,
    SearchRequest,
    SearchResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import InvalidSearchCursorError
from jentic_one.shared.web import get_ctx, get_current_identity
from jentic_one.shared.web.links import build_link

router = APIRouter()


@router.post(
    "/search",
    summary="Search operations",
    response_model=SearchResponse,
    response_model_by_alias=True,
)
async def search_operations(
    request: Request,
    body: SearchRequest,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Lexical (full-text) search over registered API operations."""
    service = SearchService(ctx)

    try:
        page = await service.search(
            query=body.query,
            apis=body.apis,
            revision_pins=body.revision_pins,
            limit=body.limit,
            cursor=body.cursor,
        )
    except SearchUnavailableError as exc:
        raise ProblemDetailException(
            status_code=501,
            detail=str(exc),
            type="search_unsupported",
        ) from exc
    except InvalidSearchCursorError as exc:
        raise ProblemDetailException(
            status_code=422,
            detail=str(exc),
            type="invalid_cursor",
        ) from exc
    except InvalidApiFilterError as exc:
        raise ProblemDetailException(
            status_code=422,
            detail=str(exc),
            type="invalid_api_filter",
        ) from exc
    except ArchivedRevisionPinError as exc:
        raise ProblemDetailException(
            status_code=422,
            detail=str(exc),
            type="archived_revision_pin",
        ) from exc

    results = [
        OperationResultResponse(
            type="operation",
            api=ApiReferenceResponse(
                vendor=r.api.vendor,
                name=r.api.name,
                version=r.api.version,
                host=r.api.host,
            ),
            operation_id=r.operation_id,
            method=r.method,
            url=r.url,
            target=r.target,
            name=r.name,
            description=r.description,
            relevance_score=r.relevance_score,
            links=SearchLinksResponse(inspect=build_link(request, r.inspect_link)),
        )
        for r in page.data
    ]

    response = SearchResponse(
        data=results,
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )

    # Return an explicit JSONResponse (rather than the model) so optional
    # envelope fields serialize as explicit ``null`` instead of being omitted:
    # ``next_cursor`` is always present for clients reading the page, and a hit's
    # optional ``name``/``description`` stay keyed. ``data`` is always a list, so
    # this is purely about preserving nullable fields. response_model on the route
    # keeps the OpenAPI schema (and generated clients) in sync. See issue #671.
    return JSONResponse(content=response.model_dump(mode="json", by_alias=True))
