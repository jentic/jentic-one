"""Shared helpers for the auth surface's pre-auth rate limiters.

Shared so the anonymous DCR registration router and /authorize derive the
caller IP and
reach the shared-state backend the same way, without cross-router imports of
underscore-private names.
"""

from __future__ import annotations

import structlog
from fastapi import Request

from jentic_one.shared.state.backend import MemoryStateBackend, SharedStateBackend
from jentic_one.shared.web.client_ip import client_ip

logger = structlog.get_logger(__name__)


def get_auth_backend(request: Request) -> SharedStateBackend:
    """Return the app's shared auth-state backend (rate limits, consent handles)."""
    backend: object = getattr(request.app.state, "auth_state_backend", None)
    if isinstance(backend, SharedStateBackend):
        return backend
    logger.warning("auth_state_backend missing from app.state, using in-memory fallback")
    return MemoryStateBackend()


__all__ = ["client_ip", "get_auth_backend"]
