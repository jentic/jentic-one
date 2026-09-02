"""Shared helpers for the auth surface's pre-auth rate limiters.

Promoted from ``authorize.py`` (which previously owned them as privates) so
the anonymous DCR registration router and /authorize derive the caller IP and
reach the shared-state backend the same way, without cross-router imports of
underscore-private names.
"""

from __future__ import annotations

import structlog
from fastapi import Request

from jentic_one.shared.state.backend import MemoryStateBackend, SharedStateBackend

logger = structlog.get_logger(__name__)


def get_auth_backend(request: Request) -> SharedStateBackend:
    """Return the app's shared auth-state backend (rate limits, consent handles)."""
    backend: object = getattr(request.app.state, "auth_state_backend", None)
    if isinstance(backend, SharedStateBackend):
        return backend
    logger.warning("auth_state_backend missing from app.state, using in-memory fallback")
    return MemoryStateBackend()


def client_ip(request: Request, trusted_proxies: frozenset[str]) -> str:
    """Extract the real client IP, honoring XFF only from trusted reverse proxies."""
    socket_ip = request.client.host if request.client else "unknown"
    if not trusted_proxies or socket_ip not in trusted_proxies:
        return socket_ip
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return socket_ip
    hops = [h.strip() for h in forwarded.split(",")]
    for hop in reversed(hops):
        if hop not in trusted_proxies:
            return hop
    return socket_ip


__all__ = ["client_ip", "get_auth_backend"]
