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


def _build_overlay_response(view: OverlayView, request: Request) -> OverlayResponse:
    api_path = build_link(request, f"/apis/{view.vendor}/{view.name}/{view.version}")
    self_link = build_link(
        request, f"/apis/{view.vendor}/{view.name}/{view.version}/overlays/{view.id}"
    )
    confirm_link = f"{self_link}:confirm" if view.status == OverlayStatus.PENDING else None

    return OverlayResponse(
        id=view.id,
        api_id=str(view.api_id),
        status=view.status,
        document=view.document,
        target_revision_id=str(view.target_revision_id) if view.target_revision_id else None,
        confirmed_revision_id=(
            str(view.confirmed_revision_id) if view.confirmed_revision_id else None
        ),
        contributed_by=view.contributed_by,
        confirmed_by_execution_id=view.confirmed_by_execution_id,
        created_at=view.created_at,
        updated_at=view.updated_at,
        confirmed_at=view.confirmed_at,
        deprecated_at=view.deprecated_at,
        links=OverlayLinksResponse(
            self_link=self_link,
            api=api_path,
            confirm=confirm_link,
        ),
    )


def _build_overlay_list_item(
    item: OverlayPageItem, request: Request, vendor: str, name: str, version: str
) -> OverlayResponse:
    api_path = build_link(request, f"/apis/{vendor}/{name}/{version}")
    self_link = build_link(request, f"/apis/{vendor}/{name}/{version}/overlays/{item.id}")
    confirm_link = f"{self_link}:confirm" if item.status == OverlayStatus.PENDING else None

    return OverlayResponse(
        id=item.id,
        api_id=str(item.api_id),
        status=item.status,
        document=item.document,
        target_revision_id=str(item.target_revision_id) if item.target_revision_id else None,
        confirmed_revision_id=(
            str(item.confirmed_revision_id) if item.confirmed_revision_id else None
        ),
        contributed_by=item.contributed_by,
        confirmed_by_execution_id=item.confirmed_by_execution_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        confirmed_at=item.confirmed_at,
        deprecated_at=item.deprecated_at,
        links=OverlayLinksResponse(
            self_link=self_link,
            api=api_path,
            confirm=confirm_link,
        ),
    )


@router.post("", status_code=201)
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


@router.get("")
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


@router.get("/{overlay_id}")
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


@router.patch("/{overlay_id}")
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
    """Update an overlay's document or target revision."""
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


@router.post("/{overlay_id}:confirm")
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
