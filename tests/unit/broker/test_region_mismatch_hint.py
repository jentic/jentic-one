"""Unit tests for the region-mismatch hint on upstream 401/403 (#638).

When an upstream returns 401/403 for an API whose spec uses a templated host /
server variable (e.g. ``https://{region}.posthog.com``), the broker attaches a
``Jentic-Hint`` header pointing at a likely region/server-variable mismatch —
without rewriting the mirrored upstream body (§6b B-002 passthrough invariant).
"""

from __future__ import annotations

import pytest

from jentic_one.broker.core.headers import REGION_MISMATCH_HINT, JenticHeader
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import _assemble_response
from jentic_one.broker.web.streaming import _metadata_headers as _streaming_metadata_headers
from jentic_one.shared.broker.execution import (
    ErrorOrigin,
    ExecutionContext,
    ExecutionOutcome,
    RunnerResult,
)

_HINT_HEADER = JenticHeader.HINT.value


def _ctx_req(*, has_server_variable: bool) -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://us.posthog.com/api/projects",
        method="GET",
        trace_id="trace-1",
        operation_id="op-1",
        api_vendor="posthog.com",
        api_name="posthog",
        api_version="v1",
        has_server_variable=has_server_variable,
    )


def _outcome(status_code: int, body: bytes = b'{"detail":"Invalid Key"}') -> ExecutionOutcome:
    result = RunnerResult(
        status_code=status_code,
        body=body,
        headers={"content-type": "application/json"},
        content_type="application/json",
        duration_ms=1,
    )
    context = ExecutionContext(
        execution_id="exec-1",
        toolkit_id="tk-1",
        operation_id="op-1",
        api=None,
        trace_id="trace-1",
    )
    error_origin = ErrorOrigin.UPSTREAM if status_code >= 400 else None
    return ExecutionOutcome(result=result, context=context, error_origin=error_origin)


@pytest.mark.parametrize("status_code", [401, 403])
def test_hint_added_for_401_403_on_templated_host_api(status_code: int) -> None:
    response = _assemble_response(_outcome(status_code), _ctx_req(has_server_variable=True))
    assert response.status_code == status_code
    assert response.headers[_HINT_HEADER] == REGION_MISMATCH_HINT


def test_hint_does_not_rewrite_upstream_body() -> None:
    body = b'{"type":"authentication_error","detail":"Invalid personal API key."}'
    response = _assemble_response(_outcome(401, body=body), _ctx_req(has_server_variable=True))
    # Body is mirrored verbatim — the hint rides on the header, never the body.
    assert response.body == body
    assert response.headers[_HINT_HEADER] == REGION_MISMATCH_HINT


def test_no_hint_when_api_has_no_server_variable() -> None:
    response = _assemble_response(_outcome(401), _ctx_req(has_server_variable=False))
    assert _HINT_HEADER not in response.headers


@pytest.mark.parametrize("status_code", [200, 404, 429, 500])
def test_no_hint_for_non_auth_statuses(status_code: int) -> None:
    response = _assemble_response(_outcome(status_code), _ctx_req(has_server_variable=True))
    assert _HINT_HEADER not in response.headers


@pytest.mark.parametrize("status_code", [401, 403])
def test_streaming_hint_added_for_401_403_on_templated_host_api(status_code: int) -> None:
    headers = _streaming_metadata_headers(_ctx_req(has_server_variable=True), "exec-1", status_code)
    assert headers[_HINT_HEADER] == REGION_MISMATCH_HINT


def test_streaming_no_hint_when_api_has_no_server_variable() -> None:
    headers = _streaming_metadata_headers(_ctx_req(has_server_variable=False), "exec-1", 401)
    assert _HINT_HEADER not in headers


def test_streaming_no_hint_for_non_auth_status() -> None:
    headers = _streaming_metadata_headers(_ctx_req(has_server_variable=True), "exec-1", 500)
    assert _HINT_HEADER not in headers
