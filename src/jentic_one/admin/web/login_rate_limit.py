"""Pre-auth rate limiting for ``POST /auth/login``.

Two token buckets gate every login attempt before any credential work runs:

* **per caller IP** — bounds password spraying across accounts from one host;
* **per account email** — bounds a guessing burst against one account spread
  over many hosts (the failed-login lockout caps the total, this caps the rate).

The IP bucket is spent first, so a host already throttled cannot also drain a
victim's email bucket. Email keys are SHA-256 digests of the normalised address
so a Redis-backed deployment never stores raw emails in its keyspace.

Buckets live on the app's shared-state backend: the auth surface's
``auth_state_backend`` when it is installed (combined deploys), otherwise the
backend the standalone admin app builds at startup. Exhausted buckets raise a
``429`` RFC 9457 problem with ``Retry-After``.
"""

from __future__ import annotations

import hashlib

import structlog
from fastapi import FastAPI, Request
from jentic.problem_details import TooManyRequests

from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.resilience.rate_limit import RateLimitOutcome
from jentic_one.shared.state import build_state_backend
from jentic_one.shared.state.backend import MemoryStateBackend, SharedStateBackend
from jentic_one.shared.web.client_ip import client_ip

logger = structlog.get_logger(__name__)

#: ``app.state`` attribute holding the shared pre-auth state backend. The auth
#: surface installs it in combined deploys; the standalone admin app installs
#: its own via :func:`install_login_state_backend`.
_STATE_BACKEND_ATTR = "auth_state_backend"


def install_login_state_backend(app: FastAPI, ctx: Context) -> None:
    """Give a standalone admin app the shared-state backend its login limiter needs.

    No-op when a backend is already installed. The backend follows
    ``broker.resilience.backend`` (the same selection the auth surface uses) and
    is closed on shutdown.
    """
    if isinstance(getattr(app.state, _STATE_BACKEND_ATTR, None), SharedStateBackend):
        return
    backend = build_state_backend(ctx.config.broker.resilience.backend)
    setattr(app.state, _STATE_BACKEND_ATTR, backend)

    async def _close_login_backend() -> None:
        await backend.aclose()

    app.router.add_event_handler("shutdown", _close_login_backend)


def _get_backend(request: Request) -> SharedStateBackend:
    backend: object = getattr(request.app.state, _STATE_BACKEND_ATTR, None)
    if isinstance(backend, SharedStateBackend):
        return backend
    logger.warning("login_rate_limit_backend_missing — using a per-process in-memory backend")
    return MemoryStateBackend()


def _get_limiters(request: Request, ctx: Context) -> tuple[RateLimiter, RateLimiter]:
    limiters: tuple[RateLimiter, RateLimiter] | None = getattr(
        request.app.state, "_admin_login_limiters", None
    )
    if limiters is not None:
        return limiters
    cfg = ctx.config.admin.auth.login_rate_limit
    backend = _get_backend(request)
    # Own namespaces: the auth surface's bare-IP limiters share this store, and
    # a login burst must not drain the /authorize quota (or vice versa).
    limiters = (
        RateLimiter(
            backend, default_rpm=cfg.ip_rpm, burst=cfg.ip_burst, namespace="admin-login-ip"
        ),
        RateLimiter(
            backend,
            default_rpm=cfg.email_rpm,
            burst=cfg.email_burst,
            namespace="admin-login-email",
        ),
    )
    request.app.state._admin_login_limiters = limiters
    return limiters


def _email_key(email: str) -> str:
    # Normalised the way the user lookup matches (case-insensitive), so case
    # variants of one address share a bucket.
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _deny(request: Request, outcome: RateLimitOutcome, *, bucket: str) -> TooManyRequests:
    logger.warning("login_rate_limited", bucket=bucket, retry_after_s=outcome.retry_after_s)
    return TooManyRequests(
        detail="Too many login attempts; retry after the indicated delay.",
        type="rate_limit_exceeded",
        instance=request.url.path,
        headers={**outcome.headers(), "Retry-After": str(outcome.retry_after_s)},
    )


async def enforce_login_rate_limit(request: Request, ctx: Context, *, email: str) -> None:
    """Spend one token from the caller-IP and account-email buckets, or raise 429."""
    if not ctx.config.admin.auth.login_rate_limit.enabled:
        return
    ip_limiter, email_limiter = _get_limiters(request, ctx)
    trusted = ctx.config.auth.oauth_rate_limit.trusted_networks
    ip_outcome = await ip_limiter.acquire(client_ip(request, trusted))
    if not ip_outcome.allowed:
        raise _deny(request, ip_outcome, bucket="ip")
    email_outcome = await email_limiter.acquire(_email_key(email))
    if not email_outcome.allowed:
        raise _deny(request, email_outcome, bucket="email")


__all__ = ["enforce_login_rate_limit", "install_login_state_backend"]
