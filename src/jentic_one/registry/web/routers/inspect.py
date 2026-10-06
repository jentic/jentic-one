"""Inspect router — resolve operations to full structural detail."""

from __future__ import annotations

import uuid as uuid_mod
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from jentic.problem_details import BadRequest, ProblemDetailException

from jentic_one.registry.services.errors import OperationNotFoundError
from jentic_one.registry.services.inspect.formatters.markdown import render_markdown
from jentic_one.registry.services.inspect.formatters.openapi import render_openapi_yaml
from jentic_one.registry.services.inspect.models import SUMMARY_LOAD_OPTIONS, OperationInspectResult
from jentic_one.registry.services.inspect.service import InspectService
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.registry.web.content_negotiation import accepted_media_types
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_ctx, get_current_identity

router = APIRouter()

_SUPPORTED_MEDIA_TYPES = {"application/json", "text/markdown", "application/openapi+yaml"}


@router.get("/inspect", summary="Inspect operation")
async def inspect_operation(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    id: str | None = Query(None, description="METHOD URL identifier"),
    operation_id: str | None = Query(None, description="Operation ID"),
    revision_id: str | None = Query(None, description="Pin to specific revision"),
    detail: Literal["summary", "full"] = Query("summary"),
) -> Response:
    """Inspect an operation — resolve to full structural detail.

    `api.vendor`/`api.name`/`api.version` in the result is the canonical API
    reference: the identity to use in credential scopes, revision pins and
    other API references. `api.display_name` (optional) is a human-readable
    label only.
    """
    if id and operation_id:
        raise BadRequest(
            detail="Provide exactly one of 'id' or 'operation_id', not both",
            instance="/inspect",
        )
    if not id and not operation_id:
        raise BadRequest(
            detail="Provide exactly one of 'id' or 'operation_id'",
            instance="/inspect",
        )

    if detail == "full":
        raise ProblemDetailException(
            status_code=501,
            detail="detail=full is not yet supported; use detail=summary",
            type="not_implemented",
            instance="/inspect",
        )

    base_url = str(request.base_url).rstrip("/")
    async with ctx.registry_db.session() as session:
        if id:
            parts = id.split(" ", 1)
            if len(parts) != 2:
                raise BadRequest(
                    detail="'id' must be in the form 'METHOD URL'",
                    instance="/inspect",
                )
            method, url = parts

            rev_id: uuid_mod.UUID | None = None
            if revision_id:
                try:
                    rev_id = uuid_mod.UUID(revision_id)
                except ValueError:
                    raise BadRequest(
                        detail="Invalid revision_id format",
                        instance="/inspect",
                    ) from None

            lookup_svc = URLLookupService(session)
            lookup_result = await lookup_svc.resolve(method=method, url=url, revision_id=rev_id)
            if lookup_result is None:
                raise OperationNotFoundError(id)

            svc = InspectService(session, base_url=base_url)
            result = await svc.inspect(
                operation_id=lookup_result.operation_id,
                method=method,
                url=url,
                load_options=SUMMARY_LOAD_OPTIONS,
            )
        else:
            assert operation_id is not None
            svc = InspectService(session, base_url=base_url)
            result = await svc.inspect_by_id(
                operation_id=operation_id,
                load_options=SUMMARY_LOAD_OPTIONS,
            )

    accept = accepted_media_types(request)
    return _negotiate_response(accept, result)


def _negotiate_response(accept: list[str], result: OperationInspectResult) -> Response:
    """Return the response in the best matching format."""

    for media_type in accept:
        if media_type in ("application/json", "*/*"):
            return JSONResponse(content=result.model_dump(mode="json", by_alias=True))
        if media_type == "text/markdown":
            return Response(content=render_markdown(result), media_type="text/markdown")
        if media_type == "application/openapi+yaml":
            return Response(
                content=render_openapi_yaml(result),
                media_type="application/openapi+yaml",
            )

    if not accept or accept == ["*/*"]:
        return JSONResponse(content=result.model_dump(mode="json", by_alias=True))

    raise ProblemDetailException(
        status_code=406,
        detail=f"Unsupported media type; supported: {', '.join(_SUPPORTED_MEDIA_TYPES)}",
        type="not_acceptable",
        instance="/inspect",
    )
