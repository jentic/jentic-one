"""Catalog (Discover) router — thin glue over CatalogService.

Handlers only: project CatalogService views into web schemas and build links.
No DB access, no try/except (service errors map to Problem Details via the
registry error handler). Action verbs use the colon pattern (``:refresh``,
``:import``). Route ordering matters: ``/operations`` is declared before the bare
``/{entry_id:path}`` because the ``:path`` converter greedily eats slashes.

An ``entry_id`` is ``{source_slug}/{external_id}`` and the external id may itself
contain slashes (``public/googleapis.com/admin``), so the path parameter uses
Starlette's ``{entry_id:path}`` converter, which matches **literal** ``/``.
Clients must therefore send the slashes unencoded — a generated HTTP client that
percent-encodes path params (``%2F``) will NOT match these routes. The OpenAPI
document can only describe ``{entry_id}`` as a plain string, so any client wiring
must build the path by interpolation, not by an encode-each-segment helper.

Every entry route answers an entry hidden from the caller exactly like a missing
one (``404``, same body), and reads with the caller's effective permissions.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from jentic.problem_details import BadRequest, ProblemDetailException

from jentic_one.registry.services.catalog.service import CatalogEntryView, CatalogService
from jentic_one.registry.services.errors import (
    CatalogEntryNotFoundError,
    ForcedSyncForbiddenError,
)
from jentic_one.registry.web.deps import get_catalog_service
from jentic_one.registry.web.schemas.apis import ApiImportLinksResponse, ApiImportResponse
from jentic_one.registry.web.schemas.catalog import (
    CatalogEntryLinksResponse,
    CatalogEntryResponse,
    CatalogListResponse,
    CatalogRefreshResponse,
    CatalogRefreshSourceResponse,
    CatalogSnoozeRequest,
    OperationPreviewListResponse,
    PreviewInfoResponse,
    PreviewOperationResponse,
    PreviewParameterResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import ORG_ADMIN
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web import effective_permissions, get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.links import build_link
from jentic_one.shared.web.openapi_responses import conflict

router = APIRouter()


#: Longest ``entry_id`` the catalog stores.
_ENTRY_ID_MAX_LENGTH = 320

_ENTRY_ID_DESCRIPTION = (
    "Catalog entry identity, `{source_slug}/{external_id}`; send every `/` unencoded."
)

EntryIdPath = Annotated[
    str,
    Path(description=_ENTRY_ID_DESCRIPTION, openapi_examples=ex.example(ex.CATALOG_ENTRY_ID)),
]


def _checked_entry_id(entry_id: str) -> str:
    """Reject an id no entry can have (empty, ``.``/``..`` segments, over-long) as missing."""
    segments = entry_id.split("/")
    if (
        len(entry_id) > _ENTRY_ID_MAX_LENGTH
        or any(seg in ("", ".", "..") for seg in segments)
        or any(ord(ch) < 0x20 or ch == "\x7f" for ch in entry_id)
    ):
        raise CatalogEntryNotFoundError(entry_id)
    return entry_id


def _entry_response(request: Request, view: CatalogEntryView) -> CatalogEntryResponse:
    self_link = build_link(request, f"/catalog/{view.entry_id}")
    return CatalogEntryResponse(
        entry_id=view.entry_id,
        source_slug=view.source_slug,
        catalog_api_id=view.catalog_api_id,
        vendor=view.vendor,
        display_vendor=view.display_vendor,
        title=view.title,
        version=view.version,
        tags=list(view.tags),
        human_url=view.human_url,
        has_spec=view.has_spec,
        registered=view.registered,
        update_available=view.update_available,
        alternates=view.alternates,
        links=CatalogEntryLinksResponse(
            self_link=self_link,
            operations=f"{self_link}/operations",
            import_link=f"{self_link}:import",
            human=view.human_url,
            alternates=[build_link(request, f"/catalog/{alt}") for alt in view.alternate_ids],
        ),
    )


@router.get("/catalog", response_model=CatalogListResponse, response_model_by_alias=True)
async def list_catalog(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    permissions: frozenset[str] = Depends(effective_permissions),
    svc: CatalogService = Depends(get_catalog_service),
    q: str | None = Query(
        default=None,
        max_length=500,
        description=(
            "Search text matched against each entry's `catalog_api_id`; results are ranked "
            "by match quality."
        ),
        openapi_examples=ex.example("charges"),
    ),
    registered_only: bool = Query(
        default=False,
        description="Only return entries already imported into the registry.",
        openapi_examples=ex.example(True),
    ),
    unregistered_only: bool = Query(
        default=False,
        description=(
            "Only return entries not yet imported; cannot be combined with `registered_only` "
            "or `outdated_only`."
        ),
        openapi_examples=ex.example(False),
    ),
    outdated_only: bool = Query(
        default=False,
        description="Only return imported entries with an upstream update available.",
        openapi_examples=ex.example(True),
    ),
    include_snoozed: bool = Query(
        default=False,
        description="Treat entries whose update notification is snoozed as outdated.",
        openapi_examples=ex.example(True),
    ),
    cursor: str | None = Query(
        default=None,
        description=(
            "Opaque cursor from a previous page's `next_cursor`; keep the other filters "
            "unchanged while paging."
        ),
        openapi_examples=ex.example(ex.CURSOR),
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of entries to return (1-200).",
        openapi_examples=ex.example(25),
    ),
    include_alternates: bool = Query(
        default=False,
        description=(
            "Return every visible entry instead of collapsing entries that share a "
            "`catalog_api_id` into one."
        ),
        openapi_examples=ex.example(False),
    ),
) -> JSONResponse:
    """List a keyset page of the catalog entries visible to the caller.

    The catalog holds thousands of entries, so this is cursor-paginated like
    ``GET /apis``: follow ``next_cursor`` until ``has_more`` is false. Visible
    entries sharing a ``catalog_api_id`` collapse into one (registered first,
    then source precedence) unless ``include_alternates`` is set.
    ``catalog_total``/``registered_count``/``outdated_count`` count the visible
    collapsed list before filters and paging, so the status row stays stable.
    """
    if registered_only and unregistered_only:
        raise ProblemDetailException(
            status_code=422,
            detail="registered_only and unregistered_only are mutually exclusive",
            type="mutually_exclusive_filters",
            instance="/catalog",
        )
    if outdated_only and unregistered_only:
        # Outdated ⊆ registered, so this pair can only ever return an empty page. Reject it
        # as a contradiction rather than silently yielding nothing.
        raise ProblemDetailException(
            status_code=422,
            detail="outdated_only and unregistered_only are mutually exclusive",
            type="mutually_exclusive_filters",
            instance="/catalog",
        )

    try:
        page = await svc.list_all(
            permissions=permissions,
            actor_id=identity.sub or None,
            include_alternates=include_alternates,
            q=q,
            registered_only=registered_only,
            unregistered_only=unregistered_only,
            outdated_only=outdated_only,
            cursor=cursor,
            limit=limit,
            include_snoozed=include_snoozed,
        )
    except InvalidCursorError:
        raise BadRequest(detail="Invalid pagination cursor", instance="/catalog") from None

    resp = CatalogListResponse(
        data=[_entry_response(request, v) for v in page.data],
        catalog_total=page.catalog_total,
        registered_count=page.registered_count,
        outdated_count=page.outdated_count,
        oldest_source_sync_age_seconds=page.oldest_source_sync_age_seconds,
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get(
    "/catalog/{entry_id:path}/operations",
    response_model=OperationPreviewListResponse,
    response_model_by_alias=True,
    responses=conflict("The catalog entry has no spec (card-only)."),
)
async def preview_catalog_operations(
    entry_id: EntryIdPath,
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    permissions: frozenset[str] = Depends(effective_permissions),
    svc: CatalogService = Depends(get_catalog_service),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of matching operations to skip.",
        openapi_examples=ex.example(0),
    ),
    limit: int = Query(
        default=200,
        ge=0,
        le=200,
        description="Maximum number of operations to return (0-200).",
        openapi_examples=ex.example(25),
    ),
    tag: str | None = Query(
        default=None,
        description="Only return operations with a tag containing this text (case-insensitive).",
        openapi_examples=ex.example("Charges"),
    ),
    q: str | None = Query(
        default=None,
        max_length=500,
        description=(
            "Only return operations whose method, path, summary or operation ID contains "
            "this text (case-insensitive)."
        ),
        openapi_examples=ex.example("charges"),
    ),
) -> JSONResponse:
    """Preview the operations of a catalog entry's spec (capped, offset-paginated).

    ``tag`` and ``q`` filter the spec's operations server-side before windowing,
    so the UI's search box covers every operation in the spec and pages the
    filtered set via ``offset``/``limit`` ("Load more").
    """
    preview = await svc.preview(
        _checked_entry_id(entry_id),
        permissions=permissions,
        actor_id=identity.sub or None,
        offset=offset,
        limit=limit,
        tag=tag,
        q=q,
    )
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
        server_variables=preview.server_variables,
        required_schemes=preview.required_schemes,
    )
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get(
    "/catalog/{entry_id:path}", response_model=CatalogEntryResponse, response_model_by_alias=True
)
async def get_catalog_entry(
    request: Request,
    entry_id: EntryIdPath,
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    permissions: frozenset[str] = Depends(effective_permissions),
    svc: CatalogService = Depends(get_catalog_service),
) -> JSONResponse:
    """Retrieve a single visible catalog entry by ``entry_id``.

    A hidden or unknown id is a ``404``; when the id looks like a bare
    ``catalog_api_id`` the body's ``did_you_mean`` names the visible entry for it.
    """
    view = await svc.get(
        _checked_entry_id(entry_id), permissions=permissions, actor_id=identity.sub or None
    )
    resp = _entry_response(request, view)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.post("/catalog:refresh", status_code=200, response_model=CatalogRefreshResponse)
async def refresh_catalog(
    identity: Identity = get_current_identity(required_permissions=["catalog:sync"]),
    permissions: frozenset[str] = Depends(effective_permissions),
    svc: CatalogService = Depends(get_catalog_service),
    force: bool = Query(
        default=False,
        description=(
            "Apply each source's sync even when it would remove more entries than "
            "the source's mass-removal guard allows (`org:admin`)."
        ),
        openapi_examples=ex.example(False),
    ),
) -> JSONResponse:
    """Sync now every enabled catalog source the caller may read (catalog:sync).

    Each source's listing fetch is bounded by ``ingest.fetch_timeout_s`` and all
    of them together by ``catalog.refresh_deadline_seconds``. ``status`` is
    ``partial`` when a refreshed source failed or was skipped; ``sources``
    gives each one's outcome. Sources the caller cannot read are not synced or
    reported. ``force=true`` (``org:admin``) overrides the mass-removal guard
    for this run.
    """
    if force and ORG_ADMIN not in permissions:
        raise ForcedSyncForbiddenError()
    result = await svc.refresh(permissions=permissions, actor_id=identity.sub or None, force=force)
    # An explicit operator "refresh now" is the strongest "check upstream" signal,
    # so trigger the update-notify sweep too. Fire-and-forget: never block the
    # refresh response on it.
    svc.trigger_update_notify_sweep()
    resp = CatalogRefreshResponse(
        count=result.count,
        status="partial" if result.partial else "refreshed",
        sources=[
            CatalogRefreshSourceResponse(slug=o.slug, status=o.status, error_code=o.error_code)
            for o in result.sources
        ],
    )
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.post(
    "/catalog/{entry_id:path}:import",
    status_code=202,
    response_model=ApiImportResponse,
    response_model_by_alias=True,
    responses=conflict("The catalog entry has no spec (card-only)."),
)
async def import_catalog_entry(
    request: Request,
    entry_id: EntryIdPath,
    identity: Identity = get_current_identity(required_permissions=["catalog:import"]),
    svc: CatalogService = Depends(get_catalog_service),
    switch_source: bool = Query(
        default=False,
        description=(
            "When another catalog entry's import already holds this entry's "
            "`catalog_api_id`, supersede that API with this entry's spec (its "
            "credentials then point at it). Needs `credentials:write`; without it the "
            "import takes a distinct name and leaves that API untouched."
        ),
        openapi_examples=ex.example(False),
    ),
) -> JSONResponse:
    """Enqueue an async import of a visible catalog entry into the local registry."""
    job_id = await svc.import_entry(
        _checked_entry_id(entry_id), identity, switch_source=switch_source
    )
    resp = ApiImportResponse(
        job_id=job_id,
        status="queued",
        links=ApiImportLinksResponse(self_link=build_link(request, f"/jobs/{job_id}")),
    )
    return JSONResponse(status_code=202, content=resp.model_dump(by_alias=True))


@router.post(
    "/catalog/{entry_id:path}:snooze",
    status_code=204,
    responses=conflict("The catalog entry has no outstanding update to snooze."),
)
async def snooze_catalog_entry(
    entry_id: EntryIdPath,
    body: CatalogSnoozeRequest | None = None,
    identity: Identity = get_current_identity(required_permissions=["events:write"]),
    svc: CatalogService = Depends(get_catalog_service),
) -> Response:
    """Snooze the outstanding update notification for a catalog entry.

    Operator action (``events:write``): quiet the "update available" badge for a
    known-and-accepted upstream change without adopting it. Body is optional — omit or send
    ``{"snoozed_until": null}`` to mute until a newer upstream digest lands (the primary
    per-API affordance); send an ISO-8601 ``snoozed_until`` for a time-boxed snooze. A
    genuinely newer upstream change re-lights the badge automatically.
    """
    await svc.snooze_entry(
        _checked_entry_id(entry_id), identity, until=body.snoozed_until if body else None
    )
    return Response(status_code=204)


@router.post("/catalog/{entry_id:path}:unsnooze", status_code=204)
async def unsnooze_catalog_entry(
    entry_id: EntryIdPath,
    identity: Identity = get_current_identity(required_permissions=["events:write"]),
    svc: CatalogService = Depends(get_catalog_service),
) -> Response:
    """Clear a snooze for a catalog entry. Operator action (``events:write``)."""
    await svc.unsnooze_entry(_checked_entry_id(entry_id), identity)
    return Response(status_code=204)
