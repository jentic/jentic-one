"""Shared helpers for the auth surface's rate limiters.

Shared so every anonymous auth route (``/authorize`` and the rest of the
authorization flow, the local-login form, DCR registration, the
approval-status poll, the token and revocation endpoints, the
session-continue exchange) derives the caller's identity and reaches the
shared-state backend the same way, without cross-router imports of
underscore-private names.

A pre-auth limiter keys on what the caller cannot choose freely: its network
address. Any request parameter (``client_id``, a signed blob, a token) is
caller-chosen before it has been verified, so adding it to the key would hand
a caller a fresh bucket per value. The token endpoint is the one exception: it
prefixes the body ``client_id`` to the network, so one host running many
machine clients does not squeeze them all into one bucket.
"""

from __future__ import annotations

from fastapi import Request

from jentic_one.shared.state.backend import SharedStateBackend
from jentic_one.shared.web.ratelimit import (
    IPV6_CLIENT_PREFIX,
    client_ip,
    client_network,
    client_rate_limit_key,
)


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


__all__ = [
    "IPV6_CLIENT_PREFIX",
    "client_ip",
    "client_network",
    "client_rate_limit_key",
    "get_auth_backend",
]
