"""Public ``GET /instance`` — a self-describing backend-identity surface.

When a hosted (``remote``) Jentic install and a ``local`` self-hosted one are
both reachable from the same machine, a client (an MCP server, the CLI, an
agent) can be pointed at either one. Nothing in a normal tool response says
*which* backend replied, so a caller draws false conclusions ("APIs
disappeared", "credentials vanished") when the two systems are simply different
backends.

This endpoint gives any client a cheap, unauthenticated way to read the
identity of the backend it is talking to, so it can label its responses and a
human/agent can tell local from remote at a glance. It intentionally exposes
only non-sensitive identity metadata: the operator-declared ``backend`` locality
(``server.backend``), the instance's own canonical base URL / host (from
``auth.canonical_base_url``) and, when telemetry has resolved one, the opaque
telemetry ``instance_id``. All are values the operator already set or that the
instance advertises about itself — no secrets.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from jentic_one.shared.context import Context

INSTANCE_PATH = "/instance"


class InstanceIdentityResponse(BaseModel):
    """Self-describing identity of the backend serving this request.

    A client can compare ``backend``/``canonical_base_url``/``host`` against where
    it *thinks* it is pointed to confirm it is talking to the intended backend
    (e.g. a local install vs. a remote one) before diagnosing "missing" data.
    """

    backend: str = Field(
        description=(
            "Operator-declared backend locality (server.backend): 'local' for a "
            "self-hosted install on the operator's own machine/network, 'remote' for "
            "a hosted install run elsewhere. A hint, not an authorization signal; "
            "defaults to 'local'."
        )
    )
    canonical_base_url: str = Field(
        description="The instance's own canonical base URL (auth.canonical_base_url); '' if unset."
    )
    host: str = Field(description="Host (with port) parsed from canonical_base_url; '' if unset.")
    instance_id: str | None = Field(
        default=None,
        description="Opaque telemetry instance id if telemetry has resolved one, else null.",
    )


def resolve_instance_identity(ctx: Context) -> InstanceIdentityResponse:
    """Build the backend-identity payload from the live application ``Context``."""
    canonical_base_url = ctx.config.auth.canonical_base_url or ""
    host = urlsplit(canonical_base_url).netloc if canonical_base_url else ""
    return InstanceIdentityResponse(
        backend=ctx.config.server.backend,
        canonical_base_url=canonical_base_url,
        host=host,
        instance_id=ctx.instance_id,
    )


def get_instance_router() -> APIRouter:
    """Router exposing the public backend-identity endpoint (``GET /instance``)."""
    router = APIRouter()

    @router.get(
        INSTANCE_PATH,
        operation_id="getInstance",
        summary="Backend identity",
        tags=["System"],
        response_model=InstanceIdentityResponse,
    )
    async def instance(request: Request) -> InstanceIdentityResponse:
        """Return this backend's self-describing identity.

        Unauthenticated and dependency-free so any client (an MCP server, the
        CLI, an agent) can read which backend it is bound to — local vs. a
        remote install — and label its responses accordingly.
        """
        ctx: Context = request.app.state.ctx
        return resolve_instance_identity(ctx)

    return router
