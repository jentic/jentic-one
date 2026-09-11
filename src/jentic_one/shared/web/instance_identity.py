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
only non-sensitive identity/deployment metadata: the operator-declared
``backend`` locality (``server.backend``), the instance's own canonical base
URL / host (from ``auth.canonical_base_url``, with any userinfo stripped before
echoing), whether the ``/mcp`` endpoint is served, and the broker (data plane)
URL clients need for ``execute`` (``server.mcp.broker_url``, when honestly
advertisable — see ``_advertised_broker_url``). The ``instance_id`` is a
one-way digest *derived from* the telemetry instance id — distinct installs get
distinct values, but the durable telemetry identifier itself is never
published.
"""

from __future__ import annotations

import hashlib
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

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
    mcp_enabled: bool = Field(
        default=False,
        description=(
            "Whether this instance serves the daemon-native Streamable HTTP MCP "
            "endpoint at /mcp (server.mcp.enabled). Lets clients (notably the UI's "
            "agent MCP card) advertise the HTTP transport only when it exists. Not "
            "sensitive: the endpoint's enabled state is observable by probing /mcp "
            "anyway."
        ),
    )
    broker_url: str | None = Field(
        default=None,
        description=(
            "The broker (data plane) base URL a client should send `execute` traffic "
            "to, as configured by the operator (server.mcp.broker_url), with any "
            "userinfo stripped. Null when the platform cannot honestly advertise "
            "one: on a 'remote' backend a loopback-host value (the config default) "
            "describes the control plane's own machine, not an address any client "
            "can dial, so it is withheld rather than published as misleading "
            "guidance. Deployment metadata, not a secret — the broker URL is handed "
            "to every client expected to call it (issue #1249)."
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


def _is_loopback_host(hostname: str | None) -> bool:
    """``localhost`` or a literal loopback IP — parsed, never prefix-matched.

    Same semantics as the MCP execute proxy's guard (``mcp/execute.py``) and the
    Go client's ``isLoopbackHost``; kept local because ``shared.web`` must not
    import from the ``mcp`` module (shared is imported by every module).
    """
    if hostname is None:
        return False
    name = hostname.lower()
    if name == "localhost":
        return True
    try:
        return ip_address(name).is_loopback
    except ValueError:
        return False


def _advertised_broker_url(config_broker_url: str, backend: str) -> str | None:
    """The broker URL ``/instance`` may honestly advertise, or ``None``.

    ``server.mcp.broker_url`` is the address the CONTROL PLANE dials for its
    server-side execute proxy hop. It is the best broker pointer the platform
    has, so it is republished for clients (issue #1249) — except when doing so
    would mislead: on a ``remote`` backend a loopback host (notably the config
    default ``http://127.0.0.1:8100``) names the control plane's own machine,
    which no remote client can dial, so ``None`` (→ the UI keeps its
    ``<broker-url>`` placeholder + "ask your operator" fallback). On a
    ``local`` backend the loopback default is exactly the address a client on
    that machine should use. Userinfo is stripped before echoing, mirroring
    ``canonical_base_url``.
    """
    if not config_broker_url:
        return None
    parts = urlsplit(config_broker_url)
    if backend == "remote" and _is_loopback_host(parts.hostname):
        return None
    sanitized, _host = _sanitized_url_parts(config_broker_url)
    return sanitized


def resolve_instance_identity(ctx: Context) -> InstanceIdentityResponse:
    """Build the backend-identity payload from the live application ``Context``."""
    canonical_base_url = ctx.config.auth.canonical_base_url or ""
    canonical_base_url, host = (
        _sanitized_url_parts(canonical_base_url) if canonical_base_url else ("", "")
    )
    return InstanceIdentityResponse(
        backend=ctx.config.server.backend,
        canonical_base_url=canonical_base_url,
        host=host,
        instance_id=_public_instance_id(ctx.instance_id),
        mcp_enabled=ctx.config.server.mcp.enabled,
        broker_url=_advertised_broker_url(
            ctx.config.server.mcp.broker_url, ctx.config.server.backend
        ),
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
