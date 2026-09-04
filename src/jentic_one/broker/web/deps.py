"""Broker-specific FastAPI dependencies for token validation and authorization.

Toolkit *selection* happens in the handler (see ``routers/execute``): it needs
the discovered API identity, which is only known after discovery. These
dependencies do auth + scope only; the handler calls ``select_toolkit``
through the injected ``get_toolkit_deriver`` provider.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated

import structlog
from fastapi import Depends, Request
from jentic.problem_details import Forbidden, Unauthorized

from jentic_one.broker.adapters.runners.base import UpstreamRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.exceptions import RateLimitExceededError
from jentic_one.broker.core.proxy_headers import reconstruct_upstream_url
from jentic_one.broker.services.auth import CompositeTokenValidator
from jentic_one.broker.services.idempotency import SharedStateIdempotencyStore
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import RuleEvaluatorProtocol, ToolkitDeriverProtocol
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event
from jentic_one.shared.events.mcp_session import SESSION_ID_HEADER, schedule_mcp_session_emit
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE
from jentic_one.shared.web.auth import extract_credential
from jentic_one.shared.web.deps import derive_origin

_logger = structlog.get_logger(__name__)

_meter = get_meter("broker")
_rate_limited_total = _meter.create_counter(
    "broker.rate_limited_total",
    description="Requests rejected with 429 by the per-caller rate limiter.",
)

_auth_failure_counts: dict[tuple[str, int], int] = {}
_auth_failure_emitted: set[tuple[str, int]] = set()
_background_tasks: set[asyncio.Task[None]] = set()


def _minute_bucket() -> int:
    return int(time.time()) // 60


def _record_auth_failure(actor_sub: str, request: Request) -> None:
    """Increment the per-actor auth failure counter; emit event if threshold crossed."""
    bucket = _minute_bucket()
    key = (actor_sub, bucket)
    _auth_failure_counts[key] = _auth_failure_counts.get(key, 0) + 1

    ctx: Context | None = getattr(request.app.state, "ctx", None)
    if ctx is None:
        return
    threshold = ctx.config.security.auth_failure_event_threshold

    if _auth_failure_counts[key] >= threshold and key not in _auth_failure_emitted:
        _auth_failure_emitted.add(key)
        count = _auth_failure_counts[key]
        actor_type = getattr(getattr(request.state, "identity", None), "actor_type", None)

        async def _emit() -> None:
            try:
                async with ctx.admin_db.transaction() as session:
                    await emit_event(
                        session,
                        type=EventType.UNAUTHORIZED_ACCESS_ATTEMPT,
                        severity=EventSeverity.WARNING,
                        summary=(
                            f"Agent {actor_sub} exceeded authorization failure "
                            f"threshold ({count} in 60s)"
                        ),
                        created_by=actor_sub,
                        actor_id=actor_sub,
                        actor_type=actor_type.value if actor_type else None,
                        requires_action=True,
                    )
            except Exception:
                _logger.warning("emit_auth_failure_event_failed", actor_sub=actor_sub)

        task = asyncio.create_task(_emit())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    stale_keys = [k for k in _auth_failure_counts if k[1] < bucket - 1]
    for k in stale_keys:
        _auth_failure_counts.pop(k, None)
        _auth_failure_emitted.discard(k)


async def require_broker_identity(request: Request) -> Identity:
    """Validate the credential (API key, JWT, or opaque token) and return the resolved identity.

    Raises Unauthorized if the credential is missing, unknown, revoked, or expired.
    """
    credential = extract_credential(request)

    validator: CompositeTokenValidator = request.app.state.broker_token_validator
    try:
        resolved = await validator.validate(credential)
    except TokenValidationError as exc:
        raise Unauthorized(
            detail="Invalid or expired access token",
            instance=request.url.path,
            type="unauthorized",
        ) from exc

    resolved.origin = derive_origin(request.headers.get("user-agent"))

    # Broker half of the two-plane ``mcp.session_started`` emit: an MCP session
    # whose only traffic is ``execute`` never touches the control plane, so it
    # must be detected here too (table-backed dedupe keeps it to one event).
    schedule_mcp_session_emit(
        getattr(request.app.state, "ctx", None),
        user_agent=request.headers.get("user-agent"),
        session_id=request.headers.get(SESSION_ID_HEADER),
        actor_id=resolved.sub,
        actor_type=resolved.actor_type.value,
    )
    return resolved


async def require_execute_scope(request: Request) -> Identity:
    """Authenticate and require the broker execute scope (no toolkit logic here)."""
    resolved = await require_broker_identity(request)

    # Toolkit keys carry BROKER_EXECUTE_SCOPE implicitly (set by ToolkitKeyResolver):
    # a toolkit has no actor_scope_grants, so holding a valid key *is* the execute
    # capability. Anything beyond "may execute" is gated by the toolkit permission
    # rules (RuleEvaluator) in the handler, not by scopes.
    if BROKER_EXECUTE_SCOPE not in resolved.permissions:
        _record_auth_failure(resolved.sub, request)
        raise Forbidden(
            detail=f"Insufficient scope: '{BROKER_EXECUTE_SCOPE}' required",
            instance=request.url.path,
            type="insufficient_scope",
        )

    return resolved


def get_toolkit_deriver(request: Request) -> ToolkitDeriverProtocol:
    """Provide the toolkit deriver so handlers don't read ``app.state`` directly."""
    deriver: ToolkitDeriverProtocol = request.app.state.broker_toolkit_deriver
    return deriver


async def require_execute_within_rate_limit(request: Request) -> Identity:
    """Auth + scope, then enforce the per-caller rate limit keyed on ``sub``.

    Enforced here — a post-auth dependency — because the actor isn't resolved at
    admission time (the admission middleware runs before auth). The limiter lives on
    ``app.state``; when rate limiting is disabled it is ``None`` and this is a
    pure pass-through of ``require_execute_scope``. A deny surfaces directly as a
    ``429`` carrying ``RateLimit-*`` + ``Retry-After`` (we are at the web edge).
    """
    resolved = await require_execute_scope(request)

    limiter: RateLimiter | None = getattr(request.app.state, "broker_rate_limiter", None)
    if limiter is None:
        return resolved

    outcome = await limiter.acquire(resolved.sub)
    if not outcome.allowed:
        _rate_limited_total.add(1, {"actor_type": resolved.actor_type.value})
        headers = outcome.headers()
        headers["Retry-After"] = str(outcome.retry_after_s)
        raise RateLimitExceededError(
            detail="Rate limit exceeded; slow down and retry after the indicated delay.",
            type="rate_limit_exceeded",
            headers=headers,
        )
    return resolved


def get_http_runner(request: Request) -> UpstreamRunner:
    """Select the upstream runner for this request via the scheme→runner registry.

    Handlers reach the runner only through this provider (DI convention),
    never by reading ``request.app.state`` inline. The runner is chosen by the
    upstream URL's **scheme** through the :class:`RunnerRegistry`:
    an unsupported scheme raises ``501`` and a degraded runner ``503``, before
    any operation discovery. A test swaps the runner via
    ``app.dependency_overrides[get_http_runner]``.
    """
    registry: RunnerRegistry = request.app.state.broker_runner_registry
    upstream_url = reconstruct_upstream_url(request.scope)
    return registry.select(upstream_url)


def get_idempotency_store(request: Request) -> SharedStateIdempotencyStore | None:
    """Provide the idempotency store, or ``None`` when idempotency is disabled.

    The handler treats ``None`` as "no idempotency": an ``Idempotency-Key`` is
    ignored and the request executes normally. A test swaps the store via
    ``app.dependency_overrides[get_idempotency_store]``.
    """
    return getattr(request.app.state, "broker_idempotency_store", None)


def get_rule_evaluator(request: Request) -> RuleEvaluatorProtocol:
    """Provide the rule evaluator so handlers don't read ``app.state`` directly."""
    evaluator: RuleEvaluatorProtocol = request.app.state.broker_rule_evaluator
    return evaluator


RequireBrokerIdentity = Annotated[Identity, Depends(require_broker_identity)]
RequireToolkitAccess = Annotated[Identity, Depends(require_execute_within_rate_limit)]
ToolkitDeriver = Annotated[ToolkitDeriverProtocol, Depends(get_toolkit_deriver)]
RuleEvaluatorDep = Annotated[RuleEvaluatorProtocol, Depends(get_rule_evaluator)]
HttpRunnerDep = Annotated[UpstreamRunner, Depends(get_http_runner)]
IdempotencyStoreDep = Annotated[SharedStateIdempotencyStore | None, Depends(get_idempotency_store)]
