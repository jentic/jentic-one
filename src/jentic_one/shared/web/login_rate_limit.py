"""Pre-auth rate limiting for the password-login doors.

Both doors that verify an account password — ``POST /auth/login`` (admin
surface) and the local-login form submit on the ``/authorize`` flow (auth
surface) — call :func:`enforce_login_rate_limit` before any credential work, so
the two buckets below hold whichever door an attacker picks:

* **per caller IP** — bounds password spraying across accounts from one host.
  IPv6 callers are bucketed per ``/64`` so one subscriber cannot rotate
  addresses to mint fresh buckets;
* **per account email** — bounds a guessing burst against one account spread
  over many hosts (the failed-login lockout caps the total, this caps the rate).

The IP bucket is spent first, so a host already throttled cannot also drain a
victim's email bucket. Email keys are SHA-256 digests of the normalised address
so a Redis-backed deployment never stores raw emails in its keyspace.

Buckets live on the app's pre-auth shared-state backend
(:mod:`jentic_one.shared.web.auth_state`). Exhausted buckets raise a ``429``
RFC 9457 problem with ``Retry-After`` and count an ``outcome=rate_limited``
login.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import structlog
from fastapi import Request
from jentic.problem_details import TooManyRequests

from jentic_one.shared.auth.login_metrics import login_counter
from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.resilience.rate_limit import RateLimitOutcome
from jentic_one.shared.web.auth_state import get_auth_state_backend
from jentic_one.shared.web.client_ip import client_ip, rate_limit_ip_key

logger = structlog.get_logger(__name__)

#: Detail of every login ``429`` (also the OpenAPI example).
LOGIN_RATE_LIMIT_DETAIL = "Too many login attempts; retry after the indicated delay."

#: Per-process key for the ``key_ref`` log field: lets an operator correlate
#: repeated denials of one bucket within a process without the log carrying a
#: raw IP or email (or an unkeyed hash an IPv4 sweep would reverse).
_LOG_KEY = secrets.token_bytes(16)


def _get_limiters(request: Request, ctx: Context) -> tuple[RateLimiter, RateLimiter]:
    cfg = ctx.config.admin.auth.login_rate_limit
    # Cached per effective config: a changed limit (reload, test override)
    # rebuilds the wrappers; the buckets themselves live in the backend.
    params = (cfg.ip_rpm, cfg.ip_burst, cfg.email_rpm, cfg.email_burst)
    cached: tuple[tuple[int, int, int, int], tuple[RateLimiter, RateLimiter]] | None = getattr(
        request.app.state, "_login_limiters", None
    )
    if cached is not None and cached[0] == params:
        return cached[1]
    backend = get_auth_state_backend(request)
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
    request.app.state._login_limiters = (params, limiters)
    return limiters


def _email_key(email: str) -> str:
    # Normalised the way the user lookup matches (case-insensitive), so case
    # and whitespace variants of one address share a bucket.
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _key_ref(key: str) -> str:
    return hmac.new(_LOG_KEY, key.encode(), hashlib.sha256).hexdigest()[:12]


def _deny(request: Request, outcome: RateLimitOutcome, *, bucket: str, key: str) -> TooManyRequests:
    login_counter.add(1, {"outcome": "rate_limited", "bucket": bucket})
    logger.warning(
        "login_rate_limited",
        bucket=bucket,
        key_ref=_key_ref(key),
        path=request.url.path,
        retry_after_s=outcome.retry_after_s,
    )
    return TooManyRequests(
        detail=LOGIN_RATE_LIMIT_DETAIL,
        type="rate_limit_exceeded",
        instance=request.url.path,
        headers={**outcome.headers(), "Retry-After": str(outcome.retry_after_s)},
    )


async def enforce_login_rate_limit(request: Request, ctx: Context, *, email: str) -> None:
    """Spend one token from the caller-IP and account-email buckets, or raise 429."""
    if not ctx.config.admin.auth.login_rate_limit.enabled:
        return
    ip_limiter, email_limiter = _get_limiters(request, ctx)
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    ip_key = rate_limit_ip_key(client_ip(request, trusted))
    ip_outcome = await ip_limiter.acquire(ip_key)
    if not ip_outcome.allowed:
        raise _deny(request, ip_outcome, bucket="ip", key=ip_key)
    email_key = _email_key(email)
    email_outcome = await email_limiter.acquire(email_key)
    if not email_outcome.allowed:
        raise _deny(request, email_outcome, bucket="email", key=email_key)


__all__ = ["LOGIN_RATE_LIMIT_DETAIL", "enforce_login_rate_limit"]
