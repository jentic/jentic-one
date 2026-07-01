"""Catalog (Discover) router — thin glue over CatalogService.

Handlers only: project CatalogService views into web schemas and build links.
No DB access, no try/except (service errors map to Problem Details via the
registry error handler). Action verbs use the colon pattern (``:refresh``,
``:import``). Route ordering matters: ``/operations`` is declared before the bare
``/{api_id:path}`` because the ``:path`` converter greedily eats slashes.

``api_id`` can contain slashes (umbrella vendors like ``googleapis.com/admin``),
so the path parameter uses Starlette's ``{api_id:path}`` converter, which matches
**literal** ``/``. Clients must therefore send the slash unencoded — a generated
HTTP client that percent-encodes path params (``%2F``) will NOT match these
routes. The OpenAPI document can only describe ``{api_id}`` as a plain string, so
this constraint cannot be expressed in the schema; any client wiring (e.g. the
Discover UI) must build the path by interpolation, not by an encode-each-segment
helper. (Kept as path-addressing for parity with jentic-mini; revisit with a
query-param/opaque-key scheme if a generated client can't honour raw slashes.)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from jentic.problem_details import BadRequest, ProblemDetailException

from jentic_one.registry.services.catalog.service import CatalogEntryView, CatalogService
from jentic_one.registry.web.deps import get_catalog_service
from jentic_one.registry.web.schemas.apis import ApiImportLinksResponse, ApiImportResponse
from jentic_one.registry.web.schemas.catalog import (
    CatalogEntryLinksResponse,
    CatalogEntryResponse,
    CatalogListResponse,
    CatalogRefreshResponse,
    OperationPreviewListResponse,
    PreviewInfoResponse,
    PreviewOperationResponse,
    PreviewParameterResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.links import build_link

router = APIRouter()


def _entry_response(request: Request, view: CatalogEntryView) -> CatalogEntryResponse:
    self_link = build_link(request, f"/catalog/{view.api_id}")
    return CatalogEntryResponse(
        api_id=view.api_id,
        vendor=view.vendor,
        path=view.path,
        spec_url=view.spec_url,
        registered=view.registered,
        links=CatalogEntryLinksResponse(
            self_link=self_link,
            operations=f"{self_link}/operations",
            import_link=f"{self_link}:import",
            github=view.github_url,
        ),
    )


@router.get("/catalog", response_model=CatalogListResponse, response_model_by_alias=True)
async def list_catalog(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: CatalogService = Depends(get_catalog_service),
    q: str | None = None,
    registered_only: bool = False,
    unregistered_only: bool = False,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    """List a keyset page of browsable catalog entries (search/filter aware).

    The catalog holds thousands of entries, so this is cursor-paginated like
    ``GET /apis``: follow ``next_cursor`` until ``has_more`` is false.
    ``catalog_total``/``registered_count`` count the whole manifest, not the
    page, so the Discover status row stays stable while scrolling.
    """
    if registered_only and unregistered_only:
        raise ProblemDetailException(
            status_code=422,
            detail="registered_only and unregistered_only are mutually exclusive",
            type="mutually_exclusive_filters",
            instance="/catalog",
        )

    try:
        page = await svc.list_all(
            q=q,
            registered_only=registered_only,
            unregistered_only=unregistered_only,
            cursor=cursor,
            limit=limit,
        )
    except InvalidCursorError:
        raise BadRequest(detail="Invalid pagination cursor", instance="/catalog") from None

    resp = CatalogListResponse(
        data=[_entry_response(request, v) for v in page.data],
        catalog_total=page.catalog_total,
        registered_count=page.registered_count,
        manifest_age_seconds=page.manifest_age_seconds,
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get(
    "/catalog/{api_id:path}/operations",
    response_model=OperationPreviewListResponse,
    response_model_by_alias=True,
)
async def preview_catalog_operations(
    api_id: str,
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: CatalogService = Depends(get_catalog_service),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=0, le=200),
    tag: str | None = None,
    q: str | None = Query(default=None, max_length=500),
) -> JSONResponse:
    """Preview the operations of a catalog entry's spec (capped, offset-paginated).

    ``tag`` and ``q`` filter the spec's operations server-side before windowing,
    so the UI's search box covers every operation in the spec and pages the
    filtered set via ``offset``/``limit`` ("Load more").
    """
    preview = await svc.preview(api_id, offset=offset, limit=limit, tag=tag, q=q)
    resp = OperationPreviewListResponse(
        data=[
            PreviewOperationResponse(
                method=op.method,
                path=op.path,
                summary=op.summary,
                description=op.description,
                operation_id=op.operation_id,
                parameters=[
                    PreviewParameterResponse(
                        name=p.name,
                        location=p.location,
                        required=p.required,
                        description=p.description,
                    )
                    for p in op.parameters
                ],
                security=op.security,
                tags=op.tags,
            )
            for op in preview.operations
        ],
        total=preview.total,
        offset=preview.offset,
        truncated=preview.truncated,
        info=PreviewInfoResponse(
            title=preview.info.title,
            version=preview.info.version,
            description=preview.info.description,
        ),
        security_schemes=preview.security_schemes,
    )
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get(
    "/catalog/{api_id:path}", response_model=CatalogEntryResponse, response_model_by_alias=True
)
async def get_catalog_entry(
    request: Request,
    api_id: str,
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: CatalogService = Depends(get_catalog_service),
) -> JSONResponse:
    """Retrieve a single catalog entry by api_id."""
    view = await svc.get(api_id)
    resp = _entry_response(request, view)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.post("/catalog:refresh", status_code=200, response_model=CatalogRefreshResponse)
async def refresh_catalog(
    identity: Identity = get_current_identity(required_permissions=["org:admin"]),
    svc: CatalogService = Depends(get_catalog_service),
) -> JSONResponse:
    """Force a refresh of the catalog cache from the upstream manifest (org:admin)."""
    result = await svc.refresh()
    resp = CatalogRefreshResponse(count=result.count)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.post(
    "/catalog/{api_id:path}:import",
    status_code=202,
    response_model=ApiImportResponse,
    response_model_by_alias=True,
)
async def import_catalog_entry(
    request: Request,
    api_id: str,
    identity: Identity = get_current_identity(required_permissions=["catalog:import"]),
    svc: CatalogService = Depends(get_catalog_service),
) -> JSONResponse:
    """Enqueue an async import of a catalog entry into the local registry."""
    job_id = await svc.import_entry(api_id, identity)
    resp = ApiImportResponse(
        job_id=job_id,
        status="queued",
        links=ApiImportLinksResponse(self_link=build_link(request, f"/jobs/{job_id}")),
    )
    return JSONResponse(status_code=202, content=resp.model_dump(by_alias=True))
