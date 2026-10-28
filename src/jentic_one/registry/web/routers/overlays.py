"""Overlays router — submission, retrieval, and lifecycle endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from jentic.problem_details import BadRequest

from jentic_one.registry.services.overlay_service import (
    OverlayPageItem,
    OverlayService,
    OverlayView,
)
from jentic_one.registry.web.params import ApiNamePath, ApiVendorPath, ApiVersionPath
from jentic_one.registry.web.schemas.overlays import (
    OverlayConfirmRequest,
    OverlayLinksResponse,
    OverlayListResponse,
    OverlayResponse,
    OverlaySubmitRequest,
    OverlayUpdateRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import OverlayStatus
from jentic_one.shared.web import get_ctx, get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.links import build_link

router = APIRouter(prefix="/apis/{vendor}/{name}/{version}/overlays")


def _overlay_links(
    self_link: str,
    api_path: str,
    *,
    status: str,
    confirmed_revision_id: object | None,
) -> OverlayLinksResponse:
    """Build the state-valid action links for an overlay (see OverlayLinksResponse)."""
    is_pending = status == OverlayStatus.PENDING
    is_materialized = status == OverlayStatus.CONFIRMED and confirmed_revision_id is not None
    is_deprecated = status == OverlayStatus.DEPRECATED
    return OverlayLinksResponse(
        self_link=self_link,
        api=api_path,
        confirm=f"{self_link}:confirm" if is_pending else None,
        rollback=f"{self_link}:rollback" if is_materialized else None,
        deprecate=self_link if not is_deprecated else None,
    )


def _build_overlay_response(view: OverlayView, request: Request) -> OverlayResponse:
    api_path = build_link(request, f"/apis/{view.vendor}/{view.name}/{view.version}")
    self_link = build_link(
        request, f"/apis/{view.vendor}/{view.name}/{view.version}/overlays/{view.id}"
    )

    return OverlayResponse(
        id=view.id,
        api_id=str(view.api_id),
        status=view.status,
        document=view.document,
        target_revision_id=str(view.target_revision_id) if view.target_revision_id else None,
        confirmed_revision_id=(
            str(view.confirmed_revision_id) if view.confirmed_revision_id else None
        ),
        superseded_revision_id=(
            str(view.superseded_revision_id) if view.superseded_revision_id else None
        ),
        contributed_by=view.contributed_by,
        created_by=view.created_by,
        confirmed_by_execution_id=view.confirmed_by_execution_id,
        created_at=view.created_at,
        updated_at=view.updated_at,
        confirmed_at=view.confirmed_at,
        deprecated_at=view.deprecated_at,
        deprecated_reason=view.deprecated_reason,
        links=_overlay_links(
            self_link,
            api_path,
            status=view.status,
            confirmed_revision_id=view.confirmed_revision_id,
        ),
    )


def _build_overlay_list_item(
    item: OverlayPageItem, request: Request, vendor: str, name: str, version: str
) -> OverlayResponse:
    api_path = build_link(request, f"/apis/{vendor}/{name}/{version}")
    self_link = build_link(request, f"/apis/{vendor}/{name}/{version}/overlays/{item.id}")

    return OverlayResponse(
        id=item.id,
        api_id=str(item.api_id),
        status=item.status,
        document=item.document,
        target_revision_id=str(item.target_revision_id) if item.target_revision_id else None,
        confirmed_revision_id=(
            str(item.confirmed_revision_id) if item.confirmed_revision_id else None
        ),
        superseded_revision_id=(
            str(item.superseded_revision_id) if item.superseded_revision_id else None
        ),
        contributed_by=item.contributed_by,
        created_by=item.created_by,
        confirmed_by_execution_id=item.confirmed_by_execution_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        confirmed_at=item.confirmed_at,
        deprecated_at=item.deprecated_at,
        deprecated_reason=item.deprecated_reason,
        links=_overlay_links(
            self_link,
            api_path,
            status=item.status,
            confirmed_revision_id=item.confirmed_revision_id,
        ),
    )


@router.post(
    "", status_code=201, response_model=OverlayResponse, summary="Submit an OpenAPI overlay"
)
async def submit_overlay(
    request: Request,
    vendor: ApiVendorPath,
    name: ApiNamePath,
    version: ApiVersionPath,
    body: OverlaySubmitRequest,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Propose a change to an API's spec as an OpenAPI Overlay document.

    An overlay is a list of `actions` that patch the API's OpenAPI document —
    for example correcting a description or adding a missing parameter.
    Submitting stores it as `pending`; it changes nothing until an operator
    confirms it (`:confirm`). Optionally pin it to a `target_revision_id` and
    record a `contributed_by` label. The document must be a JSON object with
    an `actions` array (at most 512 actions, 1 MB), else 422
    (`invalid_overlay_document`). Requires `apis:write`. Returns the overlay.
    """
    target_revision_id: uuid.UUID | None = None
    if body.target_revision_id is not None:
        try:
            target_revision_id = uuid.UUID(body.target_revision_id)
        except ValueError:
            raise BadRequest(
                detail="Invalid target_revision_id format",
                instance=request.url.path,
            ) from None

    svc = OverlayService(ctx)
    view = await svc.submit(
        vendor,
        name,
        version,
        document=body.document,
        target_revision_id=target_revision_id,
        contributed_by=body.contributed_by,
        identity=identity,
    )

    resp = _build_overlay_response(view, request)
    return JSONResponse(status_code=201, content=resp.model_dump(mode="json", by_alias=True))


@router.get("", response_model=OverlayListResponse, summary="List an API's overlays")
async def list_overlays(
    request: Request,
    vendor: ApiVendorPath,
    name: ApiNamePath,
    version: ApiVersionPath,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    cursor: str | None = Query(
        default=None,
        description="Opaque cursor from a previous page's `next_cursor`; omit for the first page.",
        openapi_examples=ex.example(ex.CURSOR),
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of overlays to return (1-200).",
        openapi_examples=ex.example(25),
    ),
    status: str | None = Query(
        default=None,
        description="Only return overlays in this status: `pending`, `confirmed` or `deprecated`.",
        openapi_examples=ex.example("confirmed"),
    ),
) -> JSONResponse:
    """List the overlays submitted against an API, newest first.

    Each overlay gives its `status` — `pending` (awaiting review), `confirmed`
    (applied to the served spec) or `deprecated` (retired) — and links to the
    actions valid in that state. Filter with `status`. Results are
    cursor-paginated: pass `next_cursor` as `cursor` while `has_more` is
    true.
    """
    svc = OverlayService(ctx)
    page = await svc.list_page(vendor, name, version, limit=limit, cursor=cursor, status=status)

    data = [_build_overlay_list_item(item, request, vendor, name, version) for item in page.data]

    resp = OverlayListResponse(data=data, has_more=page.has_more, next_cursor=page.next_cursor)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get("/{overlay_id}", response_model=OverlayResponse)
async def get_overlay(
    request: Request,
    vendor: ApiVendorPath,
    name: ApiNamePath,
    version: ApiVersionPath,
    overlay_id: Annotated[
        str, Path(description="ID of the overlay.", openapi_examples=ex.example(ex.OVERLAY_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Retrieve a single overlay by ID."""
    svc = OverlayService(ctx)
    view = await svc.get(vendor, name, version, overlay_id)

    resp = _build_overlay_response(view, request)
    return JSONResponse(status_code=200, content=resp.model_dump(mode="json", by_alias=True))


@router.patch("/{overlay_id}", response_model=OverlayResponse, summary="Edit an overlay")
async def update_overlay(
    request: Request,
    vendor: ApiVendorPath,
    name: ApiNamePath,
    version: ApiVersionPath,
    overlay_id: Annotated[
        str, Path(description="ID of the overlay.", openapi_examples=ex.example(ex.OVERLAY_ID))
    ],
    body: OverlayUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Change an overlay's `document` or `target_revision_id`.

    What the edit does depends on the overlay's state:

    - **Pending** (or confirmed but not yet applied): the stored overlay is
      updated; the served spec is untouched. Requires `apis:write`.
    - **Confirmed and live**: the edited document is re-applied over the
      overlay's original base and the result replaces the API's served spec.
      This also requires `overlays:confirm`, else 403
      (`overlay_rematerialize_forbidden`). It is refused with 409 if the
      overlay is no longer the live revision, or
      (`overlay_rollback_target_missing`) if no clean base was recorded.

    Returns the updated overlay.
    """
    target_revision_id: uuid.UUID | None = None
    if body.target_revision_id is not None:
        try:
            target_revision_id = uuid.UUID(body.target_revision_id)
        except ValueError:
            raise BadRequest(
                detail="Invalid target_revision_id format",
                instance=request.url.path,
            ) from None

    svc = OverlayService(ctx)
    view = await svc.update(
        vendor,
        name,
        version,
        overlay_id,
        document=body.document,
        target_revision_id=target_revision_id,
        identity=identity,
    )

    resp = _build_overlay_response(view, request)
    return JSONResponse(status_code=200, content=resp.model_dump(mode="json", by_alias=True))


@router.delete("/{overlay_id}", status_code=204, summary="Deprecate an overlay")
async def deprecate_overlay(
    vendor: ApiVendorPath,
    name: ApiNamePath,
    version: ApiVersionPath,
    overlay_id: Annotated[
        str, Path(description="ID of the overlay.", openapi_examples=ex.example(ex.OVERLAY_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Retire an overlay so it is no longer offered for confirmation.

    The overlay moves to `deprecated` but is kept, and remains readable for
    history; nothing is deleted. Deprecating does not rewrite the served
    spec: to undo a confirmed overlay's change, use `:rollback`. Requires
    `apis:write`. Returns 204.
    """
    svc = OverlayService(ctx)
    await svc.deprecate(vendor, name, version, overlay_id, identity=identity)
    return Response(status_code=204)


@router.post(
    "/{overlay_id}:confirm",
    response_model=OverlayResponse,
    summary="Confirm an overlay and apply it to the served spec",
)
async def confirm_overlay(
    request: Request,
    vendor: ApiVendorPath,
    name: ApiNamePath,
    version: ApiVersionPath,
    overlay_id: Annotated[
        str, Path(description="ID of the overlay.", openapi_examples=ex.example(ex.OVERLAY_ID))
    ],
    body: OverlayConfirmRequest,
    identity: Identity = get_current_identity(required_permissions=["overlays:confirm"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Apply a pending overlay, producing a new current revision of the API.

    The base spec is re-imported with the overlay applied and the result is
    promoted to the API's current revision, so it is what the API now serves.
    The overlay becomes `confirmed`. Confirming an already-applied overlay is
    a no-op that returns it unchanged; a deprecated overlay is refused with
    409 (`overlay_conflict`), and an overlay whose actions cannot be applied
    with 409 (`overlay_apply_conflict`).

    Requires `overlays:confirm` (implied by `org:admin`): contributors submit
    overlays with `apis:write`, and an operator reviews and confirms them.
    Agents cannot grant themselves this permission. Returns the overlay.
    """
    svc = OverlayService(ctx)
    view = await svc.confirm(
        vendor, name, version, overlay_id, execution_id=body.execution_id, identity=identity
    )

    resp = _build_overlay_response(view, request)
    return JSONResponse(status_code=200, content=resp.model_dump(mode="json", by_alias=True))


@router.post(
    "/{overlay_id}:rollback",
    status_code=204,
    summary="Roll back a confirmed overlay",
)
async def rollback_overlay(
    vendor: ApiVendorPath,
    name: ApiNamePath,
    version: ApiVersionPath,
    overlay_id: Annotated[
        str, Path(description="ID of the overlay.", openapi_examples=ex.example(ex.OVERLAY_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["overlays:confirm"]),
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Undo a confirmed overlay by restoring the revision it replaced.

    The overlay's revision is archived and the revision it superseded becomes
    the API's current revision again, so the served spec reverts, and the
    overlay is marked `deprecated`. Requires `overlays:confirm`, like
    confirming.

    Preconditions: the overlay is `confirmed`, its revision is still the
    live one, and the revision it replaced is still restorable. Otherwise
    nothing changes and 409 is returned: `overlay_conflict`,
    `overlay_rollback_target_missing`, or `host_owned_by_other_vendor` when
    another vendor's live API now serves one of the restored revision's
    hosts. Returns 204.
    """
    svc = OverlayService(ctx)
    await svc.rollback(vendor, name, version, overlay_id, identity=identity)
    return Response(status_code=204)
