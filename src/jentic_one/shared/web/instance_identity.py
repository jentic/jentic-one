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
(``server.backend``) and the instance's own canonical base URL / host (from
``auth.canonical_base_url``, with any userinfo stripped before echoing). The
``instance_id`` is a one-way digest *derived from* the telemetry instance id —
distinct installs get distinct values, but the durable telemetry identifier
itself is never published.
"""

from __future__ import annotations

import hashlib
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from jentic_one.shared.config import effective_auth_base_url
from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import get_ctx

INSTANCE_PATH = "/instance"

# Domain-separation prefix so the published digest can't double as a lookup key
# into anything else keyed by the raw telemetry instance id.
_INSTANCE_ID_DIGEST_PREFIX = "jentic-instance-identity:"
_INSTANCE_ID_DIGEST_LENGTH = 16


class InstanceIdentityResponse(BaseModel):
    """Self-describing identity of the backend serving this request.

    A client can compare ``backend``/``canonical_base_url``/``host`` against where
    it *thinks* it is pointed to confirm it is talking to the intended backend
    (e.g. a local install vs. a remote one) before diagnosing "missing" data.
    """

    backend: Literal["local", "remote"] = Field(
        description=(
            "Operator-declared backend locality (server.backend): 'local' for a "
            "self-hosted install on the operator's own machine/network, 'remote' for "
            "a hosted install run elsewhere. A hint, not an authorization signal; "
            "defaults to 'local'."
        )
    )
    canonical_base_url: str = Field(
        description=(
            "The instance's own canonical base URL (auth.canonical_base_url), with "
            "any userinfo stripped; '' if unset."
        )
    )
    host: str = Field(
        description=(
            "Host (and port, when the canonical base URL declares one) parsed from "
            "canonical_base_url; '' if unset or unparseable."
        )
    )
    instance_id: str | None = Field(
        default=None,
        description=(
            "Opaque digest derived from the telemetry instance id — stable per "
            "install, but not the telemetry id itself. Null when telemetry has not "
            "resolved an id (e.g. telemetry disabled)."
        ),
    )


def _public_instance_id(instance_id: str | None) -> str | None:
    """Derive the publishable instance id digest from the telemetry id."""
    if instance_id is None:
        return None
    digest = hashlib.sha256((_INSTANCE_ID_DIGEST_PREFIX + instance_id).encode("utf-8"))
    return digest.hexdigest()[:_INSTANCE_ID_DIGEST_LENGTH]


def _sanitized_url_parts(canonical_base_url: str) -> tuple[str, str]:
    """Return ``(canonical_base_url, host)`` with any userinfo stripped.

    ``urlsplit().netloc`` retains ``user:password@`` userinfo, so both the echoed
    URL and the derived host are rebuilt from ``hostname``/``port`` to guarantee
    credentials embedded in ``auth.canonical_base_url`` are never published.
    """
    parts = urlsplit(canonical_base_url)
    host = parts.hostname or ""
    if host and parts.port is not None:
        host = f"{host}:{parts.port}"
    if parts.username is not None or parts.password is not None:
        canonical_base_url = urlunsplit(
            (parts.scheme, host, parts.path, parts.query, parts.fragment)
        )
    return canonical_base_url, host


def resolve_instance_identity(ctx: Context) -> InstanceIdentityResponse:
    """Build the backend-identity payload from the live application ``Context``."""
    canonical_base_url = effective_auth_base_url(ctx.config)
    canonical_base_url, host = (
        _sanitized_url_parts(canonical_base_url) if canonical_base_url else ("", "")
    )
    return InstanceIdentityResponse(
        backend=ctx.config.server.backend,
        canonical_base_url=canonical_base_url,
        host=host,
        instance_id=_public_instance_id(ctx.instance_id),
    )


def get_instance_router() -> APIRouter:
    """Router exposing the public backend-identity endpoint (``GET /instance``)."""
    router = APIRouter()

    @router.get(
        INSTANCE_PATH,
        operation_id="getInstance",
        summary="Backend identity",
        response_model=InstanceIdentityResponse,
    )
    async def instance(ctx: Context = Depends(get_ctx)) -> InstanceIdentityResponse:
        """Return this backend's self-describing identity.

        Unauthenticated and dependency-free so any client (an MCP server, the
        CLI, an agent) can read which backend it is bound to — local vs. a
        remote install — and label its responses accordingly.
        """
        return resolve_instance_identity(ctx)

    return router
