"""APIs router — import, list, retrieval, and lifecycle endpoints."""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from jentic.problem_details import BadRequest, ProblemDetailException

from jentic_one.registry.services.api_service import ApiService, ApiView
from jentic_one.registry.services.operation_service import OperationService, OperationSummaryPage
from jentic_one.registry.services.revision_service import (
    RevisionPageItem,
    RevisionService,
    RevisionView,
)
from jentic_one.registry.services.spec_download_service import SpecDocument, SpecDownloadService
from jentic_one.registry.web.content_negotiation import accepted_media_types
from jentic_one.registry.web.deps import get_api_service
from jentic_one.registry.web.schemas.apis import (
    ApiImportLinksResponse,
    ApiImportRequest,
    ApiImportResponse,
    ApiLinksResponse,
    ApiListResponse,
    ApiReferenceResponse,
    ApiResponse,
    ApiRevisionLinksResponse,
    ApiRevisionListResponse,
    ApiRevisionResponse,
    ApiRevisionSourceInlineResponse,
    ApiRevisionSourceUrlResponse,
    ApiUpdateRequest,
    OperationSummaryLinksResponse,
    OperationSummaryListResponse,
    OperationSummaryResponse,
    SecuritySchemeListResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.models import ApiRevisionSourceType, ApiRevisionState
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web import get_ctx, get_current_identity
from jentic_one.shared.web.links import build_link
from jentic_one.shared.web.openapi_responses import not_found

router = APIRouter()


@router.get("/apis", response_model=ApiListResponse, response_model_by_alias=True)
async def list_apis(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    api_svc: ApiService = Depends(get_api_service),
    vendor: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    """List locally registered APIs with optional vendor filter and pagination.

    This is the **imported** registry — APIs present in this deployment. The
    public catalog of importable-but-not-yet-imported APIs is a separate surface
    (``GET /catalog``); the two are not blended.
    """
    try:
        page = await api_svc.list_all(vendor=vendor, cursor=cursor, limit=limit)
    except InvalidCursorError:
        raise BadRequest(detail="Invalid pagination cursor", instance="/apis") from None

    data: list[ApiResponse] = []
    for item in page.data:
        self_link = build_link(request, f"/apis/{item.vendor}/{item.name}/{item.version}")
        current_rev_link = (
            build_link(
                request,
                f"/apis/{item.vendor}/{item.name}/{item.version}/revisions/{item.current_revision_id}",
            )
            if item.current_revision_id
            else None
        )
        links = ApiLinksResponse(
            self_link=self_link,
            revisions=f"{self_link}/revisions",
            current_revision=current_rev_link,
        )
        data.append(
            ApiResponse(
                api=ApiReferenceResponse(
                    vendor=item.vendor,
                    name=item.name,
                    version=item.version,
                    host=item.host,
                ),
                catalog_api_id=item.catalog_api_id,
                display_name=item.display_name,
                description=item.description,
                icon_url=item.icon_url,
                current_revision_id=str(item.current_revision_id)
                if item.current_revision_id
                else None,
                revision_count=item.revision_count,
                operation_count=item.operation_count,
                security_schemes=item.security_schemes,
                origin=item.origin,
                source_url=item.source_url,
                update_available=item.update_available,
                created_at=item.created_at,
                updated_at=item.updated_at,
                links=links,
            )
        )

    resp = ApiListResponse(data=data, has_more=page.has_more, next_cursor=page.next_cursor)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get("/apis/{vendor}/{name}/{version}", response_model=ApiResponse)
async def get_api(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Retrieve a single API by its (vendor, name, version) identity."""
    svc = ApiService(ctx)
    view = await svc.get_by_identity(vendor, name, version)

    resp = _build_api_response(view, request)
    return JSONResponse(
        status_code=200,
        content=resp.model_dump(mode="json", by_alias=True),
    )


@router.get(
    "/apis/{vendor}/{name}/{version}/security-schemes",
    response_model=SecuritySchemeListResponse,
    summary="List security schemes for an API",
    responses=not_found(),
)
async def list_api_security_schemes(
    vendor: str,
    name: str,
    version: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """List security schemes (with OAuth2 flow URLs) for the API's current revision."""
    svc = ApiService(ctx)
    resp = await svc.get_security_schemes(vendor, name, version)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.post("/apis", status_code=202)
async def import_apis(
    request: Request,
    body: ApiImportRequest,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Import OpenAPI/Arazzo content as new API revisions (async)."""
    # Attribute each resulting revision to the authenticated principal unless the
    # client supplied an explicit `submitted_by` override (e.g. importing on
    # behalf of someone else). The job row's `created_by` records the caller
    # either way; this default carries the same principal through the ingest
    # pipeline onto ApiRevision.submitted_by, which was previously left null.
    sources = [s.model_dump(mode="json") for s in body.sources]
    for source in sources:
        if source.get("submitted_by") is None:
            source["submitted_by"] = identity.sub
    async with ctx.admin_db.transaction() as session:
        job_id = await enqueue_job(
            session,
            JobKind.IMPORT,
            created_by=identity.sub,
            actor_type=identity.actor_type,
            payload={"sources": sources},
        )

    resp = ApiImportResponse(
        job_id=job_id,
        status="queued",
        links=ApiImportLinksResponse(self_link=build_link(request, f"/jobs/{job_id}")),
    )
    return JSONResponse(
        status_code=202,
        content=resp.model_dump(by_alias=True),
    )


def _build_api_response(view: ApiView, request: Request) -> ApiResponse:
    self_link = build_link(request, f"/apis/{view.vendor}/{view.name}/{view.version}")
    current_rev_link = (
        build_link(
            request,
            f"/apis/{view.vendor}/{view.name}/{view.version}/revisions/{view.current_revision_id}",
        )
        if view.current_revision_id
        else None
    )
    return ApiResponse(
        api=ApiReferenceResponse(
            vendor=view.vendor,
            name=view.name,
            version=view.version,
            host=view.host,
        ),
        catalog_api_id=view.catalog_api_id,
        display_name=view.display_name,
        description=view.description,
        icon_url=view.icon_url,
        current_revision_id=view.current_revision_id,
        revision_count=view.revision_count,
        operation_count=view.operation_count,
        security_schemes=view.security_schemes,
        origin=view.origin,
        source_url=view.source_url,
        update_available=view.update_available,
        created_at=view.created_at,
        updated_at=view.updated_at,
        links=ApiLinksResponse(
            self_link=self_link,
            revisions=f"{self_link}/revisions",
            current_revision=current_rev_link,
        ),
    )


def _build_revision_source(
    item: RevisionPageItem | RevisionView,
) -> ApiRevisionSourceUrlResponse | ApiRevisionSourceInlineResponse | None:
    if item.source_type == ApiRevisionSourceType.URL:
        return ApiRevisionSourceUrlResponse(
            type="url",
            url=item.source_url or "",
            submitted_by=item.submitted_by,
        )
    if item.source_type == ApiRevisionSourceType.INLINE:
        return ApiRevisionSourceInlineResponse(
            type="inline",
            filename=item.source_filename,
            submitted_by=item.submitted_by,
        )
    return None


def _build_revision_links(
    request: Request, vendor: str, name: str, version: str, revision_id: str, state: str
) -> ApiRevisionLinksResponse:
    api_path = build_link(request, f"/apis/{vendor}/{name}/{version}")
    self_link = build_link(request, f"/apis/{vendor}/{name}/{version}/revisions/{revision_id}")
    promote: str | None = None
    archive: str | None = None
    if state == ApiRevisionState.DRAFT:
        promote = f"{self_link}:promote"
        archive = f"{self_link}:archive"
    elif state == ApiRevisionState.IMPORTED:
        archive = f"{self_link}:archive"
    return ApiRevisionLinksResponse(
        self_link=self_link,
        api=api_path,
        promote=promote,
        archive=archive,
    )


@router.get("/apis/{vendor}/{name}/{version}/revisions")
async def list_api_revisions(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    state: list[str] | None = Query(default=None),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    """List revisions for an API with optional state filter and cursor pagination."""
    svc = RevisionService(ctx)
    try:
        page = await svc.list_revisions(
            vendor=vendor,
            name=name,
            version=version,
            states=state,
            cursor=cursor,
            limit=limit,
        )
    except InvalidCursorError:
        raise BadRequest(
            detail="Invalid pagination cursor",
            instance=request.url.path,
        ) from None

    data: list[ApiRevisionResponse] = []
    for item in page.data:
        data.append(
            ApiRevisionResponse(
                revision_id=str(item.id),
                api=ApiReferenceResponse(
                    vendor=vendor,
                    name=name,
                    version=version,
                    host=item.host,
                ),
                source=_build_revision_source(item),
                spec_digest=item.spec_digest,
                operation_count=item.operation_count,
                submitted_by=item.submitted_by,
                state=item.state,
                origin=item.origin,
                is_current=item.is_current,
                promoted_at=item.promoted_at,
                archived_at=item.archived_at,
                created_at=item.created_at,
                links=_build_revision_links(
                    request, vendor, name, version, str(item.id), item.state
                ),
            )
        )

    resp = ApiRevisionListResponse(data=data, has_more=page.has_more, next_cursor=page.next_cursor)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get("/apis/{vendor}/{name}/{version}/revisions/{revision_id}")
async def get_api_revision(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    revision_id: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Retrieve a single revision by ID."""
    svc = RevisionService(ctx)
    view = await svc.get_revision(
        vendor=vendor, name=name, version=version, revision_id=revision_id
    )

    resp = ApiRevisionResponse(
        revision_id=str(view.id),
        api=ApiReferenceResponse(
            vendor=vendor,
            name=name,
            version=version,
            host=view.host,
        ),
        source=_build_revision_source(view),
        spec_digest=view.spec_digest,
        operation_count=view.operation_count,
        submitted_by=view.submitted_by,
        state=view.state,
        origin=view.origin,
        is_current=view.is_current,
        promoted_at=view.promoted_at,
        archived_at=view.archived_at,
        created_at=view.created_at,
        links=_build_revision_links(request, vendor, name, version, str(view.id), view.state),
    )
    return JSONResponse(
        status_code=200,
        content=resp.model_dump(mode="json", by_alias=True),
    )


def _build_operations_response(
    page: OperationSummaryPage, request: Request
) -> OperationSummaryListResponse:
    data: list[OperationSummaryResponse] = []
    for item in page.data:
        data.append(
            OperationSummaryResponse(
                operation_id=item.id,
                method=item.method,
                path=item.path,
                api=ApiReferenceResponse(
                    vendor=page.vendor,
                    name=page.name,
                    version=page.version,
                    host=item.host,
                ),
                revision_id=str(item.revision_id),
                name=item.name,
                description=item.description,
                tags=item.tags,
                deprecated=item.deprecated,
                links=OperationSummaryLinksResponse(
                    inspect=build_link(
                        request,
                        f"/inspect?operation_id={item.id}&revision_id={item.revision_id}",
                    ),
                ),
            )
        )
    return OperationSummaryListResponse(
        data=data,
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get(
    "/apis/{vendor}/{name}/{version}/operations",
    response_model=OperationSummaryListResponse,
    response_model_by_alias=True,
)
async def list_api_operations(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    """List operations for the API's current (live) revision."""
    svc = OperationService(ctx)
    try:
        page = await svc.list_for_live_revision(
            vendor=vendor, name=name, version=version, cursor=cursor, limit=limit
        )
    except InvalidCursorError:
        raise BadRequest(
            detail="Invalid pagination cursor",
            instance=request.url.path,
        ) from None

    resp = _build_operations_response(page, request)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get("/apis/{vendor}/{name}/{version}/revisions/{revision_id}/operations")
async def list_api_revision_operations(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    revision_id: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    """List operations for a specific revision."""
    svc = OperationService(ctx)
    try:
        page = await svc.list_for_revision(
            vendor=vendor,
            name=name,
            version=version,
            revision_id=revision_id,
            cursor=cursor,
            limit=limit,
        )
    except InvalidCursorError:
        raise BadRequest(
            detail="Invalid pagination cursor",
            instance=request.url.path,
        ) from None

    resp = _build_operations_response(page, request)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.patch("/apis/{vendor}/{name}/{version}")
async def update_api(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    body: ApiUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Partially update an API's presentation fields."""
    fields = body.model_dump(exclude_unset=True)
    svc = ApiService(ctx)
    if not fields:
        view = await svc.get_by_identity(vendor, name, version)
    else:
        view = await svc.update(vendor, name, version, fields=fields, identity=identity)

    resp = _build_api_response(view, request)
    return JSONResponse(status_code=200, content=resp.model_dump(mode="json", by_alias=True))


@router.delete("/apis/{vendor}/{name}/{version}", status_code=204)
async def delete_api(
    vendor: str,
    name: str,
    version: str,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Delete an API and all its revisions."""
    svc = ApiService(ctx)
    await svc.delete(vendor, name, version, identity=identity)
    return Response(status_code=204)


@router.post("/apis/{vendor}/{name}/{version}/revisions/{revision_id}:promote")
async def promote_revision(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    revision_id: str,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Promote a draft revision to published, archiving the current one."""
    svc = RevisionService(ctx)
    view = await svc.promote(vendor, name, version, revision_id, identity=identity)

    resp = _build_api_response(view, request)
    return JSONResponse(status_code=200, content=resp.model_dump(mode="json", by_alias=True))


@router.post("/apis/{vendor}/{name}/{version}/revisions/{revision_id}:archive")
async def archive_revision(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    revision_id: str,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Archive a draft revision."""
    svc = RevisionService(ctx)
    rev_view = await svc.archive(vendor, name, version, revision_id, identity=identity)

    resp = ApiRevisionResponse(
        revision_id=str(rev_view.id),
        api=ApiReferenceResponse(
            vendor=vendor,
            name=name,
            version=version,
            host=rev_view.host,
        ),
        source=_build_revision_source(rev_view),
        spec_digest=rev_view.spec_digest,
        operation_count=rev_view.operation_count,
        submitted_by=rev_view.submitted_by,
        state=rev_view.state,
        origin=rev_view.origin,
        is_current=rev_view.is_current,
        promoted_at=rev_view.promoted_at,
        archived_at=rev_view.archived_at,
        created_at=rev_view.created_at,
        links=_build_revision_links(
            request, vendor, name, version, str(rev_view.id), rev_view.state
        ),
    )
    return JSONResponse(status_code=200, content=resp.model_dump(mode="json", by_alias=True))


@router.delete("/apis/{vendor}/{name}/{version}/revisions/{revision_id}", status_code=204)
async def delete_revision(
    vendor: str,
    name: str,
    version: str,
    revision_id: str,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Delete an archived revision."""
    svc = RevisionService(ctx)
    await svc.delete(vendor, name, version, revision_id, identity=identity)
    return Response(status_code=204)


_SPEC_SUPPORTED_MEDIA_TYPES = {"application/json", "application/openapi+yaml", "application/yaml"}


def _spec_response(request: Request, doc: SpecDocument) -> Response:
    """Build a content-negotiated response for a spec document."""
    accept = accepted_media_types(request)
    for media_type in accept:
        if media_type in ("application/json", "*/*"):
            json_resp = JSONResponse(content=doc.content, media_type="application/json")
            json_resp.headers["Content-Disposition"] = (
                f'attachment; filename="{doc.filename_stem}.json"'
            )
            return json_resp
        if media_type in ("application/openapi+yaml", "application/yaml"):
            body = yaml.safe_dump(doc.content, sort_keys=False)
            yaml_resp = Response(content=body, media_type=media_type)
            yaml_resp.headers["Content-Disposition"] = (
                f'attachment; filename="{doc.filename_stem}.yaml"'
            )
            return yaml_resp

    supported = ", ".join(sorted(_SPEC_SUPPORTED_MEDIA_TYPES))
    raise ProblemDetailException(
        status_code=406,
        detail=f"Unsupported media type; supported: {supported}",
        type="not_acceptable",
        instance=request.url.path,
    )


@router.get("/apis/{vendor}/{name}/{version}/openapi")
async def get_api_spec(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    overlays: bool = Query(default=True),
) -> Response:
    """Download the OpenAPI spec for the API's current (live) revision."""
    # `overlays` is accepted for forward-compatibility with a future read-time merge.
    # Confirmed overlays are applied by *materialization* (a confirm re-ingests the
    # base spec with the overlay applied and promotes the result to the current
    # revision), so the current revision already embodies confirmed overlays and this
    # flag is currently a no-op — bodies are identical regardless of its value.
    svc = SpecDownloadService(ctx)
    doc = await svc.get_live_spec(vendor, name, version)
    return _spec_response(request, doc)


@router.get("/apis/{vendor}/{name}/{version}/revisions/{revision_id}/openapi")
async def get_api_revision_spec(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    revision_id: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    overlays: bool = Query(default=True),
) -> Response:
    """Download the OpenAPI spec for a specific revision."""
    # `overlays` is accepted for forward-compatibility with a future read-time merge.
    # Confirmed overlays are applied by materialization (they produce their own
    # revision), so a specific revision's body is served as-stored and this flag is
    # currently a no-op.
    svc = SpecDownloadService(ctx)
    doc = await svc.get_revision_spec(vendor, name, version, revision_id)
    return _spec_response(request, doc)
