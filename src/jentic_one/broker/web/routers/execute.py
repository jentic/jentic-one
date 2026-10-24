"""Broker catch-all proxy route.

A single ``/{upstream_url:path}`` catch-all replaces the six per-method
``/execute`` handlers and all inbound ``Jentic-Api-*`` parsing. ``_handle`` is a
thin **web-edge adapter**: URL reconstruction → upgrade reject → SSRF pre-check →
in-process discovery → credential resolution → header assembly → **delegate to
the shared ``BrokerExecutionPipeline``** → adapt the result to a ``Response``
(mirroring the upstream status, passing headers through, adding ``Jentic-*``).
No resilience/credential/post-processing logic is inlined here — those are
pipeline stages / runner decorators (``services/execution/pipeline.py``,
``adapters/runners/``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Depends, Request, Response
from jentic.problem_details import ProblemDetailException
from starlette.datastructures import Headers

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.broker.core.directives import (
    approval_pending_limit_directive,
    switch_toolkit_directive,
)
from jentic_one.broker.core.errors import (
    ApprovalHoldUnavailableError,
    ApprovalPendingLimitError,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    PayloadTooLargeError,
    UpgradeNotSupportedError,
    UpstreamUrlNotAllowedError,
)
from jentic_one.broker.core.execution import mint_execution_id
from jentic_one.broker.core.headers import (
    JENTIC_REVISION_HEADER,
    REGION_MISMATCH_HINT,
    TRACESTATE_HEADER,
    JenticHeader,
    header_safe_value,
)
from jentic_one.broker.core.idempotency import fingerprint
from jentic_one.broker.core.proxy_headers import (
    PASSTHROUGH_CACHE_CONTROL,
    forward_headers,
    passthrough_response_headers,
    reconstruct_upstream_url,
    replay_headers,
)
from jentic_one.broker.core.revisions import parse_revisions
from jentic_one.broker.core.schemas import (
    AsyncQueuedResponse,
    AsyncQueuedResponseLinks,
    ExecuteRequestContext,
    HeldExecutionAcceptedLinks,
    HeldExecutionAcceptedResponse,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.discovery import (
    discover,
    discover_via_pins,
    resolve_pin_for_api,
)
from jentic_one.broker.services.execution.authorization import (
    ExecutionAuthorization,
    authorize_execution,
)
from jentic_one.broker.services.execution.pipeline import ExecutionOutcome
from jentic_one.broker.services.execution.service import (
    default_broker,
    persist_streaming_execution,
    run_execution,
)
from jentic_one.broker.services.idempotency import (
    IdempotencyState,
    SharedStateIdempotencyStore,
    StoredResponse,
)
from jentic_one.broker.web.deps import (
    AgentRuleEvaluatorDep,
    CredentialDeriver,
    HttpRunnerDep,
    IdempotencyStoreDep,
    RequireExecuteAccess,
)
from jentic_one.broker.web.streaming import StreamingOutcome
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.broker.protocols import (
    AgentRuleEvaluatorProtocol,
    CredentialDeriverProtocol,
    RegistryResolverProtocol,
    ResolveResult,
    RuleVerdict,
)
from jentic_one.shared.config import AppConfig, UpstreamClientConfig
from jentic_one.shared.context import Context
from jentic_one.shared.discovery.errors import OperationNotFoundError
from jentic_one.shared.events import (
    emit_event_best_effort,
    mint_trace_id,
    summary_label,
    summary_text,
    valid_trace_id_or_none,
)
from jentic_one.shared.http.injection import apply_injection
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.jobs.hold import (
    ENCRYPTED_PAYLOAD_KEY,
    HELD_AGENT_DIRECTIVE,
    INBOUND_COOKIE_PAYLOAD_KEY,
    PendingApprovalLimitError,
    file_hold,
    review_page_path,
)
from jentic_one.shared.jobs.hold import agent_name as held_agent_name
from jentic_one.shared.jobs.protocols import InjectedAuth
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.permissions.evaluation import rule_request_path
from jentic_one.shared.tracing import (
    JENTIC_TRACESTATE_KEY,
    current_trace_id,
    pack_jentic_tracestate,
)
from jentic_one.shared.url import has_host_server_variable
from jentic_one.shared.url_validation import validate_upstream_url_async
from jentic_one.shared.web.background_tasks import get_background_tasks
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.links import build_link
from jentic_one.shared.web.protocols import UnregisteredUrlHandler

logger = structlog.get_logger(__name__)

_meter = get_meter("broker")
_streaming_persist_failures = _meter.create_counter(
    "broker.streaming_execution.persist_failures",
    unit="{persist}",
    description=(
        "Streaming execution records not persisted, by reason: write_error (the "
        "write raised), backlog_timeout (no slot within "
        "broker.streaming_persist_acquire_timeout_s) or cancelled (abandoned "
        "while waiting or writing; a record cancelled while writing may still "
        "have landed)"
    ),
)
_streaming_persist_pending = _meter.create_up_down_counter(
    "broker.streaming_execution.persist_pending",
    unit="{persist}",
    description=(
        "Streaming execution persists in this process that have not finished, by "
        "state: waiting (queued for a broker.streaming_persist_max_concurrency "
        "slot) or in_flight (holding a slot and writing)"
    ),
)
_PENDING_WAITING = {"state": "waiting"}
_PENDING_IN_FLIGHT = {"state": "in_flight"}
_REASON_WRITE_ERROR = {"reason": "write_error"}
_REASON_BACKLOG_TIMEOUT = {"reason": "backlog_timeout"}
_REASON_CANCELLED = {"reason": "cancelled"}
_unregistered_url_handled = _meter.create_counter(
    "broker.unregistered_url.handled",
    description=(
        "Injected UnregisteredUrlHandler invocations, by outcome: "
        "handled (short-circuit), declined (fell through to 404), error (handler raised)"
    ),
)

router = APIRouter()

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class _StreamingPersistGate:
    """The semaphore bounding one Context's concurrent streaming persists."""

    loop: asyncio.AbstractEventLoop
    limit: int
    semaphore: asyncio.Semaphore


# One gate per Context (its admin pool), so two Contexts in one process never
# share or thrash a semaphore. A gate is rebuilt only when the running event
# loop changes: one Semaphore must not straddle loops.
_persist_gates: weakref.WeakKeyDictionary[Context, _StreamingPersistGate] = (
    weakref.WeakKeyDictionary()
)


def _effective_persist_limit(config: AppConfig) -> int:
    """``streaming_persist_max_concurrency`` clamped to ``admin.pool_max - 1`` (floor 1)."""
    return min(
        config.broker.streaming_persist_max_concurrency,
        max(1, config.databases.admin.pool_max - 1),
    )


def _streaming_persist_gate(ctx: Context) -> _StreamingPersistGate:
    """The gate bounding ``ctx``'s concurrent streaming persists on this loop.

    Logs ``streaming_persist_limit_clamped`` when the gate is built with a
    limit below the configured one, so the effective cap is never silent.
    """
    loop = asyncio.get_running_loop()
    gate = _persist_gates.get(ctx)
    if gate is None or gate.loop is not loop:
        limit = _effective_persist_limit(ctx.config)
        configured = ctx.config.broker.streaming_persist_max_concurrency
        if limit < configured:
            logger.warning(
                "streaming_persist_limit_clamped",
                configured=configured,
                effective=limit,
                admin_pool_max=ctx.config.databases.admin.pool_max,
            )
        gate = _StreamingPersistGate(loop=loop, limit=limit, semaphore=asyncio.Semaphore(limit))
        _persist_gates[ctx] = gate
    return gate


def _record_persist_failed(outcome: StreamingOutcome, ctx_req: ExecuteRequestContext) -> None:
    """Count and log a persist whose write raised (call from an ``except``)."""
    _streaming_persist_failures.add(1, _REASON_WRITE_ERROR)
    logger.error(
        "streaming_execution_persist_failed",
        execution_id=outcome.execution_id,
        trace_id=ctx_req.trace_id,
        reason=_REASON_WRITE_ERROR["reason"],
        exc_info=True,
    )


def _record_persist_abandoned(
    outcome: StreamingOutcome,
    ctx_req: ExecuteRequestContext,
    *,
    reason: dict[str, str],
    stage: str,
    **fields: object,
) -> None:
    """Count and log a persist given up before it finished.

    ``stage`` is ``waiting`` (no slot yet, so nothing was written) or
    ``writing`` (cancelled inside the transaction, so the record may or may not
    have landed). An expected condition, so no traceback.
    """
    _streaming_persist_failures.add(1, reason)
    logger.error(
        "streaming_execution_persist_abandoned",
        execution_id=outcome.execution_id,
        trace_id=ctx_req.trace_id,
        reason=reason["reason"],
        stage=stage,
        **fields,
    )


async def _acquire_persist_slot(
    semaphore: asyncio.Semaphore,
    timeout_s: float,
    outcome: StreamingOutcome,
    ctx_req: ExecuteRequestContext,
) -> bool:
    """Wait up to ``timeout_s`` for a persist slot; ``False`` when it timed out.

    A timeout or a cancellation while waiting is counted and logged as
    abandoned; the cancellation is re-raised. No permit is held on any path
    that does not return ``True``.
    """
    _streaming_persist_pending.add(1, _PENDING_WAITING)
    try:
        async with asyncio.timeout(timeout_s):
            await semaphore.acquire()
    except TimeoutError:
        _record_persist_abandoned(
            outcome,
            ctx_req,
            reason=_REASON_BACKLOG_TIMEOUT,
            stage="waiting",
            acquire_timeout_s=timeout_s,
        )
        return False
    except asyncio.CancelledError:
        _record_persist_abandoned(outcome, ctx_req, reason=_REASON_CANCELLED, stage="waiting")
        raise
    finally:
        _streaming_persist_pending.add(-1, _PENDING_WAITING)
    return True


async def _persist_streaming_outcome(
    outcome: StreamingOutcome,
    ctx: Context,
    ctx_req: ExecuteRequestContext,
    started_at: datetime,
    actor_id: str,
    actor_type: str,
    origin: str | None = None,
) -> None:
    """Best-effort persistence of a streaming execution record.

    Extracted so unit tests can exercise the real logic without invoking the full
    handler. Runs off the request on the app's background-task registry, under
    ``broker.streaming_persist_timeout_s``. Concurrent calls for one Context are
    bounded by its ``_streaming_persist_gate``: an excess call waits for a slot
    before opening the admin transaction, for at most
    ``broker.streaming_persist_acquire_timeout_s``. Callers run it once the
    response is complete or the client has disconnected, when the request no
    longer holds its admission slot, so the wait delays neither the client nor
    ``/ready``. Never raises except ``CancelledError``: a write error, a slot
    timeout and a cancellation (its timeout or the shutdown drain) are each
    logged and counted on ``broker.streaming_execution.persist_failures`` by
    ``reason``.
    """
    status = (
        ExecutionStatus.FAILED
        if outcome.error or outcome.http_status >= 400
        else ExecutionStatus.COMPLETED
    )
    error_msg = outcome.error or (
        f"Upstream returned {outcome.http_status}" if status is ExecutionStatus.FAILED else None
    )
    try:
        semaphore = _streaming_persist_gate(ctx).semaphore
        acquire_timeout_s = ctx.config.broker.streaming_persist_acquire_timeout_s
    except Exception:
        _record_persist_failed(outcome, ctx_req)
        return
    if not await _acquire_persist_slot(semaphore, acquire_timeout_s, outcome, ctx_req):
        return
    _streaming_persist_pending.add(1, _PENDING_IN_FLIGHT)
    try:
        async with ctx.admin_db.transaction() as session:
            await persist_streaming_execution(
                session,
                execution_id=outcome.execution_id,
                started_at=started_at,
                status=status,
                http_status=outcome.http_status,
                duration_ms=outcome.duration_ms,
                error=error_msg,
                ctx_req=ctx_req,
                actor_id=actor_id,
                actor_type=actor_type,
                origin=origin,
                security_config=ctx.config.security,
            )
    except asyncio.CancelledError:
        _record_persist_abandoned(outcome, ctx_req, reason=_REASON_CANCELLED, stage="writing")
        raise
    except Exception:
        _record_persist_failed(outcome, ctx_req)
    finally:
        semaphore.release()
        _streaming_persist_pending.add(-1, _PENDING_IN_FLIGHT)


def _resolve_body_cap(content_type: str | None, cfg: UpstreamClientConfig) -> int:
    """Resolve the body cap for a request from its Content-Type.

    Matched most-specific-first: exact (``application/json``) → wildcard
    (``audio/*``) → global ``max_request_bytes``. A missing/unknown type falls
    back to the global default (never unbounded).
    """
    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        by_type = cfg.max_request_bytes_by_type
        if mime in by_type:
            return by_type[mime]
        prefix = mime.split("/", 1)[0]
        wildcard = f"{prefix}/*"
        if wildcard in by_type:
            return by_type[wildcard]
    return cfg.max_request_bytes


async def _read_capped_body(request: Request, max_bytes: int) -> bytes:
    """Buffer the inbound body, failing fast mid-stream past ``max_bytes`` (413).

    The body is buffered (not streamed) because it's needed after the inbound
    stream is gone (async persistence, idempotency, retries). Works for
    ``Transfer-Encoding: chunked`` too — ``request.stream()`` yields de-chunked
    bytes, so an over-cap chunked upload still fails with 413 (never 411).
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                detail=f"Request body exceeds the {max_bytes}-byte cap.",
                type="payload_too_large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_request_body(request: Request, method: str, ctx: Context) -> bytes | None:
    """Capped body read for body-bearing methods (None otherwise)."""
    if method not in _BODY_METHODS:
        return None
    cfg = ctx.config.broker.resilience.upstream
    max_bytes = _resolve_body_cap(request.headers.get("content-type"), cfg)
    return await _read_capped_body(request, max_bytes)


def _should_async(prefer: str | None) -> bool:
    if prefer is None:
        return False
    return "respond-async" in prefer.lower()


def _revision_header(request: Request) -> str | None:
    """Combine repeated ``Jentic-Revision`` header lines into one comma-joined value.

    The header is multi-valued by repetition *or* comma-separation (OpenAPI
    ``style: simple, explode: false``); joining repeated lines with commas lets
    the pure parser treat both wire forms identically. Returns ``None`` when the
    header is absent.
    """
    values = request.headers.getlist(JENTIC_REVISION_HEADER)
    if not values:
        return None
    return ",".join(values)


def _parse_traceparent(value: str | None) -> str | None:
    """Extract the trace-id field from a W3C ``traceparent`` header value.

    ``version-traceid-spanid-flags`` → the 32-hex ``traceid`` field, or ``None``
    when the header is absent/malformed or carries the all-zeros (invalid per
    W3C) trace id — ``valid_trace_id_or_none`` rejects both.
    """
    if not value:
        return None
    parts = value.split("-")
    if len(parts) < 4:
        return None
    return valid_trace_id_or_none(parts[1].lower())


def _derive_trace_id(headers: Headers) -> str:
    """Derive a valid 32-hex trace id for this request (#903).

    Preference order:

    1. The active OTel span — the inbound instrumentation already parsed the
       W3C ``traceparent`` (or started a fresh trace), so this is the id the
       rest of the platform correlates on. Every production surface is
       instrumented (``attach_http_observability``), so this branch always
       wins there; the rest are fallbacks for uninstrumented contexts
       (tests, embedded use).
    2. The ``traceparent`` header's trace-id field, parsed per W3C (never the
       raw header value, which is not a trace id).
    3. A 32-hex ``x-request-id`` (the #903 workaround header). Superseded by
       the span id on instrumented surfaces — a caller who needs to pick the
       trace id must send ``traceparent``.
    4. A freshly minted random id — never the literal ``"unknown"``, which
       failed event emission and 500'd the whole execute request.
    """
    from_span = current_trace_id()
    if from_span is not None:
        return from_span
    from_traceparent = _parse_traceparent(headers.get("traceparent"))
    if from_traceparent is not None:
        return from_traceparent
    from_request_id = valid_trace_id_or_none((headers.get("x-request-id") or "").lower())
    if from_request_id is not None:
        return from_request_id
    return mint_trace_id()


def _context_from_discovery(
    *, upstream_url: str, method: str, request: Request, resolved: ResolveResult
) -> ExecuteRequestContext:
    headers = request.headers
    return ExecuteRequestContext(
        upstream_url=upstream_url,
        method=method,
        trace_id=_derive_trace_id(headers),
        # toolkit_id is nullable-legacy: nothing sets it since theme-5 Phase 6b
        # deleted toolkit derivation; it survives on the schema so historical
        # execution rows keep their attribution shape.
        toolkit_id=None,
        operation=resolved.operation,
        api_vendor=resolved.api.vendor,
        api_name=resolved.api.name,
        api_version=resolved.api.version,
        prefer=headers.get("prefer"),
        pinned_revisions=None,
        server_variables=dict(resolved.server_variables) or None,
        server_variable_defaults=dict(resolved.server_variable_defaults) or None,
        server_variables_unresolved=resolved.server_variables_unresolved,
    )


def _metadata_headers(ctx_req: ExecuteRequestContext, execution_id: str) -> dict[str, str]:
    meta: dict[str, str] = {JenticHeader.EXECUTION_ID.value: execution_id}
    if ctx_req.operation:
        meta[JenticHeader.OPERATION.value] = ctx_req.operation.id
    if ctx_req.api_vendor:
        meta[JenticHeader.API_VENDOR.value] = ctx_req.api_vendor
    # Credential attribution (#740). Emitted only when the resolver actually
    # picked a stored credential — a broker-origin failure before injection,
    # inline auth, or a credential-less API leaves both ``None`` and both
    # headers absent, so a missing header unambiguously means "no credential".
    # The name is operator-authored free text: sanitize before emission.
    if ctx_req.credential_id:
        meta[JenticHeader.CREDENTIAL_ID.value] = ctx_req.credential_id
    if ctx_req.credential_name:
        meta[JenticHeader.CREDENTIAL_NAME.value] = header_safe_value(ctx_req.credential_name)
    # Echo the jentic= tracestate member (same who/what payload as the outbound
    # request) so a caller can correlate the response to its distributed trace
    # without re-deriving it.
    member = pack_jentic_tracestate(
        execution_id=execution_id,
        toolkit_id=ctx_req.toolkit_id,
        vendor=ctx_req.api_vendor,
        name=ctx_req.api_name,
        version=ctx_req.api_version,
    )
    meta[TRACESTATE_HEADER] = f"{JENTIC_TRACESTATE_KEY}={member}"
    return meta


async def _resolve_credentials(
    ctx_req: ExecuteRequestContext,
    ctx: Context,
    identity: Identity,
    credential_name: str | None = None,
    *,
    preresolved: ResolvedCredential | None = None,
    allowed_credential_ids: list[str] | None = None,
    credential_id: str | None = None,
) -> InjectedAuth:
    """Resolve + inject credentials via the shared ``CredentialService``.

    ``preresolved`` carries the direct path's already-selected credential so
    injection never re-resolves (and cannot pick a different credential than
    the one the rules were evaluated against). ``allowed_credential_ids`` is
    the injection boundary: only these ids may resolve, and an empty list
    resolves nothing. ``credential_id`` (``Jentic-Credential-Id``)
    disambiguates *within* that boundary — it can never select a credential
    outside it.
    """
    return await CredentialService(ctx).inject(
        api_vendor=ctx_req.api_vendor or "",
        api_name=ctx_req.api_name or "",
        api_version=ctx_req.api_version or "",
        identity=identity,
        credential_name=credential_name,
        credential_id=credential_id,
        allowed_credential_ids=allowed_credential_ids,
        trace_id=ctx_req.trace_id,
        preresolved=preresolved,
        request_server_variables=ctx_req.server_variables,
        server_variables_unresolved=ctx_req.server_variables_unresolved,
    )


def _inbound_cookie(request: Request) -> str | None:
    """The caller's ``Cookie`` header, every field line joined with ``; ``.

    HTTP/2 lets a client split cookies over several ``cookie`` fields; joining
    them keeps every cookie rather than only the first line.
    """
    return "; ".join(request.headers.getlist("cookie")) or None


def _apply_injection(
    upstream_url: str,
    injection: InjectedAuth,
    request: Request,
    server_variable_defaults: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """The outbound URL and forwarded headers with injected auth applied.

    Filters the inbound headers for forwarding, then applies ``injection``
    through the shared :func:`~jentic_one.shared.http.injection.apply_injection`
    the async worker also uses, so a call sent inline and the same call sent
    with ``Prefer: respond-async`` reach the upstream alike.
    """
    return apply_injection(
        forward_headers(request.headers, {}),
        upstream_url,
        injection,
        inbound_cookie=_inbound_cookie(request),
        server_variable_defaults=server_variable_defaults,
    )


def _resolve_broker(request: Request, runner: UpstreamRunner) -> Broker:
    """Select the broker for this request: an injected instance wins over the default.

    An injected ``app.state.broker`` owns its own transport and is used verbatim;
    only its absence falls back to the per-request ``broker_factory`` (default:
    :func:`default_broker`) over the selected runner. Both the buffered and
    streaming sync paths resolve through here so neither can bypass an injected
    broker's controls.
    """
    injected = getattr(request.app.state, "broker", None)
    broker_factory = getattr(request.app.state, "broker_factory", default_broker)
    return injected if injected is not None else broker_factory(runner)


async def _handle_unregistered_url(
    request: Request, *, method: str, upstream_url: str, identity: Identity
) -> Response | None:
    """Run the container-injected unregistered-URL hook, if any (None → 404).

    Only ``_handle``'s *unregistered-URL* miss calls this — it runs after
    ``validate_upstream_url_async``, so the handler only ever sees an egress-approved
    URL (see ``UnregisteredUrlHandler``'s contract). The pinned-revision miss
    never invokes it: the API is registered there, so that miss is a caller pin
    error, not an unregistered flow.

    A short-circuit leaves no execution row and emits no event — the
    outcome-attributed counter is the core-side floor so operators see
    handled/declined/error volumes without a downstream table.

    A broken handler must not change the route's contract for callers: a raised
    exception (other than a deliberate ``ProblemDetailException``) is logged and
    counted here — inside the router, while ``request_id`` is still bound — and
    falls through to the documented 404. Only ``Exception`` is caught, so
    ``CancelledError`` propagates.
    """
    handler: UnregisteredUrlHandler | None = getattr(
        request.app.state, "unregistered_url_handler", None
    )
    if handler is None:
        return None
    try:
        response = await handler(
            method=method, upstream_url=upstream_url, identity=identity, request=request
        )
    except ProblemDetailException:
        # A deliberate downstream problem response — the handler owns it.
        raise
    except Exception:
        _unregistered_url_handled.add(1, {"outcome": "error"})
        logger.exception(
            "unregistered_url_handler_failed",
            handler=f"{type(handler).__module__}.{type(handler).__qualname__}",
            method=method,
        )
        return None
    _unregistered_url_handled.add(1, {"outcome": "handled" if response is not None else "declined"})
    return response


async def _handle(
    request: Request,
    method: str,
    ctx: Context,
    identity: Identity,
    credential_deriver: CredentialDeriverProtocol,
    agent_rule_evaluator: AgentRuleEvaluatorProtocol,
    runner: UpstreamRunner,
    idempotency: SharedStateIdempotencyStore | None,
) -> Response:
    """Thin web-edge adapter — see module docstring."""
    if request.headers.get("upgrade"):
        raise UpgradeNotSupportedError(
            detail="Protocol upgrade not supported by the broker.",
            type="upgrade_not_supported",
        )

    raw_url = reconstruct_upstream_url(request.scope)
    try:
        upstream_url = await validate_upstream_url_async(raw_url, ctx.config.broker.egress)
    except ValueError as exc:
        raise UpstreamUrlNotAllowedError(detail=str(exc), type="invalid_upstream_url") from exc

    resolver: RegistryResolverProtocol = request.app.state.broker_registry_resolver

    # Parse the multi-valued Jentic-Revision header at the edge. A malformed
    # value raises InvalidRevisionPinError (→ 422) here, before any registry
    # lookup — never an uncaught 500 mid-discovery.
    pins = parse_revisions(_revision_header(request))

    resolved = await discover(resolver, method=method, url=upstream_url)
    pinned_revisions: dict[str, str] | None = None
    if resolved is None and pins:
        # No live revision serves this URL; a pinned (e.g. never-promoted draft)
        # revision still may.
        via_pin = await discover_via_pins(
            resolver, method=method, url=upstream_url, pins=pins, identity=identity
        )
        if via_pin is not None:
            resolved, pinned_revision_id = via_pin
            pinned_revisions = {
                f"{resolved.api.vendor}:{resolved.api.name}:{resolved.api.version}": str(
                    pinned_revision_id
                )
            }
    if resolved is None:
        handled = await _handle_unregistered_url(
            request, method=method, upstream_url=upstream_url, identity=identity
        )
        if handled is not None:
            return handled
        raise OperationNotFoundError(detail="Operation not found — unregistered upstream URL.")

    # If a pin applies to the discovered API, translate it to a revision_id
    # in-process (no control-plane HTTP) and re-resolve against the pinned spec.
    if pins and pinned_revisions is None:
        revision_id = await resolve_pin_for_api(
            resolver, api=resolved.api, pins=pins, identity=identity
        )
        if revision_id is not None:
            pinned = await discover(
                resolver, method=method, url=upstream_url, revision_id=revision_id
            )
            if pinned is None:
                raise OperationNotFoundError(detail="Operation not found in the pinned revision.")
            resolved = pinned
            pinned_revisions = {
                f"{resolved.api.vendor}:{resolved.api.name}:{resolved.api.version}": str(
                    revision_id
                )
            }

    ctx_req = _context_from_discovery(
        upstream_url=upstream_url, method=method, request=request, resolved=resolved
    )
    ctx_req.pinned_revisions = pinned_revisions
    # Capture whether the spec's host is templated (server variable) *before*
    # credential injection substitutes it — this is the signal that drives the
    # region-mismatch hint on an upstream 401/403 (#638).
    ctx_req.has_server_variable = has_host_server_variable(upstream_url)
    # Direct agent→credential bindings (theme-5): every caller kind rides this
    # path — retired toolkit keys resolve as successor agents. The legacy
    # toolkit-derivation path was deleted in Phase 6b.
    authorization = await authorize_execution(
        ctx=ctx,
        identity=identity,
        api=resolved.api,
        operation_id=resolved.operation.id,
        method=method,
        path=rule_request_path(
            resolved.operation.relative_path, upstream_url, operation_id=resolved.operation.id
        ),
        upstream_url=upstream_url,
        instance=request.url.path,
        credential_deriver=credential_deriver,
        agent_rule_evaluator=agent_rule_evaluator,
        credential_name=request.headers.get("jentic-credential-name"),
        credential_id=request.headers.get("jentic-credential-id"),
        request_server_variables=ctx_req.server_variables,
        server_variables_unresolved=ctx_req.server_variables_unresolved,
        trace_id=ctx_req.trace_id,
        operation_path=resolved.operation.path,
    )
    selected_credential = authorization.selected_credential
    allowed_credential_ids = authorization.allowed_credential_ids
    if selected_credential is not None:
        # Attribution is known at selection time on the direct path, so the
        # 202/streaming metadata carries it too (the buffered path re-stamps
        # the same values from the injection result).
        ctx_req.credential_id = selected_credential.credential_id
        ctx_req.credential_name = selected_credential.name

    # A require-approval rule matched — hold the execution before any upstream
    # call. Both the async and sync request paths enter the hold path (async
    # preference makes no difference: the result is always a 202 held envelope).
    if authorization.verdict == RuleVerdict.REQUIRE_APPROVAL:
        return await _handle_hold(request, ctx_req, ctx, identity, authorization)

    if _should_async(ctx_req.prefer):
        return await _handle_async(
            request,
            ctx_req,
            ctx,
            identity,
            selected_credential_id=selected_credential.credential_id,
            allowed_credential_ids=allowed_credential_ids,
        )

    idem_key = request.headers.get("idempotency-key")
    upstream_cfg = ctx.config.broker.resilience.upstream

    # Stream the response straight through for sync, non-idempotent
    # requests — idempotent requests fall to the buffered path below because
    # replay needs the whole body. Disabled requests / non-streaming runners
    # also fall through.
    if (
        upstream_cfg.stream_passthrough_enabled
        and not (idempotency is not None and idem_key)
        and isinstance(runner, StreamingUpstreamRunner)
    ):
        return await _handle_streaming(
            request,
            ctx_req,
            ctx,
            identity,
            runner,
            upstream_cfg,
            preresolved=selected_credential,
            allowed_credential_ids=allowed_credential_ids,
        )

    # Buffer the body once (needed for the idempotency fingerprint and the call).
    body = await _read_request_body(request, method, ctx)

    # Claim/replay on Idempotency-Key. Async same-job_id replay is future
    # work — only the sync path is idempotent here.
    fp: str | None = None
    if idempotency is not None and idem_key:
        # The fingerprint's consumer scope is the selected credential
        # (historically the toolkit on the pre-6b legacy path — same slot,
        # so replay identity is stable across the cutover).
        fp = fingerprint(
            method,
            ctx_req.upstream_url,
            ctx_req.credential_id or "",
            body,
        )
        outcome_idem = await idempotency.begin(identity.sub, idem_key, fp)
        if outcome_idem.state is IdempotencyState.CONFLICT:
            raise IdempotencyConflictError(
                detail="Idempotency-Key reused with a different request.",
                type="idempotency_conflict",
            )
        if outcome_idem.state is IdempotencyState.IN_PROGRESS:
            raise IdempotencyInProgressError(
                detail="A request with this Idempotency-Key is still in progress; retry shortly.",
                type="idempotency_in_progress",
                headers={"Retry-After": str(outcome_idem.retry_after_s)},
            )
        if outcome_idem.state is IdempotencyState.REPLAY and outcome_idem.stored is not None:
            return _replay_response(outcome_idem.stored)

    outcome = await _execute_buffered(
        request,
        ctx_req,
        ctx,
        identity,
        runner,
        body,
        selected_credential=selected_credential,
        allowed_credential_ids=allowed_credential_ids,
    )

    response = _assemble_response(outcome, ctx_req)
    if idempotency is not None and idem_key and fp is not None:
        await idempotency.complete(
            identity.sub,
            idem_key,
            fp,
            status_code=response.status_code,
            headers=dict(response.headers),
            body=outcome.result.body,
        )
    return response


async def _execute_buffered(
    request: Request,
    ctx_req: ExecuteRequestContext,
    ctx: Context,
    identity: Identity,
    runner: UpstreamRunner,
    body: bytes | None,
    *,
    selected_credential: ResolvedCredential | None = None,
    allowed_credential_ids: list[str] | None = None,
) -> ExecutionOutcome:
    """Inject credentials, run the buffered upstream call, and persist its record.

    Injection commits ``credential.accessed`` before the material is used (the
    ``CredentialService.inject`` contract). No admin transaction is open across
    the upstream call: ``run_execution`` opens one for the execution record only
    after the call returns or raises, so a slow upstream never pins an admin
    connection (or, on SQLite, the database write lock).
    """
    credential_name = request.headers.get("jentic-credential-name")
    injection = await _resolve_credentials(
        ctx_req,
        ctx,
        identity,
        credential_name,
        preresolved=selected_credential,
        allowed_credential_ids=allowed_credential_ids,
        credential_id=request.headers.get("jentic-credential-id"),
    )
    ctx_req.upstream_url, forwarded = _apply_injection(
        ctx_req.upstream_url, injection, request, ctx_req.server_variable_defaults
    )
    ctx_req.credential_id = injection.credential_id
    ctx_req.credential_name = injection.credential_name
    if injection.server_variables or ctx_req.server_variable_defaults:
        try:
            await validate_upstream_url_async(ctx_req.upstream_url, ctx.config.broker.egress)
        except ValueError as exc:
            raise UpstreamUrlNotAllowedError(detail=str(exc), type="invalid_upstream_url") from exc

    broker = _resolve_broker(request, runner)
    return await run_execution(
        ctx_req,
        body=body,
        headers=forwarded,
        admin_db=ctx.admin_db,
        timeout=ctx.config.broker.upstream_timeout_s,
        broker=broker,
        actor_id=identity.sub,
        actor_type=identity.actor_type.value,
        origin=identity.origin.value,
        security_config=ctx.config.security,
        signing=injection.signing,
    )


async def _handle_streaming(
    request: Request,
    ctx_req: ExecuteRequestContext,
    ctx: Context,
    identity: Identity,
    runner: StreamingUpstreamRunner,
    upstream_cfg: UpstreamClientConfig,
    *,
    preresolved: ResolvedCredential | None = None,
    allowed_credential_ids: list[str] | None = None,
) -> Response:
    """Sync, non-idempotent streaming passthrough.

    Same credential resolution + header assembly as the buffered path, but the
    upstream body streams straight to the client (no whole-buffering) under the
    response-size cap + transfer deadline + client-disconnect teardown owned by
    ``open_streaming_response``.

    Persistence is best-effort and never holds the response: once the
    streaming body ends — completed, failed mid-body, or cut off by a client
    disconnect — the completion callback schedules ``_persist_streaming_outcome``
    exactly once on the app's background-task registry and returns, once the
    request has released its admission slot, so an aborted response's connection
    closes without waiting on the database. A DB failure, timeout or shutdown
    cancellation is logged + counted and never affects the already-sent response.
    """
    body = await _read_request_body(request, ctx_req.method, ctx)
    credential_name = request.headers.get("jentic-credential-name")
    injection = await _resolve_credentials(
        ctx_req,
        ctx,
        identity,
        credential_name,
        preresolved=preresolved,
        allowed_credential_ids=allowed_credential_ids,
        credential_id=request.headers.get("jentic-credential-id"),
    )
    ctx_req.upstream_url, forwarded = _apply_injection(
        ctx_req.upstream_url, injection, request, ctx_req.server_variable_defaults
    )
    ctx_req.credential_id = injection.credential_id
    ctx_req.credential_name = injection.credential_name
    if injection.server_variables or ctx_req.server_variable_defaults:
        try:
            await validate_upstream_url_async(ctx_req.upstream_url, ctx.config.broker.egress)
        except ValueError as exc:
            raise UpstreamUrlNotAllowedError(detail=str(exc), type="invalid_upstream_url") from exc

    execution_id = mint_execution_id()
    started_at = datetime.now(UTC)
    runner_request = RunnerRequest(
        method=ctx_req.method,
        url=ctx_req.upstream_url,
        headers=forwarded,
        body=body,
        timeout_s=ctx.config.broker.upstream_timeout_s,
        signing=injection.signing,
    )

    background_tasks = get_background_tasks(request.app)
    persist_timeout_s = ctx.config.broker.streaming_persist_timeout_s

    async def _persist_callback(outcome: StreamingOutcome) -> None:
        persist = _persist_streaming_outcome(
            outcome,
            ctx,
            ctx_req,
            started_at,
            identity.sub,
            identity.actor_type.value,
            origin=identity.origin.value,
        )
        if background_tasks is None:
            # An app built without a registry (a bare test app) writes inline,
            # still bounded; every app from ``create_surface_app`` has one.
            async with asyncio.timeout(persist_timeout_s):
                await persist
            return
        background_tasks.spawn(
            persist,
            kind="streaming_execution_persist",
            timeout_s=persist_timeout_s,
            log_fields={"execution_id": outcome.execution_id, "trace_id": ctx_req.trace_id},
        )

    broker = _resolve_broker(request, runner)
    return await broker.execute_streaming(
        runner,
        runner_request,
        ctx_req,
        execution_id,
        transfer_deadline_s=upstream_cfg.transfer_deadline_s,
        background_callback=_persist_callback,
    )


def _region_mismatch_hint(status_code: int, ctx_req: ExecuteRequestContext) -> str | None:
    """The region-mismatch hint for a templated-host API's upstream 401/403 (#638).

    Returns ``None`` when it does not apply. The hint is surfaced via the
    ``Jentic-Hint`` response header — the mirrored upstream body is left verbatim
    (the passthrough invariant).
    """
    if status_code in (401, 403) and ctx_req.has_server_variable:
        return REGION_MISMATCH_HINT
    return None


def _assemble_response(outcome: ExecutionOutcome, ctx_req: ExecuteRequestContext) -> Response:
    result = outcome.result
    metadata = _metadata_headers(ctx_req, outcome.context.execution_id)
    metadata[JenticHeader.UPSTREAM_STATUS.value] = str(result.status_code)
    if outcome.error_origin is not None:
        metadata[JenticHeader.ERROR_ORIGIN.value] = outcome.error_origin.value
    hint = _region_mismatch_hint(result.status_code, ctx_req)
    if hint is not None:
        metadata[JenticHeader.HINT.value] = hint

    passthrough = passthrough_response_headers(result.headers)
    return Response(
        content=result.body,
        status_code=result.status_code,
        media_type=result.content_type,
        headers={**passthrough, **metadata},
    )


def _replay_response(stored: StoredResponse) -> Response:
    """Re-emit a stored idempotent response, tagged ``Idempotent-Replayed: true``.

    The stored headers (which already carry the original ``Jentic-*`` metadata)
    were scrubbed of sensitive values + body-encoding headers on the original
    completion; an oversized original (``body_omitted``) replays its
    status/headers with an empty body and a marker header so the caller knows the
    body wasn't cached (the no-duplicate-side-effect guarantee still held).
    """
    # A replay re-emits a passthrough body, so it carries the passthrough
    # Cache-Control whatever the stored record holds (#1835).
    headers = {k: v for k, v in stored.headers.items() if k.lower() != "cache-control"}
    headers[JenticHeader.IDEMPOTENT_REPLAYED.value] = "true"
    headers["cache-control"] = PASSTHROUGH_CACHE_CONTROL
    if stored.body_omitted:
        headers[JenticHeader.IDEMPOTENCY_BODY_OMITTED.value] = "true"
        # The stored content-length described the original (uncached) body; drop
        # it so the ASGI server recomputes it for the empty replay body.
        headers.pop("content-length", None)
    return Response(
        content=stored.body,
        status_code=stored.status_code,
        headers=headers,
    )


def _async_job_payload(
    ctx_req: ExecuteRequestContext,
    *,
    execution_id: str,
    origin: str,
    selected_credential_id: str | None = None,
    allowed_credential_ids: list[str] | None = None,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    inbound_cookie_enc: str | None = None,
) -> dict[str, Any]:
    """Build the job payload for an async (202) execution.

    ``headers`` are the request headers the run replays (see
    ``REPLAY_HEADERS``) — without them a JSON body would reach the upstream
    with no ``Content-Type``. ``inbound_cookie_enc`` is the caller's
    ``Cookie`` as platform-key ciphertext (see :func:`_captured_cookie`); the
    worker decrypts it and applies it like the sync route.

    A standalone builder so the payload contract is unit-testable at the
    producer: the worker seams (handler forwarding, executor rebuild) each
    have their own pins, and this is the third leg.
    """
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "upstream_url": ctx_req.upstream_url,
        "method": ctx_req.method,
        "trace_id": ctx_req.trace_id,
        "operation": ctx_req.operation.model_dump() if ctx_req.operation else None,
        "api_vendor": ctx_req.api_vendor,
        "api_name": ctx_req.api_name,
        "api_version": ctx_req.api_version,
        "origin": origin,
    }
    if selected_credential_id is not None:
        payload["credential_id"] = selected_credential_id
    if allowed_credential_ids is not None:
        payload["allowed_credential_ids"] = allowed_credential_ids
    if ctx_req.pinned_revisions:
        payload["pinned_revisions"] = ctx_req.pinned_revisions
    if ctx_req.server_variables:
        payload["server_variables"] = ctx_req.server_variables
    if ctx_req.server_variable_defaults:
        payload["server_variable_defaults"] = ctx_req.server_variable_defaults
    if ctx_req.server_variables_unresolved:
        payload["server_variables_unresolved"] = True
    if body:
        payload["body_b64"] = base64.b64encode(body).decode()
    if headers:
        payload["headers"] = dict(headers)
    if inbound_cookie_enc:
        payload[INBOUND_COOKIE_PAYLOAD_KEY] = inbound_cookie_enc
    return payload


def _captured_cookie(request: Request, ctx: Context) -> str | None:
    """The caller's ``Cookie`` encrypted for an async or held job payload.

    The job sits in the admin DB until a worker claims it, so the cookie is
    kept only as platform-key ciphertext. Without an encryption keyset there
    is nowhere safe to keep it: the run is still queued, without the caller's
    cookies, and the drop is logged.
    """
    cookie = _inbound_cookie(request)
    if cookie is None:
        return None
    if not ctx.has_encryption_keyset:
        logger.warning("async_execution_cookie_not_kept", reason="no_encryption_keyset")
        return None
    return ctx.encryption.encrypt(cookie)


def _review_url(ctx: Context, request: Request, approval_id: str) -> str:
    """Absolute URL of the approval's review page in the web UI.

    The UI is served under ``/app`` beside the admin API, so the admin API's
    public origin (``broker.jobs_api_base_url``) wins, then this deployment's
    ``server.public_base_url``, then the request's own origin (combined
    deployment). The URL carries no credential: the reviewer signs in.
    """
    path = review_page_path(approval_id)
    base = ctx.config.broker.jobs_api_base_url or ctx.config.server.public_base_url
    if base:
        return f"{base.rstrip('/')}{path}"
    return build_link(request, path)


async def _handle_hold(
    request: Request,
    ctx_req: ExecuteRequestContext,
    ctx: Context,
    identity: Identity,
    authorization: ExecutionAuthorization,
) -> Response:
    """File (or join) a require-approval hold and answer the 202 held envelope.

    The held job's payload is the async-job payload encrypted with the platform
    key — it can sit for the whole approval TTL — and the worker decrypts it on
    claim. Filing, the approval row and ``execution.approval_requested`` share
    one admin-DB transaction; an identical pending request joins its existing
    hold (even at the cap), and an agent at its pending cap is otherwise denied
    before anything is written. The fingerprint a join matches on leaves out the
    ``Cookie``, so a joined hold runs with the first request's captured cookie
    and the joining request's cookie is not kept.
    """
    if not ctx.has_encryption_keyset:
        raise ApprovalHoldUnavailableError(
            "This call needs human approval, but the deployment has no credentials "
            "encryption keyset to keep the held request in (credentials.encryption)",
            type="approval_hold_unavailable",
            instance=request.url.path,
        )
    cfg = ctx.config.held_executions
    credential_id = authorization.selected_credential.credential_id
    parsed_url = urlparse(ctx_req.upstream_url)
    path = parsed_url.path
    execution_id = mint_execution_id()
    body = await _read_request_body(request, ctx_req.method, ctx)
    raw_payload = _async_job_payload(
        ctx_req,
        execution_id=execution_id,
        origin=identity.origin.value,
        selected_credential_id=credential_id,
        allowed_credential_ids=authorization.allowed_credential_ids,
        body=body,
        headers=replay_headers(request.headers),
        inbound_cookie_enc=_captured_cookie(request, ctx),
    )
    payload: dict[str, object] = {
        ENCRYPTED_PAYLOAD_KEY: ctx.encryption.encrypt(json.dumps(raw_payload))
    }

    async with ctx.admin_db.transaction() as session:
        try:
            hold = await file_hold(
                session,
                agent_id=identity.sub,
                actor_type=identity.actor_type.value,
                credential_id=credential_id,
                matched_rule_id=authorization.matched_rule_id,
                api_vendor=ctx_req.api_vendor or "",
                api_name=ctx_req.api_name or "",
                api_version=ctx_req.api_version or "",
                operation_id=ctx_req.operation_id,
                method=ctx_req.method,
                path=path,
                query=parsed_url.query,
                body=body,
                trace_id=valid_trace_id_or_none(ctx_req.trace_id),
                execution_id=execution_id,
                payload=payload,
                ttl_seconds=cfg.ttl_seconds,
                max_pending=cfg.max_pending_per_agent,
            )
        except PendingApprovalLimitError as exc:
            raise ApprovalPendingLimitError(
                f"Agent '{identity.sub}' already has {exc.pending} executions pending "
                f"approval (limit {exc.limit})",
                type="approval_pending_limit_reached",
                extra={"pending": exc.pending, "limit": exc.limit},
                directive=approval_pending_limit_directive(pending=exc.pending, limit=exc.limit),
                instance=request.url.path,
            ) from exc
        review_url = _review_url(ctx, request, hold.approval_id)
        if not hold.joined:
            # An identity resolved from a JWT carries no name; read it from the
            # agent row then, so every approval event names the agent alike.
            name = identity.actor_name or await held_agent_name(session, identity.sub)
            agent = summary_label(name, identity.sub)
            await emit_event_best_effort(
                session,
                type=EventType.EXECUTION_APPROVAL_REQUESTED,
                severity=EventSeverity.WARNING,
                summary=f"Agent {agent} wants to {ctx_req.method} {summary_text(path)}",
                requires_action=True,
                trace_id=valid_trace_id_or_none(ctx_req.trace_id),
                execution_id=execution_id,
                job_id=hold.job_id,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "held_execution_id": hold.approval_id,
                    "agent_id": identity.sub,
                    "method": ctx_req.method,
                    "path": path,
                    "api_vendor": ctx_req.api_vendor,
                    "api_name": ctx_req.api_name,
                    "api_version": ctx_req.api_version,
                    "review_url": review_url,
                    "expires_at": hold.expires_at.isoformat(),
                },
            )

    logger.info(
        "execution_held",
        approval_id=hold.approval_id,
        job_id=hold.job_id,
        joined=hold.joined,
        method=ctx_req.method,
    )
    base = ctx.config.broker.jobs_api_base_url or ""
    held = f"{base}/executions/holds/{hold.approval_id}"
    resp_body = HeldExecutionAcceptedResponse(
        held_execution_id=hold.approval_id,
        review_url=review_url,
        expires_at=hold.expires_at,
        agent_directive=HELD_AGENT_DIRECTIVE,
        links=HeldExecutionAcceptedLinks(
            self_link=held, result=f"{held}/result", withdraw=f"{held}:withdraw"
        ),
    )
    return Response(
        content=resp_body.model_dump_json(by_alias=True),
        status_code=202,
        media_type="application/json",
        headers={
            **_metadata_headers(ctx_req, hold.execution_id or execution_id),
            "Preference-Applied": "respond-async",
        },
    )


async def _handle_async(
    request: Request,
    ctx_req: ExecuteRequestContext,
    ctx: Context,
    identity: Identity,
    *,
    selected_credential_id: str | None = None,
    allowed_credential_ids: list[str] | None = None,
) -> Response:
    """Enqueue an async (202) execution. The worker shares the same pipeline.

    The payload carries the injection boundary (``allowed_credential_ids`` —
    the caller's bound credentials) and the selected credential id, so the worker's
    injection replays the same selection under the same boundary (Q-02) —
    rules were already enforced here at the edge.
    """
    execution_id = mint_execution_id()
    body = await _read_request_body(request, ctx_req.method, ctx)

    payload = _async_job_payload(
        ctx_req,
        execution_id=execution_id,
        origin=identity.origin.value,
        selected_credential_id=selected_credential_id,
        allowed_credential_ids=allowed_credential_ids,
        body=body,
        headers=replay_headers(request.headers),
        inbound_cookie_enc=_captured_cookie(request, ctx),
    )

    async with ctx.admin_db.transaction() as session:
        job_id = await enqueue_job(
            session,
            JobKind.EXECUTION,
            created_by=identity.sub,
            actor_type=identity.actor_type,
            execution_id=execution_id,
            payload=payload,
        )

    metadata = _metadata_headers(ctx_req, execution_id)
    base = ctx.config.broker.jobs_api_base_url
    job_url = f"{base}/jobs/{job_id}" if base else f"/jobs/{job_id}"

    resp_body = AsyncQueuedResponse(
        job_id=job_id,
        links=AsyncQueuedResponseLinks(self_link=job_url),
    )
    return Response(
        content=resp_body.model_dump_json(by_alias=True),
        status_code=202,
        media_type="application/json",
        headers={**metadata, "Preference-Applied": "respond-async"},
    )


@router.api_route(
    "/{upstream_url:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    summary="Execute an upstream API operation",
)
async def proxy(
    upstream_url: str,
    request: Request,
    identity: RequireExecuteAccess,
    credential_deriver: CredentialDeriver,
    agent_rule_evaluator: AgentRuleEvaluatorDep,
    runner: HttpRunnerDep,
    idempotency: IdempotencyStoreDep,
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Proxy a request to a registered upstream API operation."""
    return await _handle(
        request,
        request.method,
        ctx,
        identity,
        credential_deriver,
        agent_rule_evaluator,
        runner,
        idempotency,
    )


# ``switch_toolkit_directive`` is re-exported here for callers that already
# import this router module.
__all__ = ["router", "switch_toolkit_directive"]
