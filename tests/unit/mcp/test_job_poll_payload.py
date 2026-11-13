"""Unit tests for MCP job polling and result attachment across terminal states."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import mcp.types as mcp_types
import pytest

import jentic_one.mcp.execute as ex
import jentic_one.mcp.tools as tools_mod
from jentic_one.admin.services.errors import (
    JobNotCompletedError,
    JobNotFoundError,
    JobResultExpiredError,
)
from jentic_one.admin.services.schemas.jobs import JobResultView, JobView
from jentic_one.mcp.tools import CallEnv, dispatch_mcp_tool_call, job_poll_payload
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType

_NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)


def _env(*, sub: str = "agnt_poll_1", permissions: list[str] | None = None) -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    server = ServerConfig()
    server.mcp.enabled = True
    ctx.config.server = server
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(
            sub=sub,
            permissions=permissions if permissions is not None else ["jobs:read"],
            actor_type=ActorType.AGENT,
        ),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
    )


def _job_view(
    job_id: str,
    *,
    status: str,
    error: str | None = None,
    execution_id: str | None = "exec_poll_1",
    result_available: bool = False,
) -> JobView:
    return JobView(
        id=job_id,
        kind="execution",
        status=status,
        execution_id=execution_id,
        error=error,
        result_available=result_available,
        created_at=_NOW,
        updated_at=_NOW,
        actor_id="agnt_poll_1",
        actor_type="agent",
    )


def _result_view(
    job_id: str,
    *,
    body: dict[str, Any],
    content_type: str | None = "application/json",
) -> JobResultView:
    raw_body = json.dumps(body).encode("utf-8") if content_type else None
    return JobResultView(
        id="jres_poll_1",
        job_id=job_id,
        kind="execution",
        body=body,
        content_type=content_type,
        raw_body=raw_body,
        created_at=_NOW,
    )


def _install_job_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    job: JobView | Exception,
    result: JobResultView | Exception,
) -> None:
    class _StubJobService:
        def __init__(self, _ctx: Any) -> None:
            pass

        async def get_by_id(self, _job_id: str, *, identity: Identity) -> JobView:
            assert identity.sub
            if isinstance(job, Exception):
                raise job
            return job

    class _StubJobResultService:
        def __init__(self, _ctx: Any) -> None:
            pass

        async def get(self, _job_id: str, *, identity: Identity) -> JobResultView:
            assert identity.sub
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(tools_mod, "JobService", _StubJobService)
    monkeypatch.setattr(tools_mod, "JobResultService", _StubJobResultService)


def _decode(call_result: Any) -> dict[str, Any]:
    assert isinstance(call_result, mcp_types.CallToolResult)
    (content,) = call_result.content
    assert isinstance(content, mcp_types.TextContent)
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


async def test_poll_completed_job_attaches_result_and_caps_oversized_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completed execution jobs attach their stored result and enforce MAX_RESULT_BYTES."""
    body = {
        "execution_id": "exec_poll_1",
        "status": "completed",
        "http_status": 200,
        "duration_ms": 18,
        "body_b64": base64.b64encode(b'{"ok":true}').decode(),
    }
    _install_job_stubs(
        monkeypatch,
        job=_job_view("job_done", status="completed", result_available=True),
        result=_result_view("job_done", body=body),
    )
    res = await dispatch_mcp_tool_call(
        _env(), "get_execution_result", {"job_id": "job_done", "timeout": 0}
    )
    payload = _decode(res)
    assert payload["status"] == "completed"
    assert payload["result"] == body
    assert "result_error" not in payload

    oversized_body = {
        "execution_id": "exec_poll_1",
        "status": "completed",
        "http_status": 200,
        "duration_ms": 18,
        "body_b64": "A" * (ex.MAX_RESULT_BYTES + 256),
    }
    _install_job_stubs(
        monkeypatch,
        job=_job_view("job_big", status="completed", result_available=True),
        result=_result_view("job_big", body=oversized_body),
    )
    capped = await job_poll_payload(_env(), "job_big")
    assert capped["truncated"] is True
    assert capped["total_bytes"] > ex.MAX_RESULT_BYTES
    assert len(capped["result"].encode("utf-8")) <= ex.MAX_RESULT_BYTES


async def test_poll_failed_job_with_stored_result_attaches_actionable_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins #2030: a failed job with a stored JobResult attaches the outcome."""
    upstream_failure = {
        "execution_id": "exec_poll_fail",
        "status": "failed",
        "http_status": 422,
        "duration_ms": 27,
        "body_b64": base64.b64encode(b'{"error":"invalid_parameter"}').decode(),
    }
    _install_job_stubs(
        monkeypatch,
        job=_job_view("job_fail_res", status="failed", error=None, result_available=True),
        result=_result_view("job_fail_res", body=upstream_failure),
    )
    res = await dispatch_mcp_tool_call(
        _env(), "get_execution_result", {"job_id": "job_fail_res", "timeout": 0}
    )
    payload = _decode(res)
    assert payload["status"] == "failed"
    assert "error" not in payload
    assert "result_error" not in payload
    assert payload["result"] == upstream_failure

    runtime_denial = {
        "execution_id": "exec_poll_deny",
        "status": "failed",
        "http_status": 403,
        "duration_ms": 0,
        "problem": {
            "type": "permission_denied",
            "title": "Forbidden",
            "status": 403,
            "detail": "Binding revoked before execution",
            "error_origin": "broker",
        },
    }
    _install_job_stubs(
        monkeypatch,
        job=_job_view("job_fail_deny", status="failed", error=None, result_available=True),
        result=_result_view("job_fail_deny", body=runtime_denial, content_type=None),
    )
    denied_payload = await job_poll_payload(_env(), "job_fail_deny")
    assert denied_payload["status"] == "failed"
    assert denied_payload["result"] == runtime_denial


async def test_poll_failed_job_without_stored_result_preserves_job_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed job with no stored JobResult keeps job.error and omits result/result_error."""
    _install_job_stubs(
        monkeypatch,
        job=_job_view("job_plain_fail", status="failed", error="handler raised boom"),
        result=JobNotCompletedError("job_plain_fail"),
    )
    payload = await job_poll_payload(_env(), "job_plain_fail")
    assert payload["status"] == "failed"
    assert payload["error"] == "handler raised boom"
    assert "result" not in payload
    assert "result_error" not in payload


async def test_poll_failed_job_with_expired_result_reports_result_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired failed-job result degrades to result_error without leaking or failing the poll."""
    _install_job_stubs(
        monkeypatch,
        job=_job_view("job_expired", status="failed", error=None, result_available=False),
        result=JobResultExpiredError("job_expired"),
    )
    payload = await job_poll_payload(_env(), "job_expired")
    assert payload["status"] == "failed"
    assert "result" not in payload
    assert "expired" in payload["result_error"]


async def test_poll_missing_or_cross_user_job_returns_resolve_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Out-of-scope or missing jobs return RESOLVE_FAILED with next_tool=get_execution_result."""
    _install_job_stubs(
        monkeypatch,
        job=JobNotFoundError("job_hidden"),
        result=JobNotCompletedError("job_hidden"),
    )
    res = await dispatch_mcp_tool_call(
        _env(), "get_execution_result", {"job_id": "job_hidden", "timeout": 0}
    )
    assert isinstance(res, mcp_types.CallToolResult) and res.is_error
    payload = _decode(res)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert payload["next_tool"] == "get_execution_result"
