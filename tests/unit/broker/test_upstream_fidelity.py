"""Fidelity lock-in for upstream passthrough (#740).

The broker's #740 report was "a 403 became a 400". A code audit of ``main``
confirms the broker never rewraps upstream status/body/`Www-Authenticate` —
it mirrors them verbatim through ``_assemble_response`` +
``passthrough_response_headers``, and enriches only via ``Jentic-*`` headers
(``Upstream-Status``, ``Error-Origin``, ``Hint``). These tests are the
regression fence: if the reporter's rewrap ever recurs, this bisects it to
client-side handling instead of the broker.
"""

from __future__ import annotations

import pytest

from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.idempotency import StoredResponse
from jentic_one.broker.web.routers.execute import _assemble_response, _replay_response
from jentic_one.shared.broker.execution import (
    ErrorOrigin,
    ExecutionContext,
    ExecutionOutcome,
    RunnerResult,
)
from jentic_one.shared.schemas import OperationInfo


def _ctx_req(**overrides: object) -> ExecuteRequestContext:
    base: dict[str, object] = {
        "upstream_url": "https://api.example.com/v1/things",
        "method": "GET",
        "trace_id": "trace-1",
        "operation": OperationInfo(id="op-1"),
        "api_vendor": "example",
        "api_name": "widgets",
        "api_version": "v1",
    }
    base.update(overrides)
    return ExecuteRequestContext(**base)  # type: ignore[arg-type]


def _outcome(
    *,
    status_code: int,
    body: bytes,
    upstream_headers: dict[str, str] | None = None,
) -> ExecutionOutcome:
    result = RunnerResult(
        status_code=status_code,
        body=body,
        headers=upstream_headers or {"content-type": "application/json"},
        content_type="application/json",
        duration_ms=1,
    )
    context = ExecutionContext(
        execution_id="exec-1",
        toolkit_id="tk-1",
        operation=OperationInfo(id="op-1"),
        api=None,
        trace_id="trace-1",
    )
    error_origin = ErrorOrigin.UPSTREAM if status_code >= 400 else None
    return ExecutionOutcome(result=result, context=context, error_origin=error_origin)


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (401, b'{"error":"invalid_token"}'),
        (403, b'{"error":"insufficient_scope","detail":"needs write"}'),
        (404, b'{"error":"not_found"}'),
        (429, b'{"error":"rate_limited"}'),
        (500, b"upstream is on fire"),
    ],
)
def test_upstream_status_and_body_are_mirrored_verbatim(status_code: int, body: bytes) -> None:
    """The broker never rewraps upstream status/body (B-002 passthrough)."""
    response = _assemble_response(_outcome(status_code=status_code, body=body), _ctx_req())

    assert response.status_code == status_code
    assert response.body == body
    # ``Jentic-Upstream-Status`` mirrors the exact upstream code — the response
    # status is authoritative but the header preserves it for observability.
    assert response.headers[JenticHeader.UPSTREAM_STATUS.value] == str(status_code)
    # 4xx/5xx are tagged as upstream-origin so a caller can distinguish an
    # upstream rejection from a broker-origin failure without inspecting body.
    assert response.headers[JenticHeader.ERROR_ORIGIN.value] == ErrorOrigin.UPSTREAM.value


def test_www_authenticate_passes_through_verbatim() -> None:
    """OAuth/API-key upstreams often carry ``Www-Authenticate`` on 401 — mirror it (#740)."""
    challenge = 'Bearer realm="api.example.com", error="invalid_token"'
    response = _assemble_response(
        _outcome(
            status_code=401,
            body=b'{"error":"invalid_token"}',
            upstream_headers={
                "content-type": "application/json",
                "www-authenticate": challenge,
            },
        ),
        _ctx_req(),
    )

    assert response.headers["www-authenticate"] == challenge


def test_success_body_and_headers_are_mirrored_verbatim() -> None:
    body = b'{"id":42,"name":"widget"}'
    upstream_headers = {"content-type": "application/json", "etag": '"abc123"'}
    response = _assemble_response(
        _outcome(status_code=200, body=body, upstream_headers=upstream_headers),
        _ctx_req(),
    )

    assert response.status_code == 200
    assert response.body == body
    assert response.headers["etag"] == '"abc123"'
    # No upstream error → no ``Jentic-Error-Origin`` (broker success/upstream success).
    assert JenticHeader.ERROR_ORIGIN.value not in response.headers
    assert response.headers[JenticHeader.UPSTREAM_STATUS.value] == "200"


def test_upstream_cache_control_is_replaced_with_private_no_store() -> None:
    """Pins #1835: the body was fetched with a broker-injected credential, so an
    upstream ``public`` directive never reaches a shared cache in front of the
    broker; non-JSON bodies included."""
    response = _assemble_response(
        _outcome(
            status_code=200,
            body=b"a,b\n1,2\n",
            upstream_headers={
                "content-type": "text/csv",
                "cache-control": "public, max-age=3600",
                "etag": '"abc123"',
            },
        ),
        _ctx_req(),
    )

    assert response.headers.getlist("cache-control") == ["private, no-store"]
    assert response.headers["etag"] == '"abc123"'


def test_idempotent_replay_carries_private_no_store() -> None:
    stored = StoredResponse(
        status_code=200,
        headers={"content-type": "application/json", "Cache-Control": "public, max-age=60"},
        body=b"{}",
        body_omitted=False,
    )
    response = _replay_response(stored)

    assert response.headers.getlist("cache-control") == ["private, no-store"]
    assert response.headers[JenticHeader.IDEMPOTENT_REPLAYED.value] == "true"
