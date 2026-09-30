"""Shared link-builder utilities for consistent absolute URLs in API responses."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import Request

from jentic_one.shared.config import AppConfig, bind_origin, effective_auth_base_url
from jentic_one.shared.url import is_loopback_host


def build_link(request: Request, path: str) -> str:
    """Return an absolute URL for the given path rooted at the request's base URL."""
    return str(request.base_url).rstrip("/") + "/" + path.lstrip("/")


def _auth_request_origin(config: AppConfig, request: Request) -> str:
    """The request's base URL, with a direct loopback hit folded to ``bind_origin``.

    ``localhost:8000`` and ``127.0.0.1:8000`` reach the same process, but the
    request-less JWT-Bearer audience and ``id_token`` issuer are built from
    ``bind_origin``. Folding a direct loopback request (same port, no gateway
    path prefix) onto it keeps the discovery ``issuer`` / ``token_endpoint``
    byte-identical to what token validation expects, whichever alias the client
    used. Any other request (a gateway, a port mapping) keeps its own origin.
    """
    base = str(request.base_url).rstrip("/")
    parts = urlsplit(base)
    try:
        port = parts.port
    except ValueError:  # pragma: no cover - Starlette already parsed the Host
        return base
    if (
        parts.scheme == "http"
        and not parts.path
        and port == config.server.port
        and is_loopback_host(parts.hostname or "")
    ):
        return bind_origin(config)
    return base


def deployment_base_url(config: AppConfig, request: Request) -> str:
    """Deployment base URL for the auth surface's request-scoped links.

    Resolution order: ``auth.canonical_base_url`` → ``server.public_base_url``
    → the incoming request's origin (a direct loopback hit folded onto
    ``bind_origin``). Single home for the rule shared by the OAuth discovery
    documents (issuer, token endpoint), the agent-discovery documents (llms.txt
    links) and the MCP mount, so they can never disagree. With nothing
    configured a local deployment on any port is self-consistent; behind a
    gateway the operator sets one of the two config knobs.
    """
    return effective_auth_base_url(config) or _auth_request_origin(config, request)


def public_base_url(config: AppConfig, request: Request) -> str:
    """The deployment's public origin: ``server.public_base_url``, else the request's.

    Used for URLs a *browser* must reach on this deployment that are not owned by
    the auth surface (e.g. the credential-connect OAuth callback), so an
    ``auth.canonical_base_url`` override fronting the auth surface on another
    origin does not redirect them. The exact request origin is kept (no loopback
    folding) so the connect popup lands on the SPA's own origin. The fallback
    trusts the ``Host`` header; operators behind a TLS-terminating proxy pin
    ``server.public_base_url``.
    """
    return config.server.public_base_url or str(request.base_url).rstrip("/")
