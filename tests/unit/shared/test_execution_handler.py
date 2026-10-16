"""Unit tests for the execution job handler (RN-0 unified async path).

These assert the handler delegates to the injected ``UpstreamExecutor`` (the
broker pipeline adapter) instead of issuing its own HTTP, and applies injected
credentials to the outbound URL/headers — headers **and** query **and** cookies
— mirroring the sync router's ``_apply_injection``.

The lifecycle event write goes through ``emit_event`` → ``EventRepository`` and
needs a real DB; the handler swallows its failure (best-effort), so a fake
session that has no real backing is fine for these pure-logic unit tests.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from sqlalchemy.exc import OperationalError

from jentic_one.shared.db.errors import DatabaseIntegrityError, DatabaseUnavailableError
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.protocols import (
    ExecutionRecordNotPersistedError,
    InjectedAuth,
    QueuedExecutionRequest,
    QueuedExecutionVerdict,
    UpstreamExecRequest,
    UpstreamExecResult,
)
from jentic_one.shared.models.actors import Origin
from jentic_one.shared.models.events import EventType
from jentic_one.shared.schemas import OperationInfo
from tests.unit.fake_tx import FailingTransactions, SessionScope, post_upstream_write_failures


class _FakeSession:
    """A session whose every attribute access is a no-op coroutine target.

    The handler only touches the session via ``emit_event`` (best-effort,
    wrapped in try/except), so it never needs to behave like a real session.
    """


class _RecordingExecutor:
    def __init__(self, result: UpstreamExecResult) -> None:
        self._result = result
        self.last_request: UpstreamExecRequest | None = None

    async def execute(self, request: UpstreamExecRequest, *, db: Any) -> UpstreamExecResult:
        self.last_request = request
        return self._result


class _RaisingExecutor:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.called = False

    async def execute(self, request: UpstreamExecRequest, *, db: Any) -> UpstreamExecResult:
        self.called = True
        raise self._exc


class _FakeInjector:
    def __init__(self, injection: InjectedAuth) -> None:
        self._injection = injection
        self.last_trace_id: str | None = None
        self.last_identity: Any = None
        self.last_allowed_credential_ids: Any = "unset"
        self.last_credential_id: str | None = None
        self.last_request_server_variables: Any = "unset"
        self.last_server_variables_unresolved: bool | None = None

    async def inject(
        self,
        *,
        api_vendor: str,
        api_name: str,
        api_version: str,
        identity: Any,
        credential_name: str | None = None,
        credential_id: str | None = None,
        allowed_credential_ids: Any = None,
        trace_id: str | None = None,
        request_server_variables: Any = None,
        server_variables_unresolved: bool = False,
    ) -> InjectedAuth:
        self.last_trace_id = trace_id
        self.last_identity = identity
        self.last_allowed_credential_ids = allowed_credential_ids
        self.last_credential_id = credential_id
        self.last_request_server_variables = request_server_variables
        self.last_server_variables_unresolved = server_variables_unresolved
        return self._injection


class _FakeAuthorizer:
    """Stands in for the broker's run-time re-authorizer (``ExecutionAuthorizer``)."""

    def __init__(self, verdict: QueuedExecutionVerdict | None = None) -> None:
        self._verdict = verdict or QueuedExecutionVerdict(allowed=True)
        self.last_request: QueuedExecutionRequest | None = None

    async def authorize(self, request: QueuedExecutionRequest) -> QueuedExecutionVerdict:
        self.last_request = request
        return self._verdict


class _FakeActorStatus:
    """Stands in for the broker's live actor-status read (``ActorStatusReader``)."""

    def __init__(self, *, active: bool = True) -> None:
        self._active = active
        self.calls: list[tuple[str, str]] = []

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        self.calls.append((actor_id, actor_type))
        return self._active


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "execution_id": "exec_123",
        "upstream_url": "https://api.example.com/v1/things",
        "method": "GET",
        "trace_id": "ab" * 16,
        "api_vendor": "example",
        "api_name": "api",
        "api_version": "1.0.0",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_handler_delegates_to_executor_no_raw_http() -> None:
    """The handler dispatches through the injected executor, not raw httpx."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"ok", content_type="text/plain", duration_ms=12)
    )
    handler = ExecutionHandler(executor=executor)

    result = await handler.execute(
        "job1",
        SessionScope(_FakeSession()),
        payload=_payload(),
        created_by="usr_test",
        actor_type="user",
    )

    assert executor.last_request is not None
    assert executor.last_request.url == "https://api.example.com/v1/things"
    assert executor.last_request.method == "GET"
    assert result.body["status"] == "completed"
    assert result.body["http_status"] == 200
    assert result.body["body_b64"]  # base64 of b"ok"
    assert result.content_type == "text/plain"


@pytest.mark.asyncio
async def test_handler_marks_4xx_5xx_failed() -> None:
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=503, body=b"", content_type=None, duration_ms=5)
    )
    handler = ExecutionHandler(executor=executor)

    result = await handler.execute(
        "job2",
        SessionScope(_FakeSession()),
        payload=_payload(),
        created_by="usr_test",
        actor_type="user",
    )

    assert result.body["status"] == "failed"
    assert result.body["http_status"] == 503


@pytest.mark.asyncio
async def test_handler_pipeline_error_is_recorded_not_raised() -> None:
    """A pipeline BrokerError (timeout/circuit-open) → failed result, no re-raise.

    Re-raising would push the job into the worker's retry/DLQ path; transient-vs-
    terminal retry classification is E4.1 (RetryRunner), deferred — so the
    handler keeps the pre-RN-0 behaviour of recording a failed result.
    """
    executor = _RaisingExecutor(RuntimeError("upstream circuit open"))
    handler = ExecutionHandler(executor=executor)

    result = await handler.execute(
        "job3",
        SessionScope(_FakeSession()),
        payload=_payload(),
        created_by="usr_test",
        actor_type="user",
    )

    assert executor.called is True
    assert result.body["status"] == "failed"
    assert result.body["http_status"] is None


@pytest.mark.asyncio
async def test_handler_pipeline_db_error_redacts_sql_from_log_and_event_summary() -> None:
    """A DB error from the pipeline never puts its SQL text in logs or the event summary."""
    exc = OperationalError(
        "SELECT secret FROM credentials WHERE id = $1", ("cred-123",), Exception("lock timeout")
    )
    handler = ExecutionHandler(executor=_RaisingExecutor(exc))

    with (
        structlog.testing.capture_logs() as logs,
        patch(
            "jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock
        ) as mock_emit,
    ):
        result = await handler.execute(
            "job-sql",
            SessionScope(_FakeSession()),
            payload=_payload(),
            created_by="usr_test",
            actor_type="user",
        )

    assert result.body["status"] == "failed"
    pipeline = [e for e in logs if e["event"] == "pipeline_error"]
    assert len(pipeline) == 1
    assert "lock timeout" in pipeline[0]["error"]
    summary = mock_emit.call_args.kwargs["summary"]
    assert "lock timeout" in summary
    for rendered in (str(logs), summary):
        assert "SELECT secret" not in rendered
        assert "cred-123" not in rendered


@pytest.mark.asyncio
async def test_handler_applies_header_query_and_cookie_credentials() -> None:
    """Injected auth is applied to headers, URL query, AND cookies (not dropped)."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(
        InjectedAuth(
            headers={"Authorization": "Bearer tok"},
            query_params={"api_key": "qsecret"},  # pragma: allowlist secret
            cookies={"session": "csecret"},
        )
    )
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,  # pragma: allowlist secret
        execution_authorizer=_FakeAuthorizer(),
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job4",
        SessionScope(_FakeSession()),
        payload=_payload(),
        created_by="usr_test",
        actor_type="user",
    )

    req = executor.last_request
    assert req is not None
    assert req.headers["Authorization"] == "Bearer tok"
    assert "api_key=qsecret" in req.url
    assert req.headers["Cookie"] == "session=csecret"


@pytest.mark.asyncio
async def test_handler_no_injector_sends_no_auth() -> None:
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    handler = ExecutionHandler(executor=executor)

    await handler.execute(
        "job5",
        SessionScope(_FakeSession()),
        payload=_payload(),
        created_by="usr_test",
        actor_type="user",
    )

    req = executor.last_request
    assert req is not None
    assert req.headers == {}
    assert "?" not in req.url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "expected_allowed", "expected_id"),
    [
        # The re-derived boundary and pinned credential are what injection uses.
        (
            QueuedExecutionVerdict(
                allowed=True, allowed_credential_ids=("cred_b",), credential_id="cred_b"
            ),
            ["cred_b"],
            "cred_b",
        ),
        # An empty re-derived boundary stays deny-all.
        (QueuedExecutionVerdict(allowed=True), [], None),
    ],
)
async def test_handler_bounds_injection_to_the_run_time_boundary(
    verdict: QueuedExecutionVerdict, expected_allowed: list[str], expected_id: str | None
) -> None:
    """Injection uses the boundary re-derived at run time — never the enqueue-time
    snapshot in the payload (which here names a credential no longer bound)."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={}))
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_FakeAuthorizer(verdict),
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job_bound",
        SessionScope(_FakeSession()),
        payload=_payload(allowed_credential_ids=["cred_stale"], credential_id="cred_stale"),
        created_by="usr_test",
        actor_type="user",
    )

    assert injector.last_allowed_credential_ids == expected_allowed
    assert injector.last_credential_id == expected_id


@pytest.mark.asyncio
async def test_handler_passes_the_enqueue_selection_to_the_authorizer() -> None:
    """The re-check sees the actor, operation and enqueue-time selection."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    authorizer = _FakeAuthorizer()
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=_FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={})),
        execution_authorizer=authorizer,
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job_req",
        SessionScope(_FakeSession()),
        payload=_payload(
            operation={"id": "listThings", "path": "/v1/things", "method": "GET"},
            credential_id="cred_a",
            toolkit_id=None,
        ),
        created_by="agt_abc123",
        actor_type="agent",
    )

    assert authorizer.last_request == QueuedExecutionRequest(
        actor_id="agt_abc123",
        actor_type="agent",
        method="GET",
        upstream_url="https://api.example.com/v1/things",
        api_vendor="example",
        api_name="api",
        api_version="1.0.0",
        operation_id="listThings",
        operation_path="/v1/things",
        trace_id="ab" * 16,
        credential_id="cred_a",
        job_id="job_req",
    )


@pytest.mark.asyncio
async def test_handler_denied_job_fails_without_injecting_or_dispatching() -> None:
    """A run-time denial records a failed result carrying the sync route's problem
    body, emits EXECUTION_FAILED, and never touches the credential or upstream."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"ok", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(
        InjectedAuth(headers={"Authorization": "Bearer tok"}, query_params={}, cookies={})
    )
    problem = {
        "type": "no_credential_binding",
        "title": "No credential binding for this API",
        "status": 403,
        "error_origin": "broker",
    }
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_FakeAuthorizer(
            QueuedExecutionVerdict(allowed=False, problem=problem)
        ),
        actor_status=_FakeActorStatus(),
    )

    with (
        patch(
            "jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock
        ) as mock_emit,
        patch(
            "jentic_one.shared.jobs.execution_handler.maybe_emit_repeated_failure",
            new_callable=AsyncMock,
        ) as mock_repeated,
    ):
        result = await handler.execute(
            "job_denied",
            SessionScope(_FakeSession()),
            payload=_payload(credential_id="cred_a", allowed_credential_ids=["cred_a"]),
            created_by="agt_abc123",
            actor_type="agent",
        )

    assert executor.last_request is None
    assert injector.last_trace_id is None  # inject() never called
    assert result.body == {
        "execution_id": "exec_123",
        "status": "failed",
        "http_status": 403,
        "duration_ms": 0,
        "problem": problem,
    }
    assert result.content_type is None
    mock_emit.assert_awaited_once()
    assert mock_emit.call_args.kwargs["type"] == EventType.EXECUTION_FAILED
    assert "no_credential_binding" in mock_emit.call_args.kwargs["summary"]
    mock_repeated.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_denied_job_keys_repeated_failure_on_the_folded_operation() -> None:
    """A run-time denial still counts toward repeated-failure detection with the
    full operation identity folded from the payload — not a dropped id."""
    handler = ExecutionHandler(
        executor=_RecordingExecutor(
            UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
        ),
        credential_injector=_FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={})),
        execution_authorizer=_FakeAuthorizer(
            QueuedExecutionVerdict(allowed=False, problem={"type": "action_denied"})
        ),
        actor_status=_FakeActorStatus(),
    )
    operation = {"id": "op_x", "path": "/v1/things/{id}", "method": "GET"}

    with (
        patch("jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock),
        patch(
            "jentic_one.shared.jobs.execution_handler.maybe_emit_repeated_failure",
            new_callable=AsyncMock,
        ) as mock_repeated,
    ):
        await handler.execute(
            "job_denied_op",
            SessionScope(_FakeSession()),
            payload=_payload(operation=operation, credential_id="cred_a"),
            created_by="agt_abc123",
            actor_type="agent",
        )

    assert mock_repeated.call_args.kwargs["operation"] == OperationInfo(**operation)


@pytest.mark.asyncio
async def test_handler_reauthorizes_on_the_operation_dict_id() -> None:
    """The run-time re-check reads the id off the folded ``operation`` dict."""
    authorizer = _FakeAuthorizer()
    handler = ExecutionHandler(
        executor=_RecordingExecutor(
            UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
        ),
        credential_injector=_FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={})),
        execution_authorizer=authorizer,
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job_dict_only",
        SessionScope(_FakeSession()),
        payload=_payload(operation={"id": "op_dict", "path": "/v1/things", "method": "GET"}),
        created_by="agt_abc123",
        actor_type="agent",
    )

    assert authorizer.last_request is not None
    assert authorizer.last_request.operation_id == "op_dict"
    assert authorizer.last_request.relative_path is None


@pytest.mark.asyncio
async def test_handler_reauthorizes_on_the_operation_relative_path() -> None:
    """Pins #1424: the run-time rule re-check is handed the enqueue-time
    server-relative path, so a base-path API's rules are judged on the same
    path at run time as at the sync edge."""
    authorizer = _FakeAuthorizer()
    handler = ExecutionHandler(
        executor=_RecordingExecutor(
            UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
        ),
        credential_injector=_FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={})),
        execution_authorizer=authorizer,
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job_relative",
        SessionScope(_FakeSession()),
        payload=_payload(
            operation={
                "id": "op_w",
                "path": "/widgets/{id}",
                "method": "GET",
                "relative_path": "/widgets/42",
            }
        ),
        created_by="agt_abc123",
        actor_type="agent",
    )

    assert authorizer.last_request is not None
    assert authorizer.last_request.relative_path == "/widgets/42"


def test_handler_refuses_an_injector_without_an_authorizer() -> None:
    """Wiring a credential injector without the run-time re-check fails closed."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    with pytest.raises(ValueError, match="requires an execution_authorizer"):
        ExecutionHandler(
            executor=executor,
            credential_injector=_FakeInjector(
                InjectedAuth(headers={}, query_params={}, cookies={})
            ),
        )


def test_handler_refuses_an_injector_without_an_actor_status_reader() -> None:
    """Wiring a credential injector without the live actor read fails closed."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    with pytest.raises(ValueError, match="requires an actor_status reader"):
        ExecutionHandler(
            executor=executor,
            credential_injector=_FakeInjector(
                InjectedAuth(headers={}, query_params={}, cookies={})
            ),
            execution_authorizer=_FakeAuthorizer(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_type", ["agent", "user", "service_account"])
async def test_handler_denies_when_the_live_actor_read_is_inactive(actor_type: str) -> None:
    """Pins #1138: an actor the live read reports inactive (disabled, archived or
    missing — or a retired actor type) is denied with the sync edge's 401 even
    after the authorizer allowed it; nothing is injected or dispatched."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"ok", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(
        InjectedAuth(headers={"Authorization": "Bearer tok"}, query_params={}, cookies={})
    )
    actor_status = _FakeActorStatus(active=False)
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_FakeAuthorizer(),
        actor_status=actor_status,
    )

    with patch(
        "jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock
    ) as mock_emit:
        result = await handler.execute(
            "job_gone",
            _FakeSession(),
            payload=_payload(),
            created_by="act_gone",
            actor_type=actor_type,
        )

    assert actor_status.calls == [("act_gone", actor_type)]
    assert executor.last_request is None
    assert injector.last_identity is None
    assert result.body["status"] == "failed"
    assert result.body["http_status"] == 401
    assert result.body["problem"]["type"] == "unauthorized"
    assert result.body["problem"]["instance"] == "/api.example.com/v1/things"
    assert mock_emit.await_args is not None
    assert mock_emit.await_args.kwargs["type"] == EventType.EXECUTION_FAILED


@pytest.mark.asyncio
async def test_handler_injects_for_the_live_active_identity() -> None:
    """The identity handed to the injector carries the live ``active`` read."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )

    injector = _FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={}))
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_FakeAuthorizer(),
        actor_status=_FakeActorStatus(active=True),
    )

    await handler.execute(
        "job_live", _FakeSession(), payload=_payload(), created_by="agt_live", actor_type="agent"
    )

    identity = injector.last_identity
    assert identity is not None
    assert identity.sub == "agt_live"
    assert identity.actor_type == "agent"
    assert identity.active is True
    assert executor.last_request is not None


@pytest.mark.asyncio
async def test_handler_passes_actor_fields_in_metadata() -> None:
    """created_by and actor_type are forwarded in the executor request metadata."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"ok", content_type=None, duration_ms=1)
    )
    handler = ExecutionHandler(executor=executor)

    await handler.execute(
        "job6",
        SessionScope(_FakeSession()),
        payload=_payload(),
        created_by="agt_abc123",
        actor_type="agent",
    )

    req = executor.last_request
    assert req is not None
    assert req.metadata["actor_id"] == "agt_abc123"
    assert req.metadata["actor_type"] == "agent"


@pytest.mark.asyncio
async def test_handler_carries_credential_attribution_and_trace_id() -> None:
    """The worker path attributes like the sync router (#740): the caller's
    trace_id reaches inject() (so the CREDENTIAL_ACCESSED audit event joins to
    the execution) and the resolved credential id/name ride the executor
    metadata (so the pipeline persists them on the execution record — NULL
    must keep meaning "no credential used", async included)."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(
        InjectedAuth(
            headers={},
            query_params={},
            cookies={},
            credential_id="cred_abc",
            credential_name="stripe-live",
        )
    )
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_FakeAuthorizer(),
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job8",
        SessionScope(_FakeSession()),
        payload=_payload(trace_id="ab" * 16),
        created_by="agt_abc123",
        actor_type="agent",
    )

    assert injector.last_trace_id == "ab" * 16
    req = executor.last_request
    assert req is not None
    assert req.metadata["credential_id"] == "cred_abc"
    assert req.metadata["credential_name"] == "stripe-live"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_trace_id", [None, "unknown", "trace-xyz"])
async def test_handler_mints_a_valid_trace_id_for_garbage_payloads(
    bad_trace_id: str | None,
) -> None:
    """A missing/malformed payload trace_id is replaced by one minted valid id
    shared by inject() and the executor metadata — so the credential audit
    event, the lifecycle events, and the persisted execution row still
    correlate with each other, and the literal "unknown" never reaches the
    execution record (#903)."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={}))
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_FakeAuthorizer(),
        actor_status=_FakeActorStatus(),
    )

    payload = _payload()
    payload.pop("trace_id")
    if bad_trace_id is not None:
        payload["trace_id"] = bad_trace_id

    await handler.execute(
        "job9",
        SessionScope(_FakeSession()),
        payload=payload,
        created_by="agt_abc123",
        actor_type="agent",
    )

    minted = injector.last_trace_id
    assert minted is not None
    assert re.fullmatch(r"[0-9a-f]{32}", minted)
    req = executor.last_request
    assert req is not None
    assert req.metadata["trace_id"] == minted


@pytest.mark.asyncio
async def test_handler_no_credential_leaves_attribution_none() -> None:
    """No stored credential used → attribution metadata is None, matching the
    schema's "NULL unambiguously means no credential" contract."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={}))
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_FakeAuthorizer(),
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job9",
        SessionScope(_FakeSession()),
        payload=_payload(),
        created_by="usr_test",
        actor_type="user",
    )

    req = executor.last_request
    assert req is not None
    assert req.metadata["credential_id"] is None
    assert req.metadata["credential_name"] is None


@pytest.mark.asyncio
async def test_handler_rejects_missing_actor_fields() -> None:
    """Without created_by/actor_type, the handler raises ValueError."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"ok", content_type=None, duration_ms=1)
    )
    handler = ExecutionHandler(executor=executor)

    with pytest.raises(ValueError, match="created_by and actor_type are required"):
        await handler.execute("job7", SessionScope(_FakeSession()), payload=_payload())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_type"),
    [(200, EventType.EXECUTION_COMPLETED), (503, EventType.EXECUTION_FAILED)],
)
async def test_handler_tags_lifecycle_events_with_payload_origin(
    status_code: int, expected_type: str
) -> None:
    """The origin persisted in the job payload rides both lifecycle events as a
    closed-enum tag, so async executions split telemetry by surface like the
    sync path (lane D, #1177)."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=status_code, body=b"", content_type=None, duration_ms=1)
    )
    handler = ExecutionHandler(executor=executor)

    with patch(
        "jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock
    ) as mock_emit:
        await handler.execute(
            "job10",
            SessionScope(_FakeSession()),
            payload=_payload(origin="mcp"),
            created_by="agt_abc123",
            actor_type="agent",
        )

    mock_emit.assert_awaited_once()
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["type"] == expected_type
    assert kwargs["tags"] == {Origin.MCP}


@pytest.mark.asyncio
async def test_handler_missing_origin_emits_untagged_event() -> None:
    """Payloads without an origin (or with garbage) emit untagged, never raise."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    handler = ExecutionHandler(executor=executor)

    with patch(
        "jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock
    ) as mock_emit:
        await handler.execute(
            "job11",
            SessionScope(_FakeSession()),
            payload=_payload(),
            created_by="agt_abc123",
            actor_type="agent",
        )

    assert mock_emit.call_args.kwargs["tags"] is None


@pytest.mark.asyncio
async def test_handler_forwards_operation_dict_in_metadata() -> None:
    """The payload's ``operation`` dict (id + path template + method) reaches
    the executor metadata — validated once here (the handler folds it for the
    repeated-failure detector) — and no flat ``operation_id`` key rides
    alongside (#1382)."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    handler = ExecutionHandler(executor=executor)
    operation = {"id": "op_x", "path": "/v1/things/{id}", "method": "GET"}

    await handler.execute(
        "job_op",
        SessionScope(_FakeSession()),
        payload=_payload(operation=operation),
        created_by="agt_abc123",
        actor_type="agent",
    )

    assert executor.last_request is not None
    assert executor.last_request.metadata["operation"] == OperationInfo(**operation).model_dump()
    assert "operation_id" not in executor.last_request.metadata


@pytest.mark.asyncio
async def test_handler_scopes_credentials_on_payload_server_variables() -> None:
    """The server-variable values discovery resolved at enqueue time reach both
    the run-time re-authorization and the credential injection."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={}))
    authorizer = _FakeAuthorizer()
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=authorizer,
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job11",
        SessionScope(_FakeSession()),
        payload=_payload(
            upstream_url="https://api.example.com/eu/things",
            server_variables={"region": "eu"},
        ),
        created_by="usr_test",
        actor_type="user",
    )

    assert injector.last_request_server_variables == {"region": "eu"}
    assert injector.last_server_variables_unresolved is False
    assert authorizer.last_request is not None
    assert authorizer.last_request.server_variables == {"region": "eu"}
    assert authorizer.last_request.server_variables_unresolved is False


@pytest.mark.asyncio
async def test_handler_propagates_unresolved_server_variables() -> None:
    """An enqueue-time "values unknown" flag keeps the worker failing closed."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={}))
    authorizer = _FakeAuthorizer()
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=authorizer,
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job13",
        SessionScope(_FakeSession()),
        payload=_payload(
            upstream_url="https://api.example.com/us/things", server_variables_unresolved=True
        ),
        created_by="usr_test",
        actor_type="user",
    )

    assert injector.last_server_variables_unresolved is True
    assert authorizer.last_request is not None
    assert authorizer.last_request.server_variables_unresolved is True


@pytest.mark.asyncio
async def test_handler_substitutes_server_variable_defaults() -> None:
    """A templated upstream URL falls back to the declared default when the
    credential supplies no value for the variable."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(InjectedAuth(headers={}, query_params={}, cookies={}))
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_FakeAuthorizer(),
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job12",
        SessionScope(_FakeSession()),
        payload=_payload(
            upstream_url="https://api.example.com/{region}/things",
            server_variable_defaults={"region": "us"},
        ),
        created_by="usr_test",
        actor_type="user",
    )

    req = executor.last_request
    assert req is not None
    assert req.url == "https://api.example.com/us/things"


@pytest.mark.asyncio
async def test_handler_replays_payload_headers_under_injected_credentials() -> None:
    """The broker-kept request headers reach the upstream; injected auth wins a clash."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)
    )
    injector = _FakeInjector(
        InjectedAuth(headers={"Authorization": "Bearer tok"}, query_params={}, cookies={})
    )
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,  # pragma: allowlist secret
        execution_authorizer=_FakeAuthorizer(),
        actor_status=_FakeActorStatus(),
    )

    await handler.execute(
        "job_hdr",
        SessionScope(_FakeSession()),
        payload=_payload(
            method="POST",
            body_b64="eyJhIjoxfQ==",
            headers={
                "content-type": "application/json",
                "stripe-version": "2024-06-20",
                "authorization": "Bearer agent",
            },
        ),
        created_by="usr_test",
        actor_type="user",
    )

    req = executor.last_request
    assert req is not None
    assert req.headers["content-type"] == "application/json"
    assert req.headers["stripe-version"] == "2024-06-20"
    assert req.body == b'{"a":1}'
    assert req.headers["Authorization"] == "Bearer tok"
    assert "authorization" not in req.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [pytest.param(DatabaseIntegrityError("dup"), id="integrity"), *post_upstream_write_failures()],
)
async def test_handler_lifecycle_write_failure_still_returns_the_upstream_result(
    error: Exception,
) -> None:
    """The lifecycle transaction runs after the upstream call. Whatever it fails
    with, the job still completes with the real result: raising would requeue
    the job and send the upstream request a second time."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=201, body=b"made", content_type="text/plain", duration_ms=4)
    )
    handler = ExecutionHandler(executor=executor)
    db = FailingTransactions(error)

    with structlog.testing.capture_logs() as logs:
        result = await handler.execute(
            "job-lifecycle",
            db,
            payload=_payload(),
            created_by="usr_test",
            actor_type="user",
        )

    assert db.opened == 1
    assert result.body["status"] == "completed"
    assert result.body["http_status"] == 201
    assert result.body["body_b64"]
    assert result.content_type == "text/plain"
    failed = [e for e in logs if e["event"] == "queued_execution_lifecycle_write_failed"]
    assert len(failed) == 1
    assert failed[0]["error_type"] == type(error).__name__
    # A swallowed bug keeps its traceback; a DB error (SQL + params) does not.
    assert failed[0]["exc_info"] is (error if isinstance(error, TypeError) else False)


@pytest.mark.asyncio
async def test_handler_denial_lifecycle_write_failure_still_returns_the_denial() -> None:
    """A denied job whose lifecycle write fails still completes with its problem."""
    problem = {"type": "no_credential_binding", "status": 403}
    handler = ExecutionHandler(
        executor=_RecordingExecutor(
            UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=0)
        ),
        execution_authorizer=_FakeAuthorizer(
            QueuedExecutionVerdict(allowed=False, problem=problem)
        ),
    )
    db = FailingTransactions(DatabaseUnavailableError("connection lost"))

    result = await handler.execute(
        "job-denied-lifecycle",
        db,
        payload=_payload(),
        created_by="usr_test",
        actor_type="user",
    )

    assert db.opened == 1
    assert result.body["status"] == "failed"
    assert result.body["http_status"] == 403
    assert result.body["problem"] == problem


@pytest.mark.asyncio
async def test_handler_unpersisted_record_reports_the_real_upstream_result() -> None:
    """A record write that fails after a 2xx upstream reports the 2xx (body kept),
    not a pipeline failure, and still emits EXECUTION_COMPLETED."""
    upstream = UpstreamExecResult(
        status_code=200, body=b"ok", content_type="application/json", duration_ms=9
    )
    exc = ExecutionRecordNotPersistedError(upstream)
    exc.__cause__ = DatabaseUnavailableError("connection lost")
    handler = ExecutionHandler(executor=_RaisingExecutor(exc))
    scope = SessionScope(_FakeSession())

    with (
        structlog.testing.capture_logs() as logs,
        patch(
            "jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock
        ) as mock_emit,
    ):
        result = await handler.execute(
            "job-unpersisted",
            scope,
            payload=_payload(),
            created_by="usr_test",
            actor_type="user",
        )

    assert result.body["status"] == "completed"
    assert result.body["http_status"] == 200
    assert result.body["duration_ms"] == 9
    assert result.body["body_b64"]
    assert result.content_type == "application/json"
    assert scope.opened == 1
    assert mock_emit.call_args.kwargs["type"] == EventType.EXECUTION_COMPLETED
    assert not [e for e in logs if e["event"] == "pipeline_error"]
    logged = [e for e in logs if e["event"] == "queued_execution_record_not_persisted"]
    assert len(logged) == 1
    assert logged[0]["error_type"] == "DatabaseUnavailableError"
    assert logged[0]["http_status"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cause", "runner_failed", "label", "traceback"),
    [
        pytest.param(DatabaseUnavailableError("lost"), False, "record", False, id="db-write"),
        pytest.param(TypeError("bad argument"), False, "record", True, id="bug-in-write"),
        pytest.param(RuntimeError("runner crashed"), True, "runner", True, id="runner-crash"),
    ],
)
async def test_handler_unpersisted_record_counts_and_logs_what_failed(
    cause: Exception, runner_failed: bool, label: str, traceback: bool
) -> None:
    """A crashed call counts as ``write=runner``, not as a failed record write,
    and a swallowed bug keeps its traceback while a DB error does not."""
    exc = ExecutionRecordNotPersistedError(
        None, upstream_error=type(cause).__name__, runner_failed=runner_failed
    )
    exc.__cause__ = cause
    handler = ExecutionHandler(executor=_RaisingExecutor(exc))

    with (
        structlog.testing.capture_logs() as logs,
        patch("jentic_one.shared.jobs.execution_handler._record_write_failure") as counted,
        patch("jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock),
    ):
        result = await handler.execute(
            "job-unpersisted-label",
            SessionScope(_FakeSession()),
            payload=_payload(),
            created_by="usr_test",
            actor_type="user",
        )

    assert result.body["status"] == "failed"
    counted.assert_called_once_with(label)
    logged = [e for e in logs if e["event"] == "queued_execution_record_not_persisted"]
    assert len(logged) == 1
    assert logged[0]["exc_info"] is (exc if traceback else False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream_error", "reason"),
    [
        (None, "Execution record not persisted"),
        ("circuit open", "circuit open (execution record not persisted)"),
    ],
)
async def test_handler_unpersisted_record_without_a_result_fails_without_raising(
    upstream_error: str | None, reason: str
) -> None:
    """A record write that fails after the upstream raised completes the job as
    failed (never requeued). The reason keeps the broker's failure and names
    the lost record."""
    handler = ExecutionHandler(
        executor=_RaisingExecutor(
            ExecutionRecordNotPersistedError(None, upstream_error=upstream_error)
        )
    )

    with patch(
        "jentic_one.shared.jobs.execution_handler.emit_event", new_callable=AsyncMock
    ) as mock_emit:
        result = await handler.execute(
            "job-unpersisted-failed",
            SessionScope(_FakeSession()),
            payload=_payload(),
            created_by="usr_test",
            actor_type="user",
        )

    assert result.body["status"] == "failed"
    assert result.body["http_status"] is None
    assert mock_emit.call_args.kwargs["type"] == EventType.EXECUTION_FAILED
    assert mock_emit.call_args.kwargs["summary"] == f"Execution failed: {reason}"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", post_upstream_write_failures())
async def test_handler_audit_failure_before_dispatch_propagates(error: Exception) -> None:
    """A credential-audit failure happens before the upstream call, so it
    propagates (the worker requeues the job) and nothing is dispatched."""
    executor = _RaisingExecutor(AssertionError("must not dispatch"))

    class _FailingInjector(_FakeInjector):
        async def inject(self, **_kwargs: Any) -> InjectedAuth:
            raise error

    handler = ExecutionHandler(
        executor=executor,
        credential_injector=_FailingInjector(InjectedAuth(headers={}, query_params={}, cookies={})),
        execution_authorizer=_FakeAuthorizer(),
    )

    with pytest.raises(type(error)):
        await handler.execute(
            "job-audit-failed",
            SessionScope(_FakeSession()),
            payload=_payload(),
            created_by="usr_test",
            actor_type="user",
        )

    assert executor.called is False
