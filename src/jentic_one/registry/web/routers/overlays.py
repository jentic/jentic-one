"""Overlays router — submission, retrieval, and lifecycle endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from jentic.problem_details import BadRequest

from jentic_one.registry.services.overlay_service import (
    OverlayPageItem,
    OverlayService,
    OverlayView,
)
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
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web import get_ctx, get_current_identity
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


@router.post("", status_code=201, response_model=OverlayResponse)
async def submit_overlay(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    body: OverlaySubmitRequest,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Submit a new overlay for an API."""
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


@router.get("", response_model=OverlayListResponse)
async def list_overlays(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
) -> JSONResponse:
    """List overlays for an API with optional status filter and cursor pagination."""
    svc = OverlayService(ctx)
    try:
        page = await svc.list_page(vendor, name, version, limit=limit, cursor=cursor, status=status)
    except InvalidCursorError:
        raise BadRequest(
            detail="Invalid pagination cursor",
            instance=request.url.path,
        ) from None

    data = [_build_overlay_list_item(item, request, vendor, name, version) for item in page.data]

    resp = OverlayListResponse(data=data, has_more=page.has_more, next_cursor=page.next_cursor)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get("/{overlay_id}", response_model=OverlayResponse)
async def get_overlay(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    overlay_id: str,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Retrieve a single overlay by ID."""
    svc = OverlayService(ctx)
    view = await svc.get(vendor, name, version, overlay_id)

    resp = _build_overlay_response(view, request)
    return JSONResponse(status_code=200, content=resp.model_dump(mode="json", by_alias=True))


@router.patch("/{overlay_id}", response_model=OverlayResponse)
async def update_overlay(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    overlay_id: str,
    body: OverlayUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Update an overlay's document or target revision.

    Editing a *pending* overlay (or a stuck CONFIRMED-but-unmaterialized one) is an
    ordinary contributor edit — ``apis:write``. Editing a *materialized* overlay
    (CONFIRMED and currently serving) instead **re-materializes** it: the edited document
    is re-applied over the overlay's original pre-overlay base and re-ingested, rewriting
    the API's served spec (D1). That is an operator action, so it additionally requires
    ``overlays:confirm``; a caller with only ``apis:write`` gets a 403
    (``overlay_rematerialize_forbidden``). The re-materialize is refused with a 409 if the
    overlay is no longer the live revision, and with ``overlay_rollback_target_missing``
    if no clean base was recorded to re-apply over.
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


@router.delete("/{overlay_id}", status_code=204)
async def deprecate_overlay(
    vendor: str,
    name: str,
    version: str,
    overlay_id: str,
    identity: Identity = get_current_identity(required_permissions=["apis:write"]),
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Deprecate an overlay (soft delete)."""
    svc = OverlayService(ctx)
    await svc.deprecate(vendor, name, version, overlay_id, identity=identity)
    return Response(status_code=204)


@router.post("/{overlay_id}:confirm", response_model=OverlayResponse)
async def confirm_overlay(
    request: Request,
    vendor: str,
    name: str,
    version: str,
    overlay_id: str,
    body: OverlayConfirmRequest,
    identity: Identity = get_current_identity(required_permissions=["overlays:confirm"]),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Confirm an overlay, materializing it onto the served spec.

    Requires ``overlays:confirm``: confirming rewrites the API's served spec (it
    re-ingests the base spec with the overlay applied and promotes the result to the
    current revision), so it is an operator action, not a contributor one (contributors
    ``submit`` overlays with ``apis:write``; an operator reviews and confirms). This is a
    purpose-scoped downgrade from the former ``org:admin`` gate — narrow enough that an
    owner can grant it to a trusted operator without handing over full admin power.
    ``org:admin`` still satisfies it (it implies ``overlays:confirm``), and the scope is
    deliberately excluded from an agent's self-service grantable set so a low-privilege
    agent cannot escalate into it.
    """
    svc = OverlayService(ctx)
    view = await svc.confirm(
        vendor, name, version, overlay_id, execution_id=body.execution_id, identity=identity
    )

    resp = _build_overlay_response(view, request)
    return JSONResponse(status_code=200, content=resp.model_dump(mode="json", by_alias=True))


@router.post("/{overlay_id}:rollback", status_code=204)
async def rollback_overlay(
    vendor: str,
    name: str,
    version: str,
    overlay_id: str,
    identity: Identity = get_current_identity(required_permissions=["overlays:confirm"]),
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Un-confirm a materialized overlay, restoring the revision it superseded (A5b).

    Requires ``overlays:confirm`` — the symmetric inverse of confirm. Rolling back
    rewrites the API's served spec (it archives the overlay's materialized revision and
    promotes the prior revision back to current), so it is the same operator action as
    confirm, not a contributor one. The overlay must be CONFIRMED, currently live, and
    carry a recorded superseded revision that is still restorable; otherwise a 409 is
    returned (``overlay_conflict`` or ``overlay_rollback_target_missing``) and nothing
    changes.
    """
    svc = OverlayService(ctx)
    await svc.rollback(vendor, name, version, overlay_id, identity=identity)
    return Response(status_code=204)
