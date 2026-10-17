"""Shared helpers for the auth surface's rate limiters.

Shared so every auth router (the token endpoint, the anonymous DCR
registration router and /authorize) reaches the shared-state backend the same
way, and the last two derive the caller IP the same way, without cross-router
imports of underscore-private names.
"""

from __future__ import annotations

from fastapi import Request

from jentic_one.shared.state.backend import SharedStateBackend


def get_auth_backend(request: Request) -> SharedStateBackend:
    """Return the app's shared auth-state backend (rate limits, consent handles).

    Raises:
        RuntimeError: The app was assembled without the auth surface's
            ``install_on_app``, which sets ``app.state.auth_state_backend`` to
            the process's shared-state backend. A private per-request backend
            would hold no limit or single-use marker at all, so a missing one
            is a wiring error, never a fallback.
    """
    backend: object = getattr(request.app.state, "auth_state_backend", None)
    if isinstance(backend, SharedStateBackend):
        return backend
    raise RuntimeError(
        "app.state.auth_state_backend is not set; assemble the auth surface with "
        "its install_on_app, which wires the process's shared-state backend "
        "(Context.state_backend)"
    )


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
