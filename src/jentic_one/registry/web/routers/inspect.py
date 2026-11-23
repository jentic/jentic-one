"""Inspect router — resolve operations to full structural detail."""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any, Final, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from jentic.problem_details import BadRequest, ProblemDetailException

from jentic_one.registry.services.inspect.formatters.markdown import render_markdown
from jentic_one.registry.services.inspect.formatters.openapi import render_openapi_yaml
from jentic_one.registry.services.inspect.models import SUMMARY_LOAD_OPTIONS, OperationInspectResult
from jentic_one.registry.services.inspect.service import InspectService
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.registry.web.content_negotiation import accepted_media_types
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.discovery.errors import OperationNotFoundError
from jentic_one.shared.web import get_ctx, get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.openapi_responses import problem

router = APIRouter()

_SUPPORTED_MEDIA_TYPES = {"application/json", "text/markdown", "application/openapi+yaml"}


#: The handler negotiates one of three renderings of the same result, so the
#: ``200`` declares all three: the structured JSON document, and the two
#: text renderings built from it.
_INSPECT_200: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": (
            "The resolved operation, rendered in the format negotiated from `Accept`. "
            "`application/json` (the default, and what `*/*` or an absent `Accept` gets) "
            "returns the structured result below. `text/markdown` returns the same result "
            "as a human-readable Markdown page, and `application/openapi+yaml` as a "
            "single-operation OpenAPI document — both are plain text, with no JSON "
            "envelope. Any other requested media type answers `406`."
        ),
        "model": OperationInspectResult,
        "content": {
            "text/markdown": {
                "schema": {
                    "type": "string",
                    "description": "The operation rendered as a Markdown page.",
                }
            },
            "application/openapi+yaml": {
                "schema": {
                    "type": "string",
                    "description": (
                        "The operation rendered as a single-operation OpenAPI YAML document."
                    ),
                }
            },
        },
    },
    406: problem(
        "The `Accept` header asked for a media type this endpoint cannot render. It serves "
        "`application/json`, `text/markdown` and `application/openapi+yaml` only (plus `*/*` "
        "and an absent header, which both get JSON). Nothing is resolved: re-request with one "
        "of those three."
    ),
}


@router.get("/inspect", summary="Inspect operation", responses=_INSPECT_200)
async def inspect_operation(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["apis:read"]),
    ctx: Context = Depends(get_ctx),
    id: str | None = Query(
        None,
        description="METHOD URL identifier",
        openapi_examples=ex.example(ex.OPERATION_METHOD_URL),
    ),
    operation_id: str | None = Query(
        None, description="Operation ID", openapi_examples=ex.example(ex.OPERATION_ID)
    ),
    revision_id: str | None = Query(
        None, description="Pin to specific revision", openapi_examples=ex.example(ex.REVISION_ID)
    ),
    detail: Literal["summary", "full"] = Query(
        "summary",
        description="Level of detail to return; only `summary` is currently supported.",
        openapi_examples=ex.example("summary"),
    ),
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
