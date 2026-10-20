"""Per-actor rate limits for the vendor-calling connect endpoints.

``POST /integrations:connect`` and ``POST /connect-sessions/{id}:confirm``
both end up firing the vendor's device-authorization / authorization endpoint
(``:confirm`` is where the device-flow ``begin`` actually happens, and a failed
``begin`` rolls the session back to ``created``, so it is retryable in a tight
loop). A ``credentials:write`` caller that spams either endpoint can get the
platform IP throttled by GitHub / Google / etc., which would fail-fast every
legitimate user on the same install. One per-actor bucket caps both, well below
any vendor's own throttle: ``CONNECT_RPM`` sustained requests per minute,
``CONNECT_BURST`` bucket capacity for a short spike. The MCP
``request_connection`` tool calls the connect-session service in-process, so it
carries its own bucket with the same policy.

Both limiters run on the process's shared-state backend (``state:``,
``Context.state_backend``): per process on ``memory``, cluster-wide on a
shared backend.
"""

from __future__ import annotations

from fastapi import FastAPI

from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state import RateLimitStore

CONNECT_RPM = 30
CONNECT_BURST = 10

ROUTE_LIMITER_ATTR = "integrations_connect_limiter"
MCP_LIMITER_ATTR = "mcp_connect_limiter"
_BACKEND_ATTR = "control_state_backend"
_NAMESPACES = {
    ROUTE_LIMITER_ATTR: "integrations_connect",
    MCP_LIMITER_ATTR: "mcp_integrations_connect",
}


def _limiter(store: RateLimitStore, namespace: str) -> RateLimiter:
    return RateLimiter(store, default_rpm=CONNECT_RPM, burst=CONNECT_BURST, namespace=namespace)


def install_connect_limiters(app: FastAPI, ctx: Context) -> None:
    """Install both connect limiters on the process's shared-state backend.

    The backend is the process's one instance; the app lifespan closes it
    (``ctx.close_state_backend()``).
    """
    backend = ctx.state_backend
    setattr(app.state, _BACKEND_ATTR, backend)
    for attr, namespace in _NAMESPACES.items():
        setattr(app.state, attr, _limiter(backend, namespace))


def connect_limiter(app: FastAPI, attr: str) -> RateLimiter:
    """Return the installed limiter ``attr`` from ``app.state``.

    Raises:
        RuntimeError: The app was assembled without
            :func:`install_connect_limiters`. A private in-process limiter
            would silently not share its buckets with the process's
            shared-state backend, so a missing one is a wiring error.
    """
    limiter: RateLimiter | None = getattr(app.state, attr, None)
    if limiter is None:
        raise RuntimeError(
            f"the control connect limiter {attr!r} is not installed on this app; "
            "assemble it with install_connect_limiters(app, ctx), which builds it on "
            "the process's shared-state backend (Context.state_backend)"
        )
    return limiter


__all__ = [
    "CONNECT_BURST",
    "CONNECT_RPM",
    "MCP_LIMITER_ATTR",
    "ROUTE_LIMITER_ATTR",
    "connect_limiter",
    "install_connect_limiters",
]
