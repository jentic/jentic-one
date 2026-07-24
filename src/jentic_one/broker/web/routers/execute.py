"""Broker catch-all proxy route.

A single ``/{upstream_url:path}`` catch-all replaces the six per-method
``/execute`` handlers and all inbound ``Jentic-Api-*`` parsing. ``_handle`` is a
thin **web-edge adapter**: URL reconstruction → upgrade reject → SSRF pre-check →
in-process discovery → credential resolution → header assembly → **delegate to
the shared ``BrokerExecutionPipeline``** → adapt the result to a ``Response``
(mirroring the upstream status, passing headers through, adding ``Jentic-*``).
No resilience/credential/post-processing logic is inlined here — those are
pipeline stages / runner decorators (added in later PRs).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import structlog
from fastapi import APIRouter, Depends, Request, Response
from jentic.problem_details import Forbidden

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    AmbiguousMatchError,
    BrokerError,
    CredentialIdentityMismatchError,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    OperationNotFoundError,
    PayloadTooLargeError,
    UpgradeNotSupportedError,
    UpstreamUrlNotAllowedError,
    action_denied_directive,
    ambiguous_toolkit_directive,
    credential_identity_mismatch_directive,
    no_toolkit_binding_directive,
    switch_toolkit_directive,
)
from jentic_one.broker.core.execution import mint_execution_id
from jentic_one.broker.core.headers import (
    JENTIC_REVISION_HEADER,
    REGION_MISMATCH_HINT,
    TRACESTATE_HEADER,
    JenticHeader,
)
from jentic_one.broker.core.idempotency import fingerprint
from jentic_one.broker.core.proxy_headers import (
    forward_headers,
    passthrough_response_headers,
    reconstruct_upstream_url,
)
from jentic_one.broker.core.revisions import parse_revisions
from jentic_one.broker.core.schemas import (
    AsyncQueuedResponse,
    AsyncQueuedResponseLinks,
    ExecuteRequestContext,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.discovery import discover, resolve_pin_for_api
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
    HttpRunnerDep,
    IdempotencyStoreDep,
    RequireToolkitAccess,
    RuleEvaluatorDep,
    ToolkitDeriver,
)
from jentic_one.broker.web.streaming import StreamingOutcome
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.broker.protocols import (
    RegistryResolverProtocol,
    ResolveResult,
    RuleEvaluatorProtocol,
    ToolkitDerivation,
    ToolkitDeriverProtocol,
)
from jentic_one.shared.config import UpstreamClientConfig
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.jobs.protocols import InjectedAuth
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ActorType, ExecutionStatus
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.schemas import APIReference
from jentic_one.shared.tracing import JENTIC_TRACESTATE_KEY, pack_jentic_tracestate
from jentic_one.shared.url import apply_server_variables, has_host_server_variable
from jentic_one.shared.url_validation import validate_upstream_url
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

_meter = get_meter("broker")
_streaming_persist_failures = _meter.create_counter(
    "broker.streaming_execution.persist_failures",
    description="Failed attempts to persist a streaming execution record",
)

router = APIRouter()

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


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
    handler.
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
    except Exception:
        _streaming_persist_failures.add(1)
        logger.error(
            "streaming_execution_persist_failed",
            execution_id=outcome.execution_id,
            exc_info=True,
        )


def _resolve_body_cap(content_type: str | None, cfg: UpstreamClientConfig) -> int:
    """Resolve the body cap for a request from its Content-Type (§04).

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


def _context_from_discovery(
    *, upstream_url: str, method: str, request: Request, resolved: ResolveResult
) -> ExecuteRequestContext:
    headers = request.headers
    return ExecuteRequestContext(
        upstream_url=upstream_url,
        method=method,
        trace_id=headers.get("traceparent", headers.get("x-request-id", "unknown")),
        # toolkit_id is intentionally left unset here — it is derived from the
        # discovered API identity by ``select_toolkit`` after discovery (§03),
        # never taken verbatim from the inbound header.
        toolkit_id=None,
        operation_id=resolved.operation_id,
        api_vendor=resolved.api.vendor,
        api_name=resolved.api.name,
        api_version=resolved.api.version,
        prefer=headers.get("prefer"),
        pinned_revisions=None,
    )


def _empty_derivation_denial(
    d: ToolkitDerivation, api: APIReference, *, instance: str
) -> BrokerError:
    """Pick the right denial for an empty toolkit derivation (#683 + #747/#748).

    Two cases, distinguished by the structured derivation result — each with its
    own ``detail`` so the problem+json ``type`` and ``detail`` never tell
    different stories (e.g. a ``credential_identity_mismatch`` must not carry a
    "not bound to toolkit" detail):

    - Bound + a bound credential is a near-miss for the API → the credential's
      identity does not cover the operation (#747/#748). Fix the *credential*,
      never file an access request (that auto-denies).
    - Otherwise → ``no_toolkit_binding``, whose recovery (file a bind request vs.
      provision a credential first) is chosen by ``no_toolkit_binding_directive``
      from whether any toolkit serves the API at all (#683).
    """
    serves = bool(d.api_served_toolkits)
    if d.agent_bound_any and not serves and d.identity_mismatch is not None:
        return CredentialIdentityMismatchError(
            "A bound credential's identity does not cover this API",
            type="credential_identity_mismatch",
            instance=instance,
            directive=credential_identity_mismatch_directive(mismatch=d.identity_mismatch),
        )
    return ActionDeniedError(
        "No toolkit binding for this API",
        type="no_toolkit_binding",
        instance=instance,
        directive=no_toolkit_binding_directive(
            vendor=api.vendor, name=api.name, version=api.version, toolkit_serves_api=serves
        ),
    )


async def select_toolkit(
    *,
    deriver: ToolkitDeriverProtocol,
    identity: Identity,
    api: APIReference,
    header_toolkit: str | None,
    instance: str,
) -> str:
    """Derive the toolkit for this execution from the caller's bindings (§03).

    ``0 → 403`` (no binding / credential identity mismatch), ``1 → use it``,
    ``N → 409`` (caller must disambiguate with ``Jentic-Toolkit-Id``). A supplied
    header is validated against the derived candidates; never silently honoured or
    silently picked.

    Non-agent actors (service accounts, users) currently follow the **same**
    derivation rule — there is no implicit bypass. Broadening this for service
    accounts is an explicit future decision, not an accident (§03/3).

    A **toolkit key** (``ActorType.TOOLKIT``) is the exception: it authenticates
    *as the toolkit itself*, so the agent→binding derivation does not apply — the
    key already names its toolkit (``identity.sub``). A supplied ``Jentic-Toolkit-Id``
    must match it; otherwise the request is rejected.
    """
    if identity.actor_type is ActorType.TOOLKIT:
        if header_toolkit and header_toolkit != identity.sub:
            # Non-recoverable: a toolkit key authenticates *as* one specific
            # toolkit, so there is no other binding to switch to and no human
            # grant that would help. A bare Forbidden (no agent_directive) is
            # correct here — the caller must fix its own request.
            raise Forbidden(
                detail="Jentic-Toolkit-Id does not match the authenticated toolkit key",
                instance=instance,
                type="toolkit_binding_required",
            )
        return identity.sub

    # Invariant: the API identity here is the *discovered* spec identity, which is
    # always concrete (vendor/name/version all set) — the registry never yields a
    # wildcard. Derivation and the nearest-miss diagnostic (#748) rely on this
    # (they slugify and compare each axis), so assert it at the boundary rather
    # than silently deriving against a blank axis.
    assert api.vendor and api.name and api.version, (
        "select_toolkit requires a concrete discovered API identity"
    )

    derivation = await deriver.derive_toolkits(
        agent_id=identity.sub,
        vendor=api.vendor,
        name=api.name,
        version=api.version,
    )
    candidates = list(derivation.toolkits)

    if header_toolkit:
        if header_toolkit not in candidates:
            # Recoverable: the agent named a toolkit it isn't bound to. Carry the
            # agent-recovery contract like every other broker denial — point it at
            # the toolkits it *is* bound to (switch_toolkit) or, if it has none,
            # at the correct provisioning/binding/credential-fix step. A bare
            # Forbidden here would be a dead-end 403 with no directive (§03 invariant).
            if candidates:
                raise ActionDeniedError(
                    f"Not bound to toolkit '{header_toolkit}' for this API",
                    type="toolkit_binding_required",
                    instance=instance,
                    directive=ambiguous_toolkit_directive(candidates),
                )
            raise _empty_derivation_denial(derivation, api, instance=instance)
        return header_toolkit

    if not candidates:
        raise _empty_derivation_denial(derivation, api, instance=instance)
    if len(candidates) > 1:
        raise AmbiguousMatchError(
            "Multiple toolkits match this API; resend with the Jentic-Toolkit-Id header.",
            type="ambiguous_toolkit",
            instance=instance,
            extra={
                "errors": [
                    {
                        "detail": f"Candidate toolkit '{tk}'.",
                        "header": "Jentic-Toolkit-Id",
                        "code": tk,
                    }
                    for tk in candidates
                ]
            },
            directive=ambiguous_toolkit_directive(candidates),
        )
    return candidates[0]


def _metadata_headers(ctx_req: ExecuteRequestContext, execution_id: str) -> dict[str, str]:
    meta: dict[str, str] = {JenticHeader.EXECUTION_ID.value: execution_id}
    if ctx_req.toolkit_id:
        meta[JenticHeader.TOOLKIT_ID.value] = ctx_req.toolkit_id
    if ctx_req.operation_id:
        meta[JenticHeader.OPERATION.value] = ctx_req.operation_id
    if ctx_req.api_vendor:
        meta[JenticHeader.API_VENDOR.value] = ctx_req.api_vendor
    # Echo the jentic= tracestate member (same who/what payload as the outbound
    # request) so a caller can correlate the response to its distributed trace
    # without re-deriving it (§04 / OpenAPI Tracestate).
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
) -> InjectedAuth:
    """Resolve + inject credentials via the shared ``CredentialService`` (§02b)."""
    return await CredentialService(ctx).inject(
        api_vendor=ctx_req.api_vendor or "",
        api_name=ctx_req.api_name or "",
        api_version=ctx_req.api_version or "",
        identity=identity,
        credential_name=credential_name,
    )


def _apply_injection(
    upstream_url: str, injection: InjectedAuth, request: Request
) -> tuple[str, dict[str, str]]:
    """Apply injected auth to the outbound URL + headers.

    Server-variable creds are substituted into the URL template; query-param
    creds are merged into the URL query; cookie creds are **appended** to the
    inbound ``Cookie`` header (never overwriting forwarded cookies, §02 §5).
    """
    if injection.server_variables:
        upstream_url = apply_server_variables(upstream_url, injection.server_variables)

    if injection.query_params:
        parsed = urlparse(upstream_url)
        sep = "&" if parsed.query else ""
        new_query = parsed.query + sep + urlencode(injection.query_params)
        upstream_url = urlunparse(parsed._replace(query=new_query))

    headers = dict(injection.headers)
    if injection.cookies:
        injected = "; ".join(f"{name}={value}" for name, value in injection.cookies.items())
        existing = request.headers.get("cookie")
        headers["Cookie"] = f"{existing}; {injected}" if existing else injected
    return upstream_url, headers


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


async def _handle(
    request: Request,
    method: str,
    ctx: Context,
    identity: Identity,
    deriver: ToolkitDeriverProtocol,
    rule_evaluator: RuleEvaluatorProtocol,
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
        upstream_url = validate_upstream_url(raw_url, ctx.config.broker.egress)
    except ValueError as exc:
        raise UpstreamUrlNotAllowedError(detail=str(exc), type="invalid_upstream_url") from exc

    resolver: RegistryResolverProtocol = request.app.state.broker_registry_resolver

    # §10: parse the multi-valued Jentic-Revision header at the edge. A malformed
    # value raises InvalidRevisionPinError (→ 422) here, before any registry
    # lookup — never an uncaught 500 mid-discovery.
    pins = parse_revisions(_revision_header(request))

    resolved = await discover(resolver, method=method, url=upstream_url)
    if resolved is None:
        raise OperationNotFoundError(
            detail="Operation not found — unregistered upstream URL.",
            type="operation_not_found",
        )

    # §10: if a pin applies to the discovered API, translate it to a revision_id
    # in-process (no control-plane HTTP) and re-resolve against the pinned spec.
    pinned_revisions: dict[str, str] | None = None
    if pins:
        revision_id = await resolve_pin_for_api(
            resolver, api=resolved.api, pins=pins, identity=identity
        )
        if revision_id is not None:
            pinned = await discover(
                resolver, method=method, url=upstream_url, revision_id=revision_id
            )
            if pinned is None:
                raise OperationNotFoundError(
                    detail="Operation not found in the pinned revision.",
                    type="operation_not_found",
                )
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
    # Toolkit is derived from the discovered API identity (never the inbound header
    # verbatim); drives credential injection and execution attribution (§03).
    ctx_req.toolkit_id = await select_toolkit(
        deriver=deriver,
        identity=identity,
        api=resolved.api,
        header_toolkit=request.headers.get("jentic-toolkit-id"),
        instance=request.url.path,
    )

    # Evaluate toolkit permission rules — default-deny when no rule matches.
    # Unconditional: even if toolkit_id were empty the evaluator returns a
    # zero-rules-loaded denial, preserving the secure-by-default posture.
    evaluation = await rule_evaluator.evaluate(
        toolkit_id=ctx_req.toolkit_id,
        method=method,
        path=urlparse(upstream_url).path,
        operation_id=resolved.operation_id,
        api_vendor=resolved.api.vendor,
    )
    if not evaluation.allowed:
        # #578: distinguish the two deny paths in the caller-visible detail.
        # ``rules_loaded == 0`` means the vendor-pooled rule set is empty —
        # nothing to match (wrong vendor, empty binding, misconfigured store);
        # otherwise we loaded rules but none matched the request shape. Both
        # branches emit ``PBAC_DENIED`` telemetry with the corresponding
        # summary so operators can grep for the branch.
        no_rules = evaluation.rules_loaded == 0
        summary = (
            "Operation denied by toolkit permission rule (no rules loaded for this vendor)"
            if no_rules
            else "Operation denied by toolkit permission rule (no rule matched)"
        )
        detail = (
            "The requested operation is denied — this toolkit has no permission rules "
            "loaded for the target API's vendor. Attach rules to the vendor's binding "
            "under PUT /toolkits/{toolkit_id}/credentials/{credential_id}/permissions."
            if no_rules
            else "The requested operation is denied by a toolkit permission rule."
        )
        try:
            async with ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.PBAC_DENIED,
                    severity=EventSeverity.WARNING,
                    summary=summary,
                    created_by=identity.sub,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                )
        except Exception:
            logger.warning("telemetry_emit_failed", event_type=EventType.PBAC_DENIED, exc_info=True)
        raise ActionDeniedError(
            detail=detail,
            type="action_denied",
            instance=request.url.path,
            directive=action_denied_directive(),
        )

    if _should_async(ctx_req.prefer):
        return await _handle_async(request, ctx_req, ctx, identity)

    idem_key = request.headers.get("idempotency-key")
    upstream_cfg = ctx.config.broker.resilience.upstream

    # §08 E2.4: stream the response straight through for sync, non-idempotent
    # requests — idempotent requests fall to the buffered path below because
    # replay needs the whole body. Disabled requests / non-streaming runners
    # also fall through.
    if (
        upstream_cfg.stream_passthrough_enabled
        and not (idempotency is not None and idem_key)
        and isinstance(runner, StreamingUpstreamRunner)
    ):
        return await _handle_streaming(request, ctx_req, ctx, identity, runner, upstream_cfg)

    # Buffer the body once (needed for the idempotency fingerprint and the call).
    body = await _read_request_body(request, method, ctx)

    # §07: claim/replay on Idempotency-Key. Async same-job_id replay is a later
    # slice — only the sync path is idempotent here.
    fp: str | None = None
    if idempotency is not None and idem_key:
        fp = fingerprint(method, ctx_req.upstream_url, ctx_req.toolkit_id or "", body)
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

    credential_name = request.headers.get("jentic-credential-name")
    injection = await _resolve_credentials(ctx_req, ctx, identity, credential_name)
    ctx_req.upstream_url, auth_headers = _apply_injection(ctx_req.upstream_url, injection, request)
    ctx_req.credential_id = injection.credential_id
    ctx_req.credential_name = injection.credential_name
    if injection.server_variables:
        try:
            validate_upstream_url(ctx_req.upstream_url, ctx.config.broker.egress)
        except ValueError as exc:
            raise UpstreamUrlNotAllowedError(detail=str(exc), type="invalid_upstream_url") from exc
    forwarded = forward_headers(request.headers, auth_headers)

    async with ctx.admin_db.transaction() as session:
        broker = _resolve_broker(request, runner)
        outcome = await run_execution(
            ctx_req,
            body=body,
            headers=forwarded,
            session=session,
            timeout=ctx.config.broker.upstream_timeout_s,
            broker=broker,
            actor_id=identity.sub,
            actor_type=identity.actor_type.value,
            origin=identity.origin.value,
            security_config=ctx.config.security,
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


async def _handle_streaming(
    request: Request,
    ctx_req: ExecuteRequestContext,
    ctx: Context,
    identity: Identity,
    runner: StreamingUpstreamRunner,
    upstream_cfg: UpstreamClientConfig,
) -> Response:
    """Sync, non-idempotent streaming passthrough (§08 E2.4).

    Same credential resolution + header assembly as the buffered path, but the
    upstream body streams straight to the client (no whole-buffering) under the
    response-size cap + transfer deadline + client-disconnect teardown owned by
    ``open_streaming_response``.

    Persistence is best-effort via a Starlette BackgroundTask that fires after the
    streaming body completes (or errors). A DB failure is logged + counted but does
    not affect the already-sent response.
    """
    body = await _read_request_body(request, ctx_req.method, ctx)
    credential_name = request.headers.get("jentic-credential-name")
    injection = await _resolve_credentials(ctx_req, ctx, identity, credential_name)
    ctx_req.upstream_url, auth_headers = _apply_injection(ctx_req.upstream_url, injection, request)
    ctx_req.credential_id = injection.credential_id
    ctx_req.credential_name = injection.credential_name
    if injection.server_variables:
        try:
            validate_upstream_url(ctx_req.upstream_url, ctx.config.broker.egress)
        except ValueError as exc:
            raise UpstreamUrlNotAllowedError(detail=str(exc), type="invalid_upstream_url") from exc
    forwarded = forward_headers(request.headers, auth_headers)

    execution_id = mint_execution_id()
    started_at = datetime.now(UTC)
    runner_request = RunnerRequest(
        method=ctx_req.method,
        url=ctx_req.upstream_url,
        headers=forwarded,
        body=body,
        timeout_s=ctx.config.broker.upstream_timeout_s,
    )

    async def _persist_callback(outcome: StreamingOutcome) -> None:
        await _persist_streaming_outcome(
            outcome,
            ctx,
            ctx_req,
            started_at,
            identity.sub,
            identity.actor_type.value,
            origin=identity.origin.value,
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
    (§6b B-002 passthrough invariant).
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
    """Re-emit a stored idempotent response, tagged ``Idempotent-Replayed: true`` (§07).

    The stored headers (which already carry the original ``Jentic-*`` metadata)
    were scrubbed of sensitive values + body-encoding headers on the original
    completion; an oversized original (``body_omitted``) replays its
    status/headers with an empty body and a marker header so the caller knows the
    body wasn't cached (the no-duplicate-side-effect guarantee still held).
    """
    headers = dict(stored.headers)
    headers[JenticHeader.IDEMPOTENT_REPLAYED.value] = "true"
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


async def _handle_async(
    request: Request,
    ctx_req: ExecuteRequestContext,
    ctx: Context,
    identity: Identity,
) -> Response:
    """Enqueue an async (202) execution. The worker shares the same pipeline."""
    execution_id = mint_execution_id()
    body = await _read_request_body(request, ctx_req.method, ctx)

    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "upstream_url": ctx_req.upstream_url,
        "method": ctx_req.method,
        "toolkit_id": ctx_req.toolkit_id,
        "trace_id": ctx_req.trace_id,
        "operation_id": ctx_req.operation_id,
        "api_vendor": ctx_req.api_vendor,
        "api_name": ctx_req.api_name,
        "api_version": ctx_req.api_version,
        "origin": identity.origin.value,
    }
    if ctx_req.pinned_revisions:
        payload["pinned_revisions"] = ctx_req.pinned_revisions
    if body:
        payload["body_b64"] = base64.b64encode(body).decode()

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
    identity: RequireToolkitAccess,
    deriver: ToolkitDeriver,
    rule_evaluator: RuleEvaluatorDep,
    runner: HttpRunnerDep,
    idempotency: IdempotencyStoreDep,
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Proxy a request to a registered upstream API operation."""
    return await _handle(
        request, request.method, ctx, identity, deriver, rule_evaluator, runner, idempotency
    )


# ``switch_toolkit_directive`` is re-exported for the worker / mirrored-error
# enrichment in later PRs.
__all__ = ["router", "switch_toolkit_directive"]
