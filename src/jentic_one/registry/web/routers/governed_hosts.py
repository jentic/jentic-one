"""Governed-hosts router — identity-scoped host digest for integrators (#1278)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from jentic_one.registry.services.governed_hosts_service import GovernedHostsService
from jentic_one.registry.web.deps import get_governed_hosts_service
from jentic_one.registry.web.schemas.governed_hosts import GovernedHostsResponse
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity

router = APIRouter()


def _etag_matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 ``If-None-Match`` comparison (weak comparison, ``*`` honoured).

    The digest ETag is content-derived and strong, but clients may echo it back
    weakened (``W/"…"``) through caches, so compare opaque-tags only.
    """
    candidates = [v.strip() for v in if_none_match.split(",")]
    if "*" in candidates:
        return True
    return etag in {v.removeprefix("W/") for v in candidates}


@router.get("/governed-hosts", response_model=GovernedHostsResponse)
async def get_governed_hosts(
    request: Request,
    identity: Identity = get_current_identity(
        required_permissions=["toolkits:read", "owner:toolkits:read"]
    ),
    svc: GovernedHostsService = Depends(get_governed_hosts_service),
) -> Response:
    """The caller's governed host set (toolkit-bound hosts) with an ETag digest.

    **Always self-scoped** — derived from the authenticated identity's own
    toolkit bindings; there is no cross-actor variant. Toolkits bind to agents
    and toolkit keys, so those are the callers this endpoint serves — a plain
    user token yields an empty set. The ``digest`` covers exactly the ``data``
    list and is also emitted as a strong ``ETag``, so integrators poll with
    ``If-None-Match`` and get an empty ``304`` until their host set actually
    changes (the change-poll seam that replaces ``GET /apis`` enumeration for
    interception scoping).
    """
    view = await svc.get_governed_hosts(identity)
    etag = f'"{view.digest}"'

    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None and _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})

    resp = GovernedHostsResponse(data=list(view.hosts), digest=view.digest)
    return JSONResponse(content=resp.model_dump(mode="json"), headers={"ETag": etag})
