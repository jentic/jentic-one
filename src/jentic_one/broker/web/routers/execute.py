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

import base64
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import structlog
from fastapi import APIRouter, Depends, Request, Response
from starlette.datastructures import Headers

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.broker.core.denial import DenialReason
from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    BrokerError,
    CredentialIdentityMismatchError,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    OperationNotFoundError,
    PayloadTooLargeError,
    UpgradeNotSupportedError,
    UpstreamUrlNotAllowedError,
    direct_action_denied_directive,
    direct_credential_identity_mismatch_directive,
    no_credential_binding_directive,
    switch_toolkit_directive,
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
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
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
    CredentialDerivation,
    CredentialDeriverProtocol,
    RegistryResolverProtocol,
    ResolveResult,
)
from jentic_one.shared.config import UpstreamClientConfig
from jentic_one.shared.context import Context
from jentic_one.shared.events import (
    emit_event_best_effort,
    mint_trace_id,
    valid_trace_id_or_none,
)
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.jobs.protocols import InjectedAuth
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.schemas import APIReference
from jentic_one.shared.tracing import (
    JENTIC_TRACESTATE_KEY,
    current_trace_id,
    pack_jentic_tracestate,
)
from jentic_one.shared.url import apply_server_variables, has_host_server_variable
from jentic_one.shared.url_validation import validate_upstream_url
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

_meter = get_meter("broker")
_streaming_persist_failures = _meter.create_counter(
    "broker.streaming_execution.persist_failures",
    description="Failed attempts to persist a streaming execution record",
)
# Denial observability (theme-5): every authorization denial increments this
# counter with a closed-enum ``reason`` (DenialReason) + the API ``vendor`` and
# the authorization ``mode`` (always ``direct`` since Phase 6b removed the
# toolkit path; the label survives so dashboards keep their shape), so denials
# can be watched as a rate delta per reason rather than grepping summaries.
_authz_denied = _meter.create_counter(
    "broker.authorization.denied",
    description="Execute requests denied by the authorization layer, by reason",
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
        operation_id=resolved.operation_id,
        api_vendor=resolved.api.vendor,
        api_name=resolved.api.name,
        api_version=resolved.api.version,
        prefer=headers.get("prefer"),
        pinned_revisions=None,
    )


def _empty_credential_derivation_denial(
    d: CredentialDerivation, api: APIReference, *, instance: str
) -> BrokerError:
    """Pick the right denial for an empty credential derivation (direct path).

    The direct-binding twin of :func:`_empty_derivation_denial` — two cases,
    each with its own ``detail`` so the problem+json ``type`` and ``detail``
    never tell different stories:

    - Bound + a bound credential is a near-miss for the API → the credential's
      identity does not cover the operation (#747/#748 twin). Fix the
      *credential*, never request another binding.
    - Otherwise → ``no_credential_binding``, whose recovery (grant a binding
      vs. provision a credential first) is chosen by
      :func:`no_credential_binding_directive` from whether any credential
      serves the API at all.
    """
    if d.agent_bound_any and not d.api_served and d.identity_mismatch is not None:
        _authz_denied.add(
            1,
            {
                "reason": DenialReason.CREDENTIAL_IDENTITY_MISMATCH.value,
                "mode": "direct",
                "vendor": api.vendor,
            },
        )
        return CredentialIdentityMismatchError(
            "A bound credential's identity does not cover this API",
            type="credential_identity_mismatch",
            instance=instance,
            directive=direct_credential_identity_mismatch_directive(mismatch=d.identity_mismatch),
        )
    _authz_denied.add(
        1,
        {
            "reason": DenialReason.NO_CREDENTIAL_BINDING.value,
            "mode": "direct",
            "vendor": api.vendor,
        },
    )
    return ActionDeniedError(
        "No credential binding for this API",
        type="no_credential_binding",
        instance=instance,
        directive=no_credential_binding_directive(
            vendor=api.vendor, name=api.name, version=api.version, api_served=d.api_served
        ),
    )


async def _emit_credential_binding_unserved(
    ctx: Context, *, api: APIReference, identity: Identity
) -> None:
    """Emit ``CREDENTIAL_BINDING_UNSERVED`` best-effort.

    Fires once per denied execute request when nothing serves the API at all —
    the operator-attention case (a credential must be provisioned before any
    binding can be granted). The caller's own retry loop will re-emit —
    acceptable at WARNING severity, consistent with how
    ``CREDENTIAL_NOT_PROVISIONED`` (424) already emits per attempt.
    """
    api_id = "/".join(part for part in (api.vendor, api.name) if part) or api.vendor
    summary = f"No credential serves API '{api_id}' — provision one to enable binding."
    try:
        async with ctx.admin_db.transaction() as session:
            await emit_event_best_effort(
                session,
                type=EventType.CREDENTIAL_BINDING_UNSERVED,
                severity=EventSeverity.WARNING,
                summary=summary,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "api": {
                        "vendor": api.vendor,
                        "name": api.name,
                        "version": api.version,
                    },
                },
            )
    except Exception:
        logger.warning(
            "telemetry_emit_failed",
            event_type=EventType.CREDENTIAL_BINDING_UNSERVED,
            exc_info=True,
        )


async def derive_credential_bindings(
    *,
    deriver: CredentialDeriverProtocol,
    identity: Identity,
    api: APIReference,
    instance: str,
    ctx: Context,
) -> CredentialDerivation:
    """Derive the caller's credential-binding candidates for this execution.

    ``0 → 403`` (no binding / credential identity mismatch — with the
    operator-visible ``CREDENTIAL_BINDING_UNSERVED`` emit for the pre-binding
    nothing-serves case). Candidate *selection* among ``N ≥ 1`` (name header →
    most-specific-wins → ``Jentic-Credential-Id`` tie-breaker → 409) is owned
    by ``CredentialService.select``, which shares the resolver with injection
    so selection and injection can never disagree.

    Non-agent actors (service accounts, users) follow the **same** derivation
    rule — no implicit bypass. Retired toolkit keys' successors (``sak_``
    service-account keys, theme-5 Phase 4) derive here like any other caller.
    """
    assert api.vendor and api.name and api.version, (
        "derive_credential_bindings requires a concrete discovered API identity"
    )
    derivation = await deriver.derive_credentials(
        agent_id=identity.sub,
        vendor=api.vendor,
        name=api.name,
        version=api.version,
    )
    if not derivation.credentials:
        denial = _empty_credential_derivation_denial(derivation, api, instance=instance)
        # The operator-visible pre-binding signal fires only for the plain
        # no-binding + nothing-serves case — an identity mismatch already has
        # its own actionable diagnostic.
        if not derivation.api_served and isinstance(denial, ActionDeniedError):
            await _emit_credential_binding_unserved(ctx, api=api, identity=identity)
        raise denial
    return derivation


def _metadata_headers(ctx_req: ExecuteRequestContext, execution_id: str) -> dict[str, str]:
    meta: dict[str, str] = {JenticHeader.EXECUTION_ID.value: execution_id}
    if ctx_req.operation_id:
        meta[JenticHeader.OPERATION.value] = ctx_req.operation_id
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
) -> InjectedAuth:
    """Resolve + inject credentials via the shared ``CredentialService``.

    ``preresolved`` carries the direct path's already-selected credential so
    injection never re-resolves (and cannot pick a different credential than
    the one the rules were evaluated against).
    """
    return await CredentialService(ctx).inject(
        api_vendor=ctx_req.api_vendor or "",
        api_name=ctx_req.api_name or "",
        api_version=ctx_req.api_version or "",
        identity=identity,
        credential_name=credential_name,
        trace_id=ctx_req.trace_id,
        preresolved=preresolved,
    )


def _apply_injection(
    upstream_url: str, injection: InjectedAuth, request: Request
) -> tuple[str, dict[str, str]]:
    """Apply injected auth to the outbound URL + headers.

    Server-variable creds are substituted into the URL template; query-param
    creds are merged into the URL query; cookie creds are **appended** to the
    inbound ``Cookie`` header (never overwriting forwarded cookies).
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
        upstream_url = validate_upstream_url(raw_url, ctx.config.broker.egress)
    except ValueError as exc:
        raise UpstreamUrlNotAllowedError(detail=str(exc), type="invalid_upstream_url") from exc

    resolver: RegistryResolverProtocol = request.app.state.broker_registry_resolver

    # Parse the multi-valued Jentic-Revision header at the edge. A malformed
    # value raises InvalidRevisionPinError (→ 422) here, before any registry
    # lookup — never an uncaught 500 mid-discovery.
    pins = parse_revisions(_revision_header(request))

    resolved = await discover(resolver, method=method, url=upstream_url)
    if resolved is None:
        raise OperationNotFoundError(
            detail="Operation not found — unregistered upstream URL.",
            type="operation_not_found",
        )

    # If a pin applies to the discovered API, translate it to a revision_id
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
    # Direct agent→credential bindings (theme-5): derive candidates (0 → 403
    # with the right directive), then select the single credential (name
    # header → most-specific-wins → Jentic-Credential-Id tie-breaker; genuine
    # tie → 409), then enforce the binding's rules — all before any secret is
    # decrypted or audited. Every caller kind rides this path: toolkit keys,
    # the one identity that bypassed it, were retired (Phase 4) and their
    # ``sak_`` service-account keys resolve here like any other actor; the
    # legacy toolkit-derivation path was deleted in Phase 6b.
    derivation = await derive_credential_bindings(
        deriver=credential_deriver,
        identity=identity,
        api=resolved.api,
        instance=request.url.path,
        ctx=ctx,
    )
    allowed_credential_ids = [bc.credential_id for bc in derivation.credentials]
    rule_set_ids = {bc.credential_id: bc.rule_set_id for bc in derivation.credentials}
    selected_credential = await CredentialService(ctx).select(
        api_vendor=resolved.api.vendor,
        api_name=resolved.api.name,
        api_version=resolved.api.version,
        identity=identity,
        credential_name=request.headers.get("jentic-credential-name"),
        credential_id=request.headers.get("jentic-credential-id"),
        allowed_credential_ids=allowed_credential_ids,
    )
    assert selected_credential is not None  # api.vendor is concrete (asserted above)
    # Attribution is known at selection time on this path, so the 202/
    # streaming metadata carries it too (the buffered path re-stamps the
    # same values from the injection result).
    ctx_req.credential_id = selected_credential.credential_id
    ctx_req.credential_name = selected_credential.name

    evaluation = await agent_rule_evaluator.evaluate(
        agent_id=identity.sub,
        credential_id=selected_credential.credential_id,
        rule_set_id=rule_set_ids.get(selected_credential.credential_id),
        method=method,
        path=urlparse(upstream_url).path,
        operation_id=resolved.operation_id,
    )
    if not evaluation.allowed:
        # Two-variant deny split (#578): an empty rule list (nothing
        # configured for this binding) vs loaded rules where none allowed
        # the request.
        no_rules = evaluation.rules_loaded == 0
        reason = DenialReason.NO_RULES_LOADED if no_rules else DenialReason.NO_RULE_MATCHED
        _authz_denied.add(
            1,
            {"reason": reason.value, "mode": "direct", "vendor": resolved.api.vendor},
        )
        summary = (
            "Operation denied by credential-binding permission rules (no rules "
            "configured for this binding)"
            if no_rules
            else "Operation denied by credential-binding permission rules (no rule matched)"
        )
        detail = (
            "The requested operation is denied — this credential binding has no "
            "permission rules configured. Attach rules under "
            "PUT /credentials/{credential_id}/agents/{agent_id}/permissions "
            "or attach a rule set."
            if no_rules
            else "The requested operation is denied by a credential-binding permission rule."
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
                    data={"reason": reason.value, "mode": "direct"},
                )
        except Exception:
            logger.warning("telemetry_emit_failed", event_type=EventType.PBAC_DENIED, exc_info=True)
        raise ActionDeniedError(
            detail=detail,
            type="action_denied",
            instance=request.url.path,
            directive=direct_action_denied_directive(),
        )

    if _should_async(ctx_req.prefer):
        return await _handle_async(
            request,
            ctx_req,
            ctx,
            identity,
            selected_credential_id=(
                selected_credential.credential_id if selected_credential else None
            ),
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

    credential_name = request.headers.get("jentic-credential-name")
    injection = await _resolve_credentials(
        ctx_req, ctx, identity, credential_name, preresolved=selected_credential
    )
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
            signing=injection.signing,
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
    *,
    preresolved: ResolvedCredential | None = None,
) -> Response:
    """Sync, non-idempotent streaming passthrough.

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
    injection = await _resolve_credentials(
        ctx_req, ctx, identity, credential_name, preresolved=preresolved
    )
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
        signing=injection.signing,
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
    *,
    selected_credential_id: str | None = None,
    allowed_credential_ids: list[str] | None = None,
) -> Response:
    """Enqueue an async (202) execution. The worker shares the same pipeline.

    On the direct-binding path the payload pins the credential the edge
    selected (and the allowed set derived from the caller's bindings) so the
    worker's injection replays the exact same selection under the same
    injection boundary (Q-02) — rules were already enforced here at the edge.
    """
    execution_id = mint_execution_id()
    body = await _read_request_body(request, ctx_req.method, ctx)

    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "upstream_url": ctx_req.upstream_url,
        "method": ctx_req.method,
        "trace_id": ctx_req.trace_id,
        "operation_id": ctx_req.operation_id,
        "api_vendor": ctx_req.api_vendor,
        "api_name": ctx_req.api_name,
        "api_version": ctx_req.api_version,
        "origin": identity.origin.value,
    }
    if selected_credential_id is not None:
        payload["credential_id"] = selected_credential_id
    if allowed_credential_ids is not None:
        payload["allowed_credential_ids"] = allowed_credential_ids
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
