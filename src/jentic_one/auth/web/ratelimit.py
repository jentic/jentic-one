"""Shared helpers for the auth surface's pre-auth rate limiters.

Shared so the anonymous DCR registration router and /authorize derive the
caller IP and reach the shared-state backend the same way, without
cross-router imports of underscore-private names.
"""

from __future__ import annotations

from fastapi import Request

from jentic_one.shared.state.backend import SharedStateBackend
from jentic_one.shared.web.auth_state import get_auth_state_backend
from jentic_one.shared.web.client_ip import client_ip


def get_auth_backend(request: Request) -> SharedStateBackend:
    """Return the app's shared auth-state backend (rate limits, consent handles)."""
    return get_auth_state_backend(request)


__all__ = ["client_ip", "get_auth_backend"]
