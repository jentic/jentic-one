"""Governed-hosts router — identity-scoped host digest for integrators (#1278)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse

from jentic_one.registry.services.governed_hosts_service import GovernedHostsService
from jentic_one.registry.web.deps import get_governed_hosts_service
from jentic_one.registry.web.schemas.governed_hosts import GovernedHostsResponse
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity

router = APIRouter()

# The body is identity-scoped, so a URL-keyed shared cache must never store or
# serve it — and the ETag would otherwise make heuristic caching *more* likely.
_RESPONSE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Vary": "Authorization",
}

_ETAG_HEADER_SPEC = {
    "description": (
        'Strong entity tag: the quoted `digest` (`"<hex>"`). '
        "Echo it in `If-None-Match` to change-poll."
    ),
    "schema": {"type": "string"},
}


def _etag_matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 ``If-None-Match`` comparison (weak comparison, ``*`` honoured).

    The digest ETag is content-derived and strong, but clients may echo it back
    weakened (``W/"…"``) through caches, so compare opaque-tags only. As a
    compatibility arm, the bare (unquoted) digest is accepted too — the body's
    ``digest`` field is what integrators actually hold, and rejecting the
    obvious ``If-None-Match: <digest>`` form would silently disable the poll
    seam with no error.
    """
    candidates = [v.strip() for v in if_none_match.split(",")]
    if "*" in candidates:
        return True
    opaque = {v.removeprefix("W/") for v in candidates}
    return etag in opaque or etag.strip('"') in opaque


@router.get(
    "/governed-hosts",
    response_model=GovernedHostsResponse,
    responses={
        200: {"headers": {"ETag": _ETAG_HEADER_SPEC}},
        304: {
            "description": (
                "The host set still matches the presented `If-None-Match` — "
                "empty body, `ETag` echoed."
            ),
            "headers": {"ETag": _ETAG_HEADER_SPEC},
        },
    },
)
async def get_governed_hosts(
    if_none_match: Annotated[
        str | None,
        Header(
            description=(
                "Change-poll precondition: the `ETag` from a previous response "
                '(quoted, `"<digest>"`; the bare digest is accepted as a '
                "compatibility form). When it still matches, the response is "
                "an empty `304`."
            )
        ),
    ] = None,
    identity: Identity = get_current_identity(
        required_permissions=["toolkits:read", "owner:toolkits:read"]
    ),
    svc: GovernedHostsService = Depends(get_governed_hosts_service),
) -> Response:
    """The caller's governed host set (toolkit-bound hosts) with an ETag digest.

    **Always self-scoped** — derived from the authenticated identity's own
    toolkit bindings; there is no cross-actor or admin variant. Toolkits bind
    to agents, so agent-scoped tokens (the OAuth agent-consent flow's output)
    are the callers this endpoint serves — a plain user token yields an empty
    set. The ``digest`` covers exactly the ``data`` list and is also emitted as
    a strong ``ETag``, so integrators poll with ``If-None-Match: "<digest>"``
    and get an empty ``304`` until their host set actually changes (the
    change-poll seam that replaces ``GET /apis`` enumeration for interception
    scoping). Poll at most once per minute; on any ``5xx`` retain the last
    known set — never fall back to an empty (intercept-nothing) list.

    Responses are identity-scoped and marked ``Cache-Control: private,
    no-store`` — a shared cache must never serve one actor's host set to
    another.
    """
    view = await svc.get_governed_hosts(identity)
    etag = f'"{view.digest}"'
    headers = {"ETag": etag, **_RESPONSE_HEADERS}

    if if_none_match is not None and _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)

    resp = GovernedHostsResponse(data=list(view.hosts), digest=view.digest)
    return JSONResponse(content=resp.model_dump(mode="json"), headers=headers)
