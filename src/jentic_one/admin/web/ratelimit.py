"""Rate limiting for unauthenticated admin auth endpoints (#1441).

``POST /auth/login`` runs before any token exists, so its limiter keys solely
on the caller's client network (IPv4 address or IPv6 ``/64``, via
:func:`~jentic_one.shared.web.ratelimit.client_rate_limit_key`). Caller-chosen
body fields such as ``email`` never enter the bucket key: including ``email``
would let one host spray distinct addresses into fresh buckets, while
per-account brute-force across distributed networks is bounded separately by
the atomic failed-login lockout counter in ``AuthService.authenticate``.
"""

from __future__ import annotations

import structlog
from fastapi import Depends, Request
from jentic.problem_details import TooManyRequests

from jentic_one.admin.services.metrics import login_counter
from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state.backend import SharedStateBackend
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.ratelimit import client_rate_limit_key

logger = structlog.get_logger(__name__)

ADMIN_STATE_BACKEND_ATTR = "admin_state_backend"
LOGIN_RATE_LIMIT_NAMESPACE = "admin-auth-login"


def get_admin_backend(request: Request) -> SharedStateBackend:
    """Return the app's shared admin-state backend.

    Raises:
        RuntimeError: The app was assembled without the admin surface's
            ``install_on_app``, which sets ``app.state.admin_state_backend`` to
            the process's shared-state backend. A private per-request backend
            would enforce no cross-request limit, so a missing one is a wiring
            error, never a fallback.
    """
    backend: object = getattr(request.app.state, ADMIN_STATE_BACKEND_ATTR, None)
    if isinstance(backend, SharedStateBackend):
        return backend
    raise RuntimeError(
        "app.state.admin_state_backend is not set; assemble the admin surface with "
        "its install_on_app, which wires the process's shared-state backend "
        "(Context.state_backend)"
    )


def _get_login_limiter(request: Request, ctx: Context) -> RateLimiter:
    cfg = ctx.config.auth.oauth_rate_limit
    backend = get_admin_backend(request)
    cached: tuple[SharedStateBackend, int, int, RateLimiter] | None = getattr(
        request.app.state, "_login_limiter", None
    )
    if (
        cached is not None
        and cached[0] is backend
        and cached[1] == cfg.authorize_rpm
        and cached[2] == cfg.authorize_burst
    ):
        return cached[3]
    limiter = RateLimiter(
        backend,
        default_rpm=cfg.authorize_rpm,
        burst=cfg.authorize_burst,
        namespace=LOGIN_RATE_LIMIT_NAMESPACE,
    )
    request.app.state._login_limiter = (
        backend,
        cfg.authorize_rpm,
        cfg.authorize_burst,
        limiter,
    )
    return limiter


async def check_login_rate_limit(
    request: Request,
    ctx: Context = Depends(get_ctx),
) -> None:
    """Enforce the per-client-network rate limit ahead of ``POST /auth/login``."""
    cfg = ctx.config.auth.oauth_rate_limit
    key = client_rate_limit_key(request, cfg.trusted_networks)
    limiter = _get_login_limiter(request, ctx)
    outcome = await limiter.acquire(key)
    if outcome.allowed:
        return
    retry_after_s = max(1, outcome.retry_after_s)
    login_counter.add(1, {"outcome": "rate_limited"})
    logger.warning("login_rate_limited", path=request.url.path, retry_after_s=retry_after_s)
    raise TooManyRequests(
        detail="Too many login attempts; slow down and retry after the indicated delay.",
        type="rate_limit_exceeded",
        instance=request.url.path,
        headers={**outcome.headers(), "Retry-After": str(retry_after_s)},
    )


__all__ = [
    "ADMIN_STATE_BACKEND_ATTR",
    "LOGIN_RATE_LIMIT_NAMESPACE",
    "check_login_rate_limit",
    "get_admin_backend",
]
