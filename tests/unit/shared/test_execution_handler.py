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

from typing import Any

import pytest

from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.protocols import (
    InjectedAuth,
    UpstreamExecRequest,
    UpstreamExecResult,
)


class _FakeSession:
    """A session whose every attribute access is a no-op coroutine target.

    The handler only touches the session via ``emit_event`` (best-effort,
    wrapped in try/except), so it never needs to behave like a real session.
    """


class _RecordingExecutor:
    def __init__(self, result: UpstreamExecResult) -> None:
        self._result = result
        self.last_request: UpstreamExecRequest | None = None

    async def execute(self, request: UpstreamExecRequest, *, session: Any) -> UpstreamExecResult:
        self.last_request = request
        return self._result


class _RaisingExecutor:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.called = False

    async def execute(self, request: UpstreamExecRequest, *, session: Any) -> UpstreamExecResult:
        self.called = True
        raise self._exc


class _FakeInjector:
    def __init__(self, injection: InjectedAuth) -> None:
        self._injection = injection

    async def inject(
        self,
        *,
        api_vendor: str,
        api_name: str,
        api_version: str,
        identity: Any,
        credential_name: str | None = None,
        trace_id: str | None = None,
    ) -> InjectedAuth:
        return self._injection


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "execution_id": "exec_123",
        "upstream_url": "https://api.example.com/v1/things",
        "method": "GET",
        "trace_id": "unknown",
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
        "job1", _FakeSession(), payload=_payload(), created_by="usr_test", actor_type="user"
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
        "job2", _FakeSession(), payload=_payload(), created_by="usr_test", actor_type="user"
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
        "job3", _FakeSession(), payload=_payload(), created_by="usr_test", actor_type="user"
    )

    assert executor.called is True
    assert result.body["status"] == "failed"
    assert result.body["http_status"] is None


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
    )

    await handler.execute(
        "job4", _FakeSession(), payload=_payload(), created_by="usr_test", actor_type="user"
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
        "job5", _FakeSession(), payload=_payload(), created_by="usr_test", actor_type="user"
    )

    req = executor.last_request
    assert req is not None
    assert req.headers == {}
    assert "?" not in req.url


@pytest.mark.asyncio
async def test_handler_passes_actor_fields_in_metadata() -> None:
    """created_by and actor_type are forwarded in the executor request metadata."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"ok", content_type=None, duration_ms=1)
    )
    handler = ExecutionHandler(executor=executor)

    await handler.execute(
        "job6",
        _FakeSession(),
        payload=_payload(),
        created_by="agt_abc123",
        actor_type="agent",
    )

    req = executor.last_request
    assert req is not None
    assert req.metadata["actor_id"] == "agt_abc123"
    assert req.metadata["actor_type"] == "agent"


@pytest.mark.asyncio
async def test_handler_rejects_missing_actor_fields() -> None:
    """Without created_by/actor_type, the handler raises ValueError."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"ok", content_type=None, duration_ms=1)
    )
    handler = ExecutionHandler(executor=executor)

    with pytest.raises(ValueError, match="created_by and actor_type are required"):
        await handler.execute("job7", _FakeSession(), payload=_payload())
