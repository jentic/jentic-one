"""Execution job handler — runs async upstream calls through the shared pipeline.

The handler is the async half of "one pipeline, two callers": it does **not**
issue its own ``httpx`` calls. It resolves credentials
(via the injected ``CredentialInjector``), applies them to the outbound
URL/headers through the same ``shared.http.injection.apply_injection`` the sync
router uses (headers **and** query **and** cookies — an ``apiKey in: query``/
``cookie`` credential is never dropped, and the caller's own cookies captured at
enqueue are kept), then dispatches through the injected ``UpstreamExecutor`` — the broker
adapter over the **same** composed runner the sync path uses (circuit breaker +
per-host bulkhead + response-size cap + error-origin enrichment), with the
``executions`` row persisted by the pipeline. The handler keeps only the
job-result body + the execution lifecycle event.

Before any credential is resolved the job is **re-authorized** through the
injected ``ExecutionAuthorizer`` — the sync route's policy (actor still active
and holding the execute scope, binding still present and not suspended,
permission rules still allow),
evaluated at run time rather than trusted from enqueue time. The identity
credentials are then resolved for is built from one fresh read of the actor's
rows through the injected ``ActorStatusReader`` (liveness and the execute
permission together), taken after those authorization reads and immediately
before injection and dispatch: an actor that is no longer active (or no longer
exists), or that has lost the execute permission, is denied there too. A
denied job completes with a ``failed`` result carrying the problem body the
sync route would have returned; no credential is injected and no upstream
call is made.

``shared/jobs/`` must not import ``broker/`` (arch boundary): the
``ExecutionAuthorizer``, ``ActorStatusReader``, ``CredentialInjector`` and
``UpstreamExecutor`` are protocols satisfied by broker-side implementations
injected at worker startup.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.crypto.encryption import EncryptionService
from jentic_one.shared.db.connectivity import involves_database_error, redact_sql
from jentic_one.shared.db.protocols import TransactionalDatabase
from jentic_one.shared.events import (
    MAX_EVENT_SUMMARY_FIELD_LEN,
    emit_event,
    valid_trace_id_or_minted,
    valid_trace_id_or_none,
)
from jentic_one.shared.events.repeated_failure import maybe_emit_repeated_failure
from jentic_one.shared.http.injection import apply_injection
from jentic_one.shared.jobs.handlers import JobResultPayload
from jentic_one.shared.jobs.hold import ENCRYPTED_PAYLOAD_KEY, INBOUND_COOKIE_PAYLOAD_KEY
from jentic_one.shared.jobs.operation_payload import operation_from_job_payload
from jentic_one.shared.jobs.protocols import (
    ActorStatusReader,
    CredentialInjector,
    ExecutionAuthorizer,
    ExecutionRecordNotPersistedError,
    InjectedAuth,
    QueuedExecutionRequest,
    QueuedExecutionVerdict,
    UpstreamExecRequest,
    UpstreamExecutor,
)
from jentic_one.shared.jobs.queued_denials import (
    inactive_actor_problem,
    insufficient_scope_problem,
    queued_execution_instance,
)
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ActorType as ActorTypeEnum
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.actors import origin_or_none
from jentic_one.shared.models.events import (
    EventSeverity,
    EventTag,
    EventType,
    thirdparty_auth_error_tag,
)
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.url_validation import validate_upstream_url_async

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter

logger = structlog.get_logger(__name__)

#: Suffix that marks a job result whose ``executions`` record was not written.
_RECORD_NOT_PERSISTED = "execution record not persisted"

# Created once on first increment, so the instrument identity is stable.
_write_failures: Counter | None = None


def _record_write_failure(write: str) -> None:
    """Count a post-dispatch failure that left a queued execution's rows unwritten.

    ``write`` is ``record`` (the ``executions`` record was not written),
    ``lifecycle`` (the lifecycle events were not written), or ``runner`` (the
    dispatched call itself crashed, so no record write was attempted).
    """
    global _write_failures
    if _write_failures is None:
        _write_failures = get_meter("jentic_one.jobs").create_counter(
            "jentic_one.jobs.execution_write_failed",
            description=(
                "Queued-execution post-dispatch failures that left the executions "
                "record (write=record) or lifecycle events (write=lifecycle) "
                "unwritten, or a dispatched call that crashed before any record "
                "write (write=runner); the job still completed with its real result."
            ),
        )
    _write_failures.add(1, {"write": write})


def _traceback_if_safe(exc: BaseException) -> BaseException | bool:
    """``exc_info`` for a swallowed post-dispatch error: its traceback, unless unsafe.

    A bug swallowed here is otherwise untraceable, so it is logged with its
    traceback. A database error anywhere in the chain is not: its message can
    carry the SQL statement and bound parameters.
    """
    return False if involves_database_error(exc) else exc


#: Result problem of a held payload this worker has no keyset to decrypt.
_HELD_PAYLOAD_UNDECRYPTABLE_PROBLEM: dict[str, Any] = {
    "type": "approval_hold_unavailable",
    "title": "Held execution could not run",
    "status": 503,
    "detail": (
        "The worker has no credentials encryption keyset to read this held execution; "
        "it was not run"
    ),
    "error_origin": "broker",
}

#: What a run with no credential injector applies: nothing but the caller's cookies.
_NO_INJECTION = InjectedAuth(headers={}, query_params={}, cookies={})

#: A pre-injection refusal: the log ``reason`` and the problem-body builder.
_PreInjectionDenial = tuple[str, Callable[[str], dict[str, object]]]


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
        encryption: EncryptionService | None = None,
        actor_status: ActorStatusReader | None = None,
    ) -> None:
        if credential_injector is not None and execution_authorizer is None:
            # Fail closed at wiring time: injecting credentials for a queued
            # job without re-checking its authorization would honour an
            # enqueue-time decision the actor/binding/rules may have revoked.
            raise ValueError("credential_injector requires an execution_authorizer")
        if credential_injector is not None and actor_status is None:
            # Same reason: without a live actor read, the identity used for
            # injection could not reflect a disable that lands after enqueue.
            raise ValueError("credential_injector requires an actor_status reader")
        if actor_status is not None and execution_authorizer is None:
            # The live actor read runs only after the run-time authorizer
            # allows the job; a reader on its own would never be consulted.
            raise ValueError("actor_status requires an execution_authorizer")
        self._executor = executor
        self._authorizer = execution_authorizer
        self._actor_status = actor_status
        self._timeout = upstream_timeout_s
        self._credential_injector = credential_injector
        self._egress = egress
        self._security_config = security_config or SecurityConfig()
        self._encryption = encryption

    def _inbound_cookie(self, payload: dict[str, Any], *, job_id: str) -> str | None:
        """The caller's ``Cookie`` the broker captured at enqueue, decrypted.

        A payload without one yields ``None``. One this worker has no keyset to
        decrypt is dropped and logged: the run goes ahead without the
        caller's cookies rather than failing a call the broker accepted.
        """
        blob = payload.get(INBOUND_COOKIE_PAYLOAD_KEY)
        if not isinstance(blob, str) or not blob:
            return None
        if self._encryption is None:
            logger.warning("queued_execution_cookie_undecryptable", job_id=job_id)
            return None
        return self._encryption.decrypt(blob)

    async def execute(
        self,
        job_id: str,
        db: TransactionalDatabase,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        """Resolve creds, dispatch through the pipeline, persist the result.

        ``db`` is the worker's database. Each write opens its own short
        transaction on it, after the upstream call, so the call is never awaited
        while an admin connection (or, on SQLite, the write lock) is held.

        Once the upstream call is dispatched nothing raises: a raise would
        requeue the job and send the upstream request again. The executor
        reports a lost record with the real upstream result, the lifecycle
        write is best-effort, and either failure is logged and counted.
        Failures before dispatch (credential resolution and its audit write)
        propagate, so the job is retried with nothing sent.
        """
        if not created_by or not actor_type:
            raise ValueError("created_by and actor_type are required for execution jobs")
        if payload is None:
            payload = {}

        # Held-job payloads are encrypted at rest with the platform key; decrypt
        # before reading any field.
        if ENCRYPTED_PAYLOAD_KEY in payload:
            if self._encryption is None:
                # Fail closed with a result the agent can read, never a run.
                logger.error("held_payload_undecryptable", job_id=job_id)
                return JobResultPayload(
                    body={
                        "execution_id": f"exec_{job_id}",
                        "status": ExecutionStatus.FAILED,
                        "http_status": 503,
                        "duration_ms": 0,
                        "problem": _HELD_PAYLOAD_UNDECRYPTABLE_PROBLEM,
                    }
                )
            payload = json.loads(self._encryption.decrypt(str(payload[ENCRYPTED_PAYLOAD_KEY])))

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
        # human identity; fold the payload's ``operation`` dict once.
        operation = operation_from_job_payload(payload)
        server_variables = _str_map(payload.get("server_variables"))
        server_variable_defaults = _str_map(payload.get("server_variable_defaults"))
        server_variables_unresolved = payload.get("server_variables_unresolved") is True

        body: bytes | None = None
        body_b64 = payload.get("body_b64")
        if body_b64:
            body = base64.b64decode(body_b64)

        upstream_url = await validate_upstream_url_async(upstream_url, self._egress)

        # Run-time re-authorization (before any credential is touched). The
        # enqueue-time selection rides along only as the disambiguation input;
        # the boundary injection uses is the one re-derived *now*.
        allowed: tuple[str, ...] = ()
        pinned_credential_id: str | None = None
        identity: Identity | None = None
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
                    relative_path=operation.relative_path if operation else None,
                    operation_path=operation.path if operation else None,
                    trace_id=trace_id,
                    credential_id=payload.get("credential_id"),
                    server_variables=server_variables,
                    server_variables_unresolved=server_variables_unresolved,
                    job_id=job_id,
                )
            )
            # One live read of the actor's rows (liveness + the execute
            # permission), after the authorization reads and right before any
            # credential is decrypted or the upstream is called, so a disable
            # or revocation that commits while the job is being re-authorized
            # still stops it. A refusal comes back as a denied verdict and is
            # recorded below like any other run-time denial.
            if verdict.allowed and self._actor_status is not None:
                identity, verdict = await self._recheck_actor(
                    self._actor_status,
                    verdict,
                    job_id=job_id,
                    execution_id=execution_id,
                    created_by=created_by,
                    actor_type=actor_type,
                    upstream_url=upstream_url,
                )
            if not verdict.allowed:
                return await self._deny(
                    db,
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

        # The request headers the broker kept for the run (Content-Type and
        # friends) and the caller's own cookies; injected credentials are
        # applied over them.
        headers: dict[str, str] = _str_map(payload.get("headers")) or {}
        inbound_cookie = self._inbound_cookie(payload, job_id=job_id)
        injection = _NO_INJECTION
        injected_defaults: dict[str, str] | None = None
        if self._credential_injector is not None and api_vendor:
            if identity is None:
                # Unreachable while the constructor's wiring invariant holds
                # (an injector implies an authorizer and an actor_status
                # reader); refuse loudly rather than send the call unchecked.
                raise RuntimeError("credential injection reached without a live actor identity")
            # The injection boundary is the one the run-time re-authorization
            # just derived (the caller's currently bound credentials) — never
            # the enqueue-time snapshot in the payload. An empty boundary
            # resolves nothing (fail closed), never the tenant-wide set.
            injection = await self._credential_injector.inject(
                api_vendor=api_vendor,
                api_name=api_name or "",
                api_version=api_version or "",
                identity=identity,
                credential_id=pinned_credential_id,
                allowed_credential_ids=list(allowed),
                trace_id=trace_id,
                request_server_variables=server_variables,
                server_variables_unresolved=server_variables_unresolved,
            )
            injected_defaults = server_variable_defaults
        upstream_url, headers = apply_injection(
            headers,
            upstream_url,
            injection,
            inbound_cookie=inbound_cookie,
            server_variable_defaults=injected_defaults,
        )
        credential_id = injection.credential_id
        credential_name = injection.credential_name
        signing = injection.signing
        if injection.server_variables or injected_defaults:
            upstream_url = await validate_upstream_url_async(upstream_url, self._egress)

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
                        # payload dict is validated (and warned about) once.
                        "operation": operation.model_dump() if operation else None,
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
                db=db,
            )
            http_status = exec_result.status_code
            response_body = exec_result.body
            content_type = exec_result.content_type
            duration_ms = exec_result.duration_ms
            if http_status >= 400:
                status = ExecutionStatus.FAILED
                error_msg = f"Upstream returned {http_status}"
        except ExecutionRecordNotPersistedError as exc:
            cause = exc.__cause__
            _record_write_failure("runner" if exc.runner_failed else "record")
            logger.error(
                "queued_execution_record_not_persisted",
                job_id=job_id,
                execution_id=execution_id,
                error_type=type(cause).__name__ if cause is not None else None,
                http_status=exc.result.status_code if exc.result is not None else None,
                exc_info=_traceback_if_safe(exc),
            )
            if exc.result is None:
                status = ExecutionStatus.FAILED
                error_msg = (
                    f"{exc.upstream_error} ({_RECORD_NOT_PERSISTED})"
                    if exc.upstream_error
                    else _RECORD_NOT_PERSISTED.capitalize()
                )[:MAX_EVENT_SUMMARY_FIELD_LEN]
            else:
                http_status = exc.result.status_code
                response_body = exc.result.body
                content_type = exc.result.content_type
                duration_ms = exc.result.duration_ms
                if http_status >= 400:
                    status = ExecutionStatus.FAILED
                    error_msg = f"Upstream returned {http_status} ({_RECORD_NOT_PERSISTED})"
        except (OSError, TimeoutError) as exc:
            status = ExecutionStatus.FAILED
            error_msg = str(exc)[:MAX_EVENT_SUMMARY_FIELD_LEN]
        except Exception as exc:
            # BrokerError (circuit open, bulkhead full, transport) crosses the arch
            # boundary via the UpstreamExecutor protocol — we can't import it here.
            # Re-raise programming errors; treat domain errors as pipeline failures.
            if isinstance(exc, (TypeError, AttributeError, KeyError, IndexError)):
                raise
            # redact_sql: a DB error's str() carries the statement and bound
            # parameters, which must not reach the persisted event summary.
            safe_error = redact_sql(str(exc)) or type(exc).__name__
            logger.warning(
                "pipeline_error",
                job_id=job_id,
                error_type=type(exc).__name__,
                error=safe_error[:MAX_EVENT_SUMMARY_FIELD_LEN],
            )
            status = ExecutionStatus.FAILED
            error_msg = safe_error[:MAX_EVENT_SUMMARY_FIELD_LEN]

        thirdparty_tag = thirdparty_auth_error_tag(http_status, api_vendor)
        error_tags: set[EventTag] | None = {thirdparty_tag} if thirdparty_tag is not None else None

        await self._write_lifecycle(
            db,
            job_id=job_id,
            execution_id=execution_id,
            trace_id=trace_id,
            status=status,
            error_msg=error_msg,
            created_by=created_by,
            actor_type=actor_type,
            credential_id=credential_id,
            operation=operation,
            error_tags=error_tags,
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

    async def _recheck_actor(
        self,
        actor_status: ActorStatusReader,
        verdict: QueuedExecutionVerdict,
        *,
        job_id: str,
        execution_id: str,
        created_by: str,
        actor_type: str,
        upstream_url: str,
    ) -> tuple[Identity | None, QueuedExecutionVerdict]:
        """Re-read the actor right before injection: the identity to inject for, or a denial.

        Called only once the authorizer has allowed the job. Returns
        ``(identity, verdict)`` with the verdict unchanged when the actor is
        still active and still holds the execute permission, else
        ``(None, denied verdict)`` carrying the sync edge's problem body, so
        the caller records it like any other run-time denial. An actor type
        that is no longer issued (a retired ``toolkit`` / ``service_account``
        job) is refused before any read.

        The worker holds no token for the actor, so it resolves the credential
        by the actor's id and type, mirroring the sync path's ``identity``
        argument to ``CredentialInjector.inject``. ``permissions`` is
        intentionally empty: the injector only uses ``sub`` + ``actor_type``.
        """
        denial = await self._actor_denial(actor_status, created_by, actor_type)
        if denial is None:
            identity = Identity(
                sub=created_by,
                actor_type=ActorTypeEnum(actor_type),
                permissions=[],
                expires_at=None,
                active=True,
            )
            return identity, verdict
        reason, problem_for = denial
        logger.info(
            "queued_execution_denied",
            reason=reason,
            stage="pre_injection",
            job_id=job_id,
            execution_id=execution_id,
            actor_id=created_by,
            actor_type=actor_type,
        )
        return None, QueuedExecutionVerdict(
            allowed=False, problem=problem_for(queued_execution_instance(upstream_url))
        )

    @staticmethod
    async def _actor_denial(
        actor_status: ActorStatusReader, created_by: str, actor_type: str
    ) -> _PreInjectionDenial | None:
        """Why the live read refuses the actor, or ``None`` when it may still execute."""
        try:
            ActorTypeEnum(actor_type)
        except ValueError:
            return "actor_type_retired", inactive_actor_problem
        authority = await actor_status.authority(
            actor_id=created_by, actor_type=actor_type, permission=BROKER_EXECUTE_PERMISSION
        )
        if not authority.active:
            return "actor_inactive", inactive_actor_problem
        if not authority.holds_permission:
            return "insufficient_scope", insufficient_scope_problem
        return None

    async def _deny(
        self,
        db: TransactionalDatabase,
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
        await self._write_lifecycle(
            db,
            job_id=job_id,
            execution_id=execution_id,
            trace_id=trace_id,
            status=ExecutionStatus.FAILED,
            error_msg=f"Authorization denied ({problem_type})",
            created_by=created_by,
            actor_type=actor_type,
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

    async def _write_lifecycle(
        self,
        db: TransactionalDatabase,
        *,
        job_id: str,
        execution_id: str,
        trace_id: str,
        status: ExecutionStatus,
        error_msg: str | None,
        created_by: str,
        actor_type: str,
        credential_id: str | None,
        operation: OperationInfo | None,
        error_tags: set[EventTag] | None = None,
        origin: str | None,
    ) -> None:
        """Write the lifecycle events in their own transaction, best-effort.

        They are written after the job's outcome is settled (the upstream call
        was dispatched, or the job was denied). Whatever the write fails with
        (a mapped database error, a pool checkout timeout, a lost connection,
        a driver error, or a bug), the failure is logged and counted, and the
        job still completes with its real result: a raise would requeue the
        job and re-send the upstream request.
        """
        try:
            async with db.transaction() as session:
                await self._emit_lifecycle(
                    session,
                    job_id=job_id,
                    execution_id=execution_id,
                    trace_id=trace_id,
                    status=status,
                    error_msg=error_msg,
                    created_by=created_by,
                    actor_type=actor_type,
                    credential_id=credential_id,
                    operation=operation,
                    error_tags=error_tags,
                    origin=origin,
                )
        except Exception as exc:
            _record_write_failure("lifecycle")
            logger.warning(
                "queued_execution_lifecycle_write_failed",
                job_id=job_id,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                exc_info=_traceback_if_safe(exc),
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
        credential_id: str | None = None,
        operation: OperationInfo | None = None,
        error_tags: set[EventTag] | None = None,
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
                failed_tags: set[EventTag] = set(error_tags or ())
                if origin_tag is not None:
                    failed_tags.add(origin_tag)
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
                    tags=failed_tags or None,
                )
        except Exception:
            logger.warning("emit_event_failed", job_id=job_id, execution_id=execution_id)

        if status == ExecutionStatus.FAILED:
            await maybe_emit_repeated_failure(
                session,
                actor_id=created_by,
                actor_type=actor_type,
                credential_id=credential_id,
                operation=operation,
                trace_id=event_trace_id,
                config=self._security_config,
            )


def _str_map(value: Any) -> dict[str, str] | None:
    """A payload ``{str: str}`` mapping, or ``None`` when absent/malformed."""
    if not isinstance(value, dict):
        return None
    return {str(k): str(v) for k, v in value.items()} or None
