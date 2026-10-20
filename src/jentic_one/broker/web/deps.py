"""Broker-specific FastAPI dependencies for token validation and authorization.

Credential-binding *selection* happens in the handler (see ``routers/execute``):
it needs the discovered API identity, which is only known after discovery.
These dependencies do auth + permission only; the handler derives bindings through
the injected ``get_credential_deriver`` provider.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
from typing import Annotated, Any

import structlog
from fastapi import Depends, Request
from jentic.problem_details import Forbidden, Unauthorized

from jentic_one.broker.adapters.runners.base import UpstreamRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.exceptions import OperationNotFoundError, RateLimitExceededError
from jentic_one.broker.core.proxy_headers import reconstruct_upstream_url
from jentic_one.broker.core.token_validation import CachedCliInvocationValidator
from jentic_one.broker.services.auth import CompositeTokenValidator
from jentic_one.broker.services.idempotency import SharedStateIdempotencyStore
from jentic_one.shared.auth.api_key_resolver import (
    RETIRED_SERVICE_ACCOUNT_KEY_DETAIL,
    is_retired_service_account_key,
)
from jentic_one.shared.auth.cli_invocation import (
    CLI_INVOCATION_TOKEN_PREFIX,
    CliInvocationIdentity,
)
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.broker.protocols import (
    AgentRuleEvaluatorProtocol,
    CredentialDeriverProtocol,
)
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event
from jentic_one.shared.events.mcp_session import SESSION_ID_HEADER, schedule_mcp_session_emit
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.web.auth import extract_credential
from jentic_one.shared.web.deps import derive_origin, get_ctx

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
            detail=(
                RETIRED_SERVICE_ACCOUNT_KEY_DETAIL
                if is_retired_service_account_key(credential)
                else "Invalid or expired access token"
            ),
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


def _extract_cli_invocation_token(request: Request) -> str | None:
    """The credential a vendor CLI sends in ``Authorization``, in any scheme it uses.

    ``gh`` sends ``token <t>``, ``cf`` sends ``Bearer <t>`` and git sends
    Basic auth with the token as the password. ``x-jentic-api-key`` is never
    read: a vendor CLI does not send it. Returns ``None`` when no credential
    can be read.
    """
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, value = header.strip().partition(" ")
    scheme = scheme.lower()
    value = value.strip()
    if scheme in ("token", "bearer"):
        return value or None
    if scheme == "basic":
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        _, separator, password = decoded.partition(":")
        return password if separator and password else None
    return None


def _cli_invocation_unauthorized(request: Request) -> Unauthorized:
    # One detail for every refusal (missing, wrong kind, unknown, revoked,
    # expired, wrong vendor): a distinguishable response is an oracle.
    return Unauthorized(
        detail="Invalid or expired CLI invocation token",
        instance=request.url.path,
        type="unauthorized",
    )


async def require_cli_invocation(request: Request) -> CliInvocationIdentity:
    """Authenticate a vendor CLI's ``cit_`` invocation token (any vendor).

    The broker's CLI facades authenticate with this, never with
    :func:`require_broker_identity`: only ``cit_`` tokens are accepted here
    (an ``at_`` token, API key or JWT is refused), and the composite
    validator behind ``/execute`` never resolves a ``cit_`` token. Facade
    routes use :func:`require_cli_facade_invocation`, which adds the vendor
    check on top of this.
    """
    token = _extract_cli_invocation_token(request)
    if token is None or not token.startswith(CLI_INVOCATION_TOKEN_PREFIX):
        raise _cli_invocation_unauthorized(request)
    validator: CachedCliInvocationValidator = request.app.state.broker_cli_invocation_validator
    try:
        resolved = await validator.validate(token)
    except TokenValidationError as exc:
        raise _cli_invocation_unauthorized(request) from exc
    resolved.identity.origin = derive_origin(request.headers.get("user-agent"))
    return resolved


def require_cli_facade_served(vendor: str) -> Any:
    """Dependency factory: 404 unless ``vendor``'s facade is switched on right now.

    Read per request from ``cli_facades.enabled`` and the vendor's own
    ``enabled``, so switching a facade off stops it without a restart (and an
    invocation token minted before the switch stops working with it). Listed
    before the token dependency, so a disabled facade answers 404 to everyone.
    """

    async def _dependency(request: Request, ctx: Context = Depends(get_ctx)) -> None:
        if not ctx.config.cli_facades.vendor_enabled(vendor):
            raise OperationNotFoundError(
                f"The {vendor} CLI facade is not enabled on this deployment.",
                type="cli_facade_not_found",
                instance=request.url.path,
            )

    return Depends(_dependency)


def require_cli_facade_invocation(vendor: str) -> Any:
    """Dependency factory: a valid invocation token minted for ``vendor``'s facade.

    A token minted for another vendor answers the same 401 as an invalid one,
    so a token leaked to one facade cannot be replayed at another. The agent
    must still hold the broker execute permission (live, through the parent
    token), the same gate ``/execute`` applies, and each call spends the
    agent's broker rate limit as a proxied call does.
    """

    async def _dependency(
        request: Request,
        invocation: CliInvocationIdentity = Depends(require_cli_invocation),
    ) -> CliInvocationIdentity:
        if invocation.claims.vendor != vendor:
            _logger.info(
                "cli_invocation_vendor_mismatch",
                invocation_id=invocation.claims.invocation_id,
                actor_id=invocation.identity.sub,
                token_vendor=invocation.claims.vendor,
                facade_vendor=vendor,
            )
            raise _cli_invocation_unauthorized(request)
        if BROKER_EXECUTE_PERMISSION not in invocation.identity.permissions:
            _record_auth_failure(invocation.identity.sub, request)
            raise Forbidden(
                detail=f"Insufficient scope: '{BROKER_EXECUTE_PERMISSION}' required",
                instance=request.url.path,
                type="insufficient_scope",
            )
        await _enforce_rate_limit(request, invocation.identity)
        return invocation

    return Depends(_dependency)


async def require_execute_permission(request: Request) -> Identity:
    """Authenticate and require the broker execute permission (no toolkit logic here)."""
    resolved = await require_broker_identity(request)

    # Every executing actor carries BROKER_EXECUTE_PERMISSION via
    # actor_permission_grants — including the successor agents the theme-5
    # Phase 4 retirement job cut for jntc_live_ toolkit keys (the job grants
    # exactly this permission). Anything beyond "may execute" is gated by the
    # permission rules in the handler, not by the grant.
    #
    # The refusal keeps OAuth2's registered vocabulary on the wire:
    # ``insufficient_scope`` is the RFC 6750 error code, so the problem type and
    # its detail stay in scope terms even though the check is on a permission.
    if BROKER_EXECUTE_PERMISSION not in resolved.permissions:
        _record_auth_failure(resolved.sub, request)
        raise Forbidden(
            detail=f"Insufficient scope: '{BROKER_EXECUTE_PERMISSION}' required",
            instance=request.url.path,
            type="insufficient_scope",
        )

    return resolved


def get_credential_deriver(request: Request) -> CredentialDeriverProtocol:
    """Provide the direct-binding credential deriver (theme-5 Phase 2)."""
    deriver: CredentialDeriverProtocol = request.app.state.broker_credential_deriver
    return deriver


def get_agent_rule_evaluator(request: Request) -> AgentRuleEvaluatorProtocol:
    """Provide the direct-binding rule evaluator (theme-5 Phase 2)."""
    evaluator: AgentRuleEvaluatorProtocol = request.app.state.broker_agent_rule_evaluator
    return evaluator


async def require_execute_within_rate_limit(request: Request) -> Identity:
    """Auth + permission, then enforce the per-caller rate limit keyed on ``sub``.

    Enforced here — a post-auth dependency — because the actor isn't resolved at
    admission time (the admission middleware runs before auth). The limiter lives on
    ``app.state``; when rate limiting is disabled it is ``None`` and this is a
    pure pass-through of ``require_execute_permission``. A deny surfaces directly as a
    ``429`` carrying ``RateLimit-*`` + ``Retry-After`` (we are at the web edge).
    """
    resolved = await require_execute_permission(request)
    await _enforce_rate_limit(request, resolved)
    return resolved


async def _enforce_rate_limit(request: Request, resolved: Identity) -> None:
    """Spend one unit of ``resolved.sub``'s rate limit, or raise 429 when it is exhausted."""
    limiter: RateLimiter | None = getattr(request.app.state, "broker_rate_limiter", None)
    if limiter is None:
        return

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


def get_cli_facade_runner(upstream_base: str) -> Any:
    """Dependency factory: the upstream runner for a facade forwarding to ``upstream_base``.

    The facade's own path is not an upstream URL, so the runner is selected
    by the scheme of the facade's fixed upstream base, through the same
    :class:`RunnerRegistry` as :func:`get_http_runner`.
    """

    def _dependency(request: Request) -> UpstreamRunner:
        registry: RunnerRegistry = request.app.state.broker_runner_registry
        return registry.select(upstream_base)

    return Depends(_dependency)


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


RequireBrokerIdentity = Annotated[Identity, Depends(require_broker_identity)]
RequireExecuteAccess = Annotated[Identity, Depends(require_execute_within_rate_limit)]
CredentialDeriver = Annotated[CredentialDeriverProtocol, Depends(get_credential_deriver)]
AgentRuleEvaluatorDep = Annotated[AgentRuleEvaluatorProtocol, Depends(get_agent_rule_evaluator)]
HttpRunnerDep = Annotated[UpstreamRunner, Depends(get_http_runner)]
IdempotencyStoreDep = Annotated[SharedStateIdempotencyStore | None, Depends(get_idempotency_store)]
