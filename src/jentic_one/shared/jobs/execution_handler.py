"""Execution job handler — runs async upstream calls through the shared pipeline.

The handler is the async half of "one pipeline, two callers": it does **not**
issue its own ``httpx`` calls. It resolves credentials
(via the injected ``CredentialInjector``), applies them to the outbound
URL/headers exactly like the sync router's ``_apply_injection`` (headers **and**
query **and** cookies — an ``apiKey in: query``/``cookie`` credential is never
dropped), then dispatches through the injected ``UpstreamExecutor`` — the broker
adapter over the **same** composed runner the sync path uses (circuit breaker +
per-host bulkhead + response-size cap + error-origin enrichment), with the
``executions`` row persisted by the pipeline. The handler keeps only the
job-result body + the execution lifecycle event.

Before any credential is resolved the job is **re-authorized** through the
injected ``ExecutionAuthorizer`` — the sync route's policy (actor still active
and holding the execute scope, binding still present and not suspended,
permission rules still allow),
evaluated at run time rather than trusted from enqueue time. A denied job
completes with a ``failed`` result carrying the problem body the sync route
would have returned; no credential is injected and no upstream call is made.

``shared/jobs/`` must not import ``broker/`` (arch boundary): the
``ExecutionAuthorizer``, ``CredentialInjector`` and ``UpstreamExecutor`` are
protocols satisfied by broker-side implementations injected at worker startup.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import structlog

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.events import (
    MAX_EVENT_SUMMARY_FIELD_LEN,
    emit_event,
    valid_trace_id_or_minted,
    valid_trace_id_or_none,
)
from jentic_one.shared.events.repeated_failure import maybe_emit_repeated_failure
from jentic_one.shared.jobs.handlers import JobResultPayload
from jentic_one.shared.jobs.operation_payload import operation_from_job_payload
from jentic_one.shared.jobs.protocols import (
    CredentialInjector,
    ExecutionAuthorizer,
    InjectedAuth,
    QueuedExecutionRequest,
    UpstreamExecRequest,
    UpstreamExecutor,
)
from jentic_one.shared.models import ActorType as ActorTypeEnum
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.actors import origin_or_none
from jentic_one.shared.models.events import EventSeverity, EventTag, EventType
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.url import apply_server_variables
from jentic_one.shared.url_validation import validate_upstream_url

logger = structlog.get_logger(__name__)

# The resolved-identity dataclass needs an ``active`` flag; the worker only
# injects after the job passed the run-time ``ExecutionAuthorizer`` re-check
# (which includes the actor-still-active check), so it is always True here.
_WORKER_IDENTITY_ACTIVE = True


class ExecutionHandler:
    """Handles ``kind=execution`` jobs by running the shared execution pipeline."""

    def __init__(
        self,
        *,
        executor: UpstreamExecutor,
        upstream_timeout_s: float = 30.0,
        credential_injector: CredentialInjector | None = None,
        egress: Any | None = None,
        security_config: SecurityConfig | None = None,
        execution_authorizer: ExecutionAuthorizer | None = None,
    ) -> None:
        if credential_injector is not None and execution_authorizer is None:
            # Fail closed at wiring time: injecting credentials for a queued
            # job without re-checking its authorization would honour an
            # enqueue-time decision the actor/binding/rules may have revoked.
            raise ValueError("credential_injector requires an execution_authorizer")
        self._executor = executor
        self._authorizer = execution_authorizer
        self._timeout = upstream_timeout_s
        self._credential_injector = credential_injector
        self._egress = egress
        self._security_config = security_config or SecurityConfig()

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        """Resolve creds, dispatch through the pipeline, persist the result."""
        if not created_by or not actor_type:
            raise ValueError("created_by and actor_type are required for execution jobs")
        if payload is None:
            payload = {}

        upstream_url = payload.get("upstream_url", "")
        method = payload.get("method", "GET")
        # Minted-if-invalid so the credential audit event, the lifecycle
        # events, and the persisted execution row all share one valid id —
        # never the literal "unknown" (#903).
        trace_id = valid_trace_id_or_minted(str(payload.get("trace_id") or ""))
        execution_id = payload.get("execution_id", f"exec_{job_id}")
        api_vendor = payload.get("api_vendor")
        api_name = payload.get("api_name")
        api_version = payload.get("api_version")
        origin = payload.get("origin")
        # The repeated-failure detector keys on the operation id and renders the
        # human identity; fold the payload's dual-written keys (the dict wins;
        # legacy in-flight jobs carry only the flat id).
        operation = operation_from_job_payload(payload)
        server_variables = _str_map(payload.get("server_variables"))
        server_variable_defaults = _str_map(payload.get("server_variable_defaults"))
        server_variables_unresolved = payload.get("server_variables_unresolved") is True

        body: bytes | None = None
        body_b64 = payload.get("body_b64")
        if body_b64:
            body = base64.b64decode(body_b64)

        upstream_url = validate_upstream_url(upstream_url, self._egress)

        # Run-time re-authorization (before any credential is touched). The
        # enqueue-time selection rides along only as the disambiguation input;
        # the boundary injection uses is the one re-derived *now*.
        allowed: tuple[str, ...] = ()
        pinned_credential_id: str | None = None
        if self._authorizer is not None:
            verdict = await self._authorizer.authorize(
                QueuedExecutionRequest(
                    actor_id=created_by,
                    actor_type=actor_type,
                    method=method,
                    upstream_url=upstream_url,
                    api_vendor=api_vendor or "",
                    api_name=api_name or "",
                    api_version=api_version or "",
                    operation_id=operation.id if operation else None,
                    credential_id=payload.get("credential_id"),
                    server_variables=server_variables,
                    server_variables_unresolved=server_variables_unresolved,
                )
            )
            if not verdict.allowed:
                return await self._deny(
                    session,
                    job_id=job_id,
                    execution_id=execution_id,
                    trace_id=trace_id,
                    problem=verdict.problem or {},
                    created_by=created_by,
                    actor_type=actor_type,
                    payload=payload,
                )
            allowed = verdict.allowed_credential_ids
            pinned_credential_id = verdict.credential_id

        headers: dict[str, str] = {}
        credential_id: str | None = None
        credential_name: str | None = None
        signing = None
        if self._credential_injector is not None and api_vendor:
            # The injection boundary is the one the run-time re-authorization
            # just derived (the caller's currently bound credentials) — never
            # the enqueue-time snapshot in the payload. An empty boundary
            # resolves nothing (fail closed), never the tenant-wide set.
            injection = await self._credential_injector.inject(
                api_vendor=api_vendor,
                api_name=api_name or "",
                api_version=api_version or "",
                identity=_worker_identity(created_by, actor_type),
                credential_id=pinned_credential_id,
                allowed_credential_ids=list(allowed),
                trace_id=trace_id,
                request_server_variables=server_variables,
                server_variables_unresolved=server_variables_unresolved,
            )
            applied = _apply_injection(upstream_url, injection, server_variable_defaults)
            upstream_url, headers = applied.url, applied.headers
            credential_id = injection.credential_id
            credential_name = injection.credential_name
            signing = injection.signing
            if injection.server_variables or server_variable_defaults:
                upstream_url = validate_upstream_url(upstream_url, self._egress)

        status = ExecutionStatus.COMPLETED
        http_status: int | None = None
        error_msg: str | None = None
        response_body: bytes = b""
        content_type: str | None = None
        duration_ms: int = 0

        try:
            exec_result = await self._executor.execute(
                UpstreamExecRequest(
                    method=method,
                    url=upstream_url,
                    headers=headers,
                    body=body,
                    timeout_s=self._timeout,
                    signing=signing,
                    metadata={
                        "execution_id": execution_id,
                        "trace_id": trace_id,
                        "toolkit_id": payload.get("toolkit_id"),
                        # The resolved operation (id + path template + method)
                        # as one dict — the already-folded value, so a malformed
                        # payload dict is validated (and warned about) once;
                        # ``operation_id`` rides alongside for legacy in-flight
                        # jobs that carry only the flat id.
                        "operation": operation.model_dump() if operation else None,
                        "operation_id": payload.get("operation_id"),
                        "api_vendor": api_vendor,
                        "api_name": api_name,
                        "api_version": api_version,
                        "pinned_revisions": payload.get("pinned_revisions"),
                        "actor_id": created_by,
                        "actor_type": actor_type,
                        "origin": origin,
                        # Credential attribution (#740): carried so the
                        # pipeline persists the same credential_id/name on
                        # the execution record as the sync router would.
                        "credential_id": credential_id,
                        "credential_name": credential_name,
                    },
                ),
                session=session,
            )
            http_status = exec_result.status_code
            response_body = exec_result.body
            content_type = exec_result.content_type
            duration_ms = exec_result.duration_ms
            if http_status >= 400:
                status = ExecutionStatus.FAILED
                error_msg = f"Upstream returned {http_status}"
        except (OSError, TimeoutError) as exc:
            status = ExecutionStatus.FAILED
            error_msg = str(exc)[:MAX_EVENT_SUMMARY_FIELD_LEN]
        except Exception as exc:
            # BrokerError (circuit open, bulkhead full, transport) crosses the arch
            # boundary via the UpstreamExecutor protocol — we can't import it here.
            # Re-raise programming errors; treat domain errors as pipeline failures.
            if isinstance(exc, (TypeError, AttributeError, KeyError, IndexError)):
                raise
            logger.warning(
                "pipeline_error",
                job_id=job_id,
                error_type=type(exc).__name__,
                error=str(exc)[:MAX_EVENT_SUMMARY_FIELD_LEN],
            )
            status = ExecutionStatus.FAILED
            error_msg = str(exc)[:MAX_EVENT_SUMMARY_FIELD_LEN]

        await self._emit_lifecycle(
            session,
            job_id=job_id,
            execution_id=execution_id,
            trace_id=trace_id,
            status=status,
            error_msg=error_msg,
            created_by=created_by,
            actor_type=actor_type,
            toolkit_id=payload.get("toolkit_id"),
            credential_id=credential_id,
            operation=operation,
            origin=origin,
        )

        result_body: dict[str, Any] = {
            "execution_id": execution_id,
            "status": status,
            "http_status": http_status,
            "duration_ms": duration_ms,
        }
        if response_body:
            result_body["body_b64"] = base64.b64encode(response_body).decode()

        return JobResultPayload(body=result_body, content_type=content_type)

    async def _deny(
        self,
        session: Any,
        *,
        job_id: str,
        execution_id: str,
        trace_id: str,
        problem: dict[str, Any],
        created_by: str,
        actor_type: str,
        payload: dict[str, Any],
    ) -> JobResultPayload:
        """Record a run-time authorization denial like any other failed execution.

        Same outcome shape as an upstream/pipeline failure — ``EXECUTION_FAILED``
        lifecycle event (+ the repeated-failure check) and a ``failed`` job
        result — plus the ``problem`` body the sync route would have returned,
        so a polling caller gets the same ``type``/``status``/directive.
        """
        problem_type = str(problem.get("type") or "about:blank")
        logger.info("queued_execution_denied", job_id=job_id, problem_type=problem_type)
        await self._emit_lifecycle(
            session,
            job_id=job_id,
            execution_id=execution_id,
            trace_id=trace_id,
            status=ExecutionStatus.FAILED,
            error_msg=f"Authorization denied ({problem_type})",
            created_by=created_by,
            actor_type=actor_type,
            toolkit_id=payload.get("toolkit_id"),
            credential_id=payload.get("credential_id"),
            operation=operation_from_job_payload(payload),
            origin=payload.get("origin"),
        )
        status = problem.get("status")
        return JobResultPayload(
            body={
                "execution_id": execution_id,
                "status": ExecutionStatus.FAILED,
                "http_status": status if isinstance(status, int) else None,
                "duration_ms": 0,
                "problem": problem,
            }
        )

    async def _emit_lifecycle(
        self,
        session: Any,
        *,
        job_id: str,
        execution_id: str,
        trace_id: str,
        status: ExecutionStatus,
        error_msg: str | None,
        created_by: str,
        actor_type: str,
        toolkit_id: str | None = None,
        credential_id: str | None = None,
        operation: OperationInfo | None = None,
        origin: str | None = None,
    ) -> None:
        # The enqueue path persisted the request-derived Origin string in the
        # job payload; it rides the lifecycle events as a closed-enum tag so
        # the async path splits telemetry by surface like the sync path does.
        origin_tag = origin_or_none(origin)
        origin_tags: set[EventTag] | None = {origin_tag} if origin_tag is not None else None
        event_trace_id = valid_trace_id_or_none(trace_id)
        try:
            if status == ExecutionStatus.COMPLETED:
                await emit_event(
                    session,
                    type=EventType.EXECUTION_COMPLETED,
                    severity=EventSeverity.INFO,
                    summary=f"Execution completed (job {job_id})",
                    execution_id=execution_id,
                    trace_id=event_trace_id,
                    job_id=job_id,
                    created_by=created_by,
                    actor_id=created_by,
                    actor_type=actor_type,
                    tags=origin_tags,
                )
            else:
                sanitized = (error_msg or "unknown")[:MAX_EVENT_SUMMARY_FIELD_LEN]
                await emit_event(
                    session,
                    type=EventType.EXECUTION_FAILED,
                    severity=EventSeverity.ERROR,
                    summary=f"Execution failed: {sanitized}",
                    requires_action=True,
                    execution_id=execution_id,
                    trace_id=event_trace_id,
                    job_id=job_id,
                    created_by=created_by,
                    actor_id=created_by,
                    actor_type=actor_type,
                    tags=origin_tags,
                )
        except Exception:
            logger.warning("emit_event_failed", job_id=job_id, execution_id=execution_id)

        if status == ExecutionStatus.FAILED:
            await maybe_emit_repeated_failure(
                session,
                actor_id=created_by,
                actor_type=actor_type,
                toolkit_id=toolkit_id,
                credential_id=credential_id,
                operation=operation,
                trace_id=event_trace_id,
                config=self._security_config,
            )


def _worker_identity(created_by: str, actor_type: str) -> Identity:
    """A minimal resolved identity for credential resolution by the worker.

    The inbound token was validated at enqueue time; the worker resolves the
    credential by the enqueuing actor's identity, mirroring the sync path's
    ``identity`` argument to ``CredentialInjector.inject``.

    ``permissions`` is intentionally empty: the credential injector only uses
    ``sub`` + ``actor_type`` to resolve the credential row — RBAC permissions
    were already enforced at enqueue time (execute:write gate on the API route).
    """
    return Identity(
        sub=created_by,
        actor_type=ActorTypeEnum(actor_type),
        permissions=[],
        expires_at=None,
        active=_WORKER_IDENTITY_ACTIVE,
    )


@dataclass(frozen=True, slots=True)
class AppliedAuth:
    """Result of applying injected credentials to an outbound request."""

    url: str
    headers: dict[str, str]


def _str_map(value: Any) -> dict[str, str] | None:
    """A payload ``{str: str}`` mapping, or ``None`` when absent/malformed."""
    if not isinstance(value, dict):
        return None
    return {str(k): str(v) for k, v in value.items()} or None


def _apply_injection(
    upstream_url: str,
    injection: InjectedAuth,
    server_variable_defaults: dict[str, str] | None = None,
) -> AppliedAuth:
    """Apply injected auth to the outbound URL + headers (worker side).

    Mirrors the sync router's ``_apply_injection``: server-variable credentials
    are substituted into the URL template, query-param credentials are merged
    into the URL query, and cookie credentials into a ``Cookie`` header, so an
    ``apiKey in: query`` / ``apiKey in: cookie`` credential is applied rather
    than silently dropped.
    """
    if injection.server_variables or server_variable_defaults:
        upstream_url = apply_server_variables(
            upstream_url, injection.server_variables, server_variable_defaults
        )

    if injection.query_params:
        parsed = urlparse(upstream_url)
        sep = "&" if parsed.query else ""
        new_query = parsed.query + sep + urlencode(injection.query_params)
        upstream_url = urlunparse(parsed._replace(query=new_query))

    headers = dict(injection.headers)
    if injection.cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in injection.cookies.items())
    return AppliedAuth(url=upstream_url, headers=headers)
