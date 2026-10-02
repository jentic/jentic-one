"""Notes router — CRUD endpoints for registry resource annotations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from jentic.problem_details import BadRequest

from jentic_one.registry.services.note_service import UNSET, NoteService, NoteView
from jentic_one.registry.web.schemas.notes import (
    NoteApiReference,
    NoteCreateRequest,
    NoteLinksResponse,
    NoteListResponse,
    NoteResourceResponse,
    NoteResponse,
    NoteUpdateRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web import get_ctx, get_current_identity
from jentic_one.shared.web.links import build_link

router = APIRouter(prefix="/notes")

# Notes routes stay on bare ``get_current_identity()`` (any authenticated caller)
# by design: there is no ``notes:*`` scope in the
# catalogue, and access is governed by ownership rather than a scope. Notes carry
# ``created_by`` and the service layer scopes rows via ``build_access_filters``
# (``registry/services/note_service.py``), so a caller only ever sees its own
# notes. Adding a route-level scope here would gate on a permission that does not
# exist; the ownership model in the service is the enforcement boundary.


def _parse_if_match(header: str | None, *, instance: str) -> int | None:
    if header is None:
        return None
    stripped = header.strip().strip('"')
    try:
        return int(stripped)
    except ValueError:
        raise BadRequest(
            detail="If-Match header must be a numeric revision",
            instance=instance,
        ) from None


def _build_links(request: Request, view: NoteView) -> NoteLinksResponse:
    self_link = build_link(request, f"/notes/{view.id}")
    resource_link: str | None = None
    if view.resource_operation_id:
        resource_link = build_link(request, f"/inspect?operation_id={view.resource_operation_id}")
    elif view.resource_api_id and view.resource_api_vendor:
        resource_link = build_link(
            request,
            f"/apis/{view.resource_api_vendor}/{view.resource_api_name}/{view.resource_api_version}",
        )
    return NoteLinksResponse(self_link=self_link, resource=resource_link)


def _build_resource(view: NoteView) -> NoteResourceResponse:
    api: NoteApiReference | None = None
    if view.resource_api_id and view.resource_api_vendor:
        api = NoteApiReference(
            vendor=view.resource_api_vendor,
            name=view.resource_api_name or "",
            version=view.resource_api_version or "",
        )
    return NoteResourceResponse(
        api=api,
        operation_id=view.resource_operation_id,
        execution_id=view.resource_execution_id,
        credential_id=view.resource_credential_id,
    )


def _build_response(request: Request, view: NoteView) -> NoteResponse:
    return NoteResponse(
        note_id=view.id,
        resource=_build_resource(view),
        type=view.type,  # type: ignore[arg-type]
        body=view.body,
        confidence=view.confidence,  # type: ignore[arg-type]
        confidence_source=view.confidence_source,
        source=view.source,  # type: ignore[arg-type]
        created_by=view.created_by,
        related_execution_id=view.related_execution_id,
        revision=view.revision,
        created_at=view.created_at,
        # Spec marks `updated_at` required & non-nullable: "when last edited
        # (or created, if never edited)". Fall back to created_at for unedited notes.
        updated_at=view.updated_at if view.updated_at is not None else view.created_at,
        links=_build_links(request, view),
    )


@router.post("", status_code=201)
async def create_note(
    request: Request,
    body: NoteCreateRequest,
    identity: Identity = get_current_identity(),
    ctx: Context = Depends(get_ctx),
) -> JSONResponse:
    """Create a new note attached to a registry resource."""
    svc = NoteService(ctx)
    resource_api = None
    if body.resource.api is not None:
        resource_api = (
            body.resource.api.vendor,
            body.resource.api.name,
            body.resource.api.version,
        )
    view = await svc.create(
        resource_api=resource_api,
        resource_operation_id=body.resource.operation_id,
        resource_execution_id=body.resource.execution_id,
        resource_credential_id=body.resource.credential_id,
        type=body.type.value if body.type is not None else None,
        body=body.body,
        confidence=body.confidence.value if body.confidence is not None else None,
        source=body.source.value if body.source is not None else None,
        identity=identity,
        related_execution_id=body.related_execution_id,
    )

    resp = _build_response(request, view)
    return JSONResponse(status_code=201, content=resp.model_dump(mode="json", by_alias=True))


@router.get("")
async def list_notes(
    request: Request,
    identity: Identity = get_current_identity(),
    ctx: Context = Depends(get_ctx),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    api: str | None = None,
    operation_id: str | None = None,
    execution_id: str | None = None,
    credential_id: str | None = None,
    type: str | None = None,
    created_by: str | None = None,
) -> JSONResponse:
    """List notes with optional filters and cursor pagination."""
    svc = NoteService(ctx)
    try:
        page = await svc.list_page(
            limit=limit,
            cursor=cursor,
            api=api,
            operation_id=operation_id,
            execution_id=execution_id,
            credential_id=credential_id,
            type=type,
            created_by=created_by,
            identity=identity,
        )
    except InvalidCursorError:
        raise BadRequest(
            detail="Invalid pagination cursor",
            instance=request.url.path,
        ) from None

    data = [_build_response(request, item) for item in page.data]
    resp = NoteListResponse(data=data, has_more=page.has_more, next_cursor=page.next_cursor)
    return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))


@router.get("/{note_id}")
async def get_note(
    request: Request,
    note_id: str,
    identity: Identity = get_current_identity(),
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Retrieve a single note by ID."""
    svc = NoteService(ctx)
    view = await svc.get(note_id, identity=identity)

    resp = _build_response(request, view)
    content = resp.model_dump(mode="json", by_alias=True)
    return JSONResponse(
        status_code=200,
        content=content,
        headers={"ETag": f'"{view.revision}"'},
    )


@router.patch("/{note_id}")
async def update_note(
    request: Request,
    note_id: str,
    body: NoteUpdateRequest,
    identity: Identity = get_current_identity(),
    ctx: Context = Depends(get_ctx),
    if_match: str | None = Header(default=None, alias="if-match"),
) -> Response:
    """Update a note (partial). Supports optimistic concurrency via If-Match."""
    revision = _parse_if_match(if_match, instance=request.url.path)

    svc = NoteService(ctx)

    kwargs: dict[str, Any] = {}
    if "body" in body.model_fields_set:
        kwargs["body"] = body.body
    if "type" in body.model_fields_set:
        kwargs["type"] = body.type.value if body.type is not None else None
    else:
        kwargs["type"] = UNSET
    if "confidence" in body.model_fields_set:
        kwargs["confidence"] = body.confidence.value if body.confidence is not None else None
    else:
        kwargs["confidence"] = UNSET
    if "source" in body.model_fields_set:
        kwargs["source"] = body.source.value if body.source is not None else None
    else:
        kwargs["source"] = UNSET
    if "related_execution_id" in body.model_fields_set:
        kwargs["related_execution_id"] = body.related_execution_id
    else:
        kwargs["related_execution_id"] = UNSET

    view = await svc.update(note_id, if_match=revision, identity=identity, **kwargs)

    resp = _build_response(request, view)
    content = resp.model_dump(mode="json", by_alias=True)
    return JSONResponse(
        status_code=200,
        content=content,
        headers={"ETag": f'"{view.revision}"'},
    )


@router.delete("/{note_id}", status_code=204)
async def delete_note(
    request: Request,
    note_id: str,
    identity: Identity = get_current_identity(),
    ctx: Context = Depends(get_ctx),
    if_match: str | None = Header(default=None, alias="if-match"),
) -> Response:
    """Delete a note. Supports optimistic concurrency via If-Match."""
    revision = _parse_if_match(if_match, instance=request.url.path)

    svc = NoteService(ctx)
    await svc.delete(note_id, if_match=revision, identity=identity)
    return Response(status_code=204)
