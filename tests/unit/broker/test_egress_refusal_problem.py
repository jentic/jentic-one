"""Broker problem+json for egress-policy refusals (#1722).

A DNS name that resolves into ``broker.egress.allowed_private_subnets`` but
matches no ``broker.egress.allowed_internal_domains`` suffix gets its own
``private_name_not_in_allowed_domains`` type and an operator-facing
``prompt_human`` directive, but only for a caller that has passed authorization
for the registered operation. Anyone else gets the generic
``invalid_upstream_url`` exactly as for any blocked address, so the type is
never a DNS oracle. Every refusal is logged as ``upstream_egress_refused`` with
the real reason, and no body ever carries the resolved internal address.
"""

from __future__ import annotations

import json
import socket
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import structlog
from fastapi import FastAPI, Request, Response

import jentic_one.broker.web.routers.execute as execute_router
from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, StreamingResult
from jentic_one.broker.adapters.runners.circuit import CircuitBreakerRunner
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner
from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    OperationNotFoundError,
    UpstreamTimeoutError,
    UpstreamUrlNotAllowedError,
    upstream_url_not_allowed,
)
from jentic_one.broker.core.problem import broker_error_problem
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.execution.authorization import ExecutionAuthorization
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.mcp import execute as mcp_execute
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.egress_refusal import egress_refusal, egress_refusal_problem
from jentic_one.shared.broker.protocols import ResolveResult, RuleVerdict
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.egress import DnsPinningTransport
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.protocols import (
    QueuedExecutionRequest,
    QueuedExecutionVerdict,
    UpstreamExecRequest,
    UpstreamExecResult,
)
from jentic_one.shared.models import CredentialType, StoredCredentialType
from jentic_one.shared.resilience import CircuitBreaker
from jentic_one.shared.schemas import APIReference, OperationInfo
from jentic_one.shared.state.backend import MemoryStateBackend
from jentic_one.shared.url_validation import (
    BLOCKED_RANGE_MESSAGE,
    EgressPolicyError,
    PrivateNameNotAllowedError,
)

_HOST = "billing.svc.cluster.local"
_INTERNAL_IP = "10.50.2.10"
_SUBNET_ONLY = EgressConfig(allowed_private_subnets=["10.50.0.0/16"])
_CONFIG_KEY = "broker.egress.allowed_internal_domains"
_HINT_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "cli"
    / "internal"
    / "agentops"
    / "testdata"
    / "broker_egress_allowlist_hint.txt"
)


def _resolving_to(*ips: str) -> Any:
    def _inner(_host: str, *_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0)) for ip in ips]

    return _inner


def _assert_no_address(body: dict[str, Any]) -> None:
    rendered = json.dumps(body)
    assert _INTERNAL_IP not in rendered
    assert "10.50.0.0" not in rendered


def _assert_private_name_problem(exc: UpstreamUrlNotAllowedError) -> None:
    body = broker_error_problem(exc)
    assert body["status"] == 400
    assert body["type"] == "private_name_not_in_allowed_domains"
    assert body["error_origin"] == "broker"
    assert _CONFIG_KEY in body["detail"]
    directive = body["agent_directive"]
    assert directive["strategy"] == "prompt_human"
    assert directive["parameters"] == {
        "config_key": _CONFIG_KEY,
        "env_var": "JENTIC__BROKER__EGRESS__ALLOWED_INTERNAL_DOMAINS",
    }
    _assert_no_address(body)


def _assert_generic_problem(exc: UpstreamUrlNotAllowedError) -> None:
    body = broker_error_problem(exc)
    assert body["type"] == "invalid_upstream_url"
    assert body["detail"] == BLOCKED_RANGE_MESSAGE
    assert "agent_directive" not in body
    # Indistinguishable from any other blocked address: not even the host.
    assert _HOST not in json.dumps(body)
    _assert_no_address(body)


# --- the mapping ------------------------------------------------------------------


def test_private_name_refusal_maps_to_distinct_problem_for_an_authorized_caller() -> None:
    _assert_private_name_problem(
        upstream_url_not_allowed(PrivateNameNotAllowedError(_HOST), authorized=True)
    )


def test_private_name_refusal_is_generic_for_an_unauthorized_caller() -> None:
    _assert_generic_problem(
        upstream_url_not_allowed(PrivateNameNotAllowedError(_HOST), authorized=False)
    )


@pytest.mark.parametrize("authorized", [True, False])
@pytest.mark.parametrize(
    "exc",
    [
        EgressPolicyError(BLOCKED_RANGE_MESSAGE),
        EgressPolicyError("upstream URL targets a blocked hostname"),
        ValueError("unsupported scheme: ftp"),
    ],
)
def test_other_refusals_keep_invalid_upstream_url(exc: ValueError, authorized: bool) -> None:
    body = broker_error_problem(upstream_url_not_allowed(exc, authorized=authorized))
    assert body["status"] == 400
    assert body["type"] == "invalid_upstream_url"
    assert body["detail"] == str(exc)
    assert "agent_directive" not in body


def test_directive_never_advises_widening_the_allowlist() -> None:
    # Pins #1722: the suffix list is the second factor that keeps a public name
    # out of the operator's private subnet. The directive must never coach a
    # (possibly prompt-injected) agent into getting it widened for a host it
    # chose, nor echo that host into text addressed to a human.
    body = broker_error_problem(
        upstream_url_not_allowed(PrivateNameNotAllowedError(_HOST), authorized=True)
    )
    instruction = body["agent_directive"]["human_readable_instruction"]
    assert "do not ask for the allowlist to be widened" in instruction.lower()
    assert "never for a public domain" in instruction
    assert "not agent-recoverable" in instruction
    assert _HOST not in instruction
    assert "host" not in body["agent_directive"]["parameters"]
    for advice in ("then retry", "ask your operator to add", "add a suffix covering"):
        assert advice not in instruction.lower()


def test_shared_exception_message_is_surface_neutral() -> None:
    # Ingest, overlays and the control plane interpolate str(exc) into their own
    # errors, so the shared message must not name one surface's config keys.
    message = str(PrivateNameNotAllowedError(_HOST))
    assert "broker." not in message
    assert "allowed_internal_domains" not in message
    assert "allowed_private_subnets" not in message


# --- pre-flight (the execute route) -------------------------------------------------


class _MissResolver:
    """A registry resolver that serves no operation (the route then answers 404)."""

    async def resolve_operation(self, **_kwargs: Any) -> None:
        return None


class _HitResolver:
    """A registry resolver that serves one registered operation on ``_HOST``."""

    async def resolve_operation(self, **_kwargs: Any) -> ResolveResult:
        return ResolveResult(
            operation=OperationInfo(
                id="op_invoices", path="/v1/invoices", method="GET", relative_path="/v1/invoices"
            ),
            api=APIReference(vendor=_HOST, name="billing", version="1"),
            path_params={},
        )


def _app(resolver: Any) -> FastAPI:
    app = FastAPI()
    app.state.broker_registry_resolver = resolver
    return app


def _request_for(host: str, resolver: Any) -> Request:
    path = f"/{host}/v1/invoices"
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "app": _app(resolver),
    }
    return Request(scope)


def _ctx(egress: EgressConfig) -> Any:
    ctx = MagicMock()
    ctx.config.broker.egress = egress
    return ctx


async def _run_handle(request: Request, egress: EgressConfig) -> Response:
    return await _handle(
        request=request,
        method="GET",
        ctx=_ctx(egress),
        identity=Identity(sub="agnt_test", permissions=[]),
        credential_deriver=MagicMock(),
        agent_rule_evaluator=MagicMock(),
        runner=MagicMock(),
        idempotency=None,
    )


def _authorize_as(outcome: ExecutionAuthorization | Exception) -> Any:
    """Stand-in for the route's authorization step.

    Authorization policy has its own suites; these tests pin only what the
    route does with its outcome while a domain-suffix refusal is withheld.
    """

    async def _authorize(**_kwargs: Any) -> ExecutionAuthorization:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return _authorize


_ALLOWED = ExecutionAuthorization(
    allowed_credential_ids=["cred_billing"],
    selected_credential=ResolvedCredential(
        credential_id="cred_billing",
        name="billing-key",
        wire_type=CredentialType.API_KEY,
        stored_type=StoredCredentialType.API_KEY,
        provider=_HOST,
    ),
    verdict=RuleVerdict.ALLOW,
)
_DENIED = ActionDeniedError(detail="denied", type="action_denied")


def _refusal_logs(logs: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == "upstream_egress_refused"]


@pytest.mark.asyncio
async def test_preflight_unregistered_private_name_gets_generic_problem_and_operator_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unregistered URL never reaches authorization: the caller gets exactly
    # what any blocked address gets, the operator log gets the real reason.
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    with structlog.testing.capture_logs() as logs, pytest.raises(UpstreamUrlNotAllowedError) as e:
        await _run_handle(_request_for(_HOST, _MissResolver()), _SUBNET_ONLY)
    _assert_generic_problem(e.value)
    [entry] = _refusal_logs(logs)
    assert entry["host"] == _HOST
    assert entry["stage"] == "preflight"
    assert entry["reason"] == "private_name_not_in_allowed_domains"
    assert entry["problem_type"] == "invalid_upstream_url"
    assert _INTERNAL_IP not in json.dumps(entry)


@pytest.mark.asyncio
async def test_preflight_unregistered_private_name_never_reaches_the_unregistered_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    called: list[str] = []

    async def _hook(**kwargs: Any) -> Response:
        called.append(kwargs["upstream_url"])
        return Response(status_code=204)

    request = _request_for(_HOST, _MissResolver())
    request.app.state.unregistered_url_handler = _hook
    with pytest.raises(UpstreamUrlNotAllowedError) as e:
        await _run_handle(request, _SUBNET_ONLY)
    _assert_generic_problem(e.value)
    assert called == []


@pytest.mark.asyncio
async def test_preflight_registered_private_name_denied_caller_gets_generic_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A denial would otherwise answer 403 here but 400 for any other blocked
    # address, telling an unprivileged caller the name resolves into the subnet.
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    monkeypatch.setattr(execute_router, "authorize_execution", _authorize_as(_DENIED))
    with structlog.testing.capture_logs() as logs, pytest.raises(UpstreamUrlNotAllowedError) as e:
        await _run_handle(_request_for(_HOST, _HitResolver()), _SUBNET_ONLY)
    _assert_generic_problem(e.value)
    [entry] = _refusal_logs(logs)
    assert entry["reason"] == "private_name_not_in_allowed_domains"
    assert entry["problem_type"] == "invalid_upstream_url"


@pytest.mark.asyncio
async def test_preflight_registered_private_name_authorized_caller_gets_distinct_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    monkeypatch.setattr(execute_router, "authorize_execution", _authorize_as(_ALLOWED))
    with structlog.testing.capture_logs() as logs, pytest.raises(UpstreamUrlNotAllowedError) as e:
        await _run_handle(_request_for(_HOST, _HitResolver()), _SUBNET_ONLY)
    _assert_private_name_problem(e.value)
    [entry] = _refusal_logs(logs)
    assert entry["host"] == _HOST
    assert entry["problem_type"] == "private_name_not_in_allowed_domains"


@pytest.mark.asyncio
async def test_preflight_private_ip_outside_subnets_keeps_generic_problem_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("192.168.1.5"))
    with structlog.testing.capture_logs() as logs, pytest.raises(UpstreamUrlNotAllowedError) as e:
        await _run_handle(_request_for(_HOST, _HitResolver()), _SUBNET_ONLY)
    assert e.value.type == "invalid_upstream_url"
    assert e.value.directive is None
    [entry] = _refusal_logs(logs)
    assert entry["reason"] == "blocked_target"
    assert "192.168.1.5" not in json.dumps(entry)


@pytest.mark.asyncio
async def test_preflight_host_in_both_allowlists_passes_the_egress_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    egress = EgressConfig(
        allowed_private_subnets=["10.50.0.0/16"],
        allowed_internal_domains=[".svc.cluster.local"],
    )
    # Past the egress check the route goes on to discovery; the miss resolver
    # answers 404, which proves the URL was allowed.
    with pytest.raises(OperationNotFoundError):
        await _run_handle(_request_for(_HOST, _MissResolver()), egress)


# --- end to end through the broker's problem handler ---------------------------------


def _asgi_app(resolver: Any, egress: EgressConfig) -> FastAPI:
    app = _app(resolver)
    install_broker_error_handlers(app)

    @app.get("/{path:path}")
    async def _proxy(request: Request) -> Response:
        return await _run_handle(request, egress)

    return app


async def _get(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        return await client.get(f"/{_HOST}/v1/invoices")


@pytest.mark.asyncio
async def test_asgi_authorized_private_name_renders_distinct_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    monkeypatch.setattr(execute_router, "authorize_execution", _authorize_as(_ALLOWED))
    response = await _get(_asgi_app(_HitResolver(), _SUBNET_ONLY))
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["jentic-error-origin"] == "broker"
    body = response.json()
    assert body["type"] == "private_name_not_in_allowed_domains"
    assert body["agent_directive"]["strategy"] == "prompt_human"
    _assert_no_address(body)


@pytest.mark.asyncio
async def test_asgi_unregistered_private_name_renders_generic_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    response = await _get(_asgi_app(_MissResolver(), _SUBNET_ONLY))
    assert response.status_code == 400
    assert response.headers["jentic-error-origin"] == "broker"
    assert response.json() == {
        "type": "invalid_upstream_url",
        "title": BLOCKED_RANGE_MESSAGE,
        "status": 400,
        "detail": BLOCKED_RANGE_MESSAGE,
        "error_origin": "broker",
    }


# --- connect time (the DNS-pinning transport under HttpRunner) ----------------------


def _pinned_runner(egress: EgressConfig) -> tuple[HttpRunner, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204, stream=httpx.ByteStream(b""))

    transport = DnsPinningTransport(httpx.MockTransport(_handler), egress)
    return HttpRunner(httpx.AsyncClient(transport=transport)), seen


_REQUEST = RunnerRequest(method="GET", url=f"https://{_HOST}/api")


@pytest.mark.asyncio
async def test_connect_time_private_name_outside_domains_gets_distinct_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    with structlog.testing.capture_logs() as logs, pytest.raises(UpstreamUrlNotAllowedError) as e:
        await runner.run(_REQUEST)
    _assert_private_name_problem(e.value)
    assert seen == []
    [entry] = _refusal_logs(logs)
    assert entry["stage"] == "connect"


@pytest.mark.asyncio
async def test_connect_time_stream_private_name_outside_domains_gets_distinct_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    with pytest.raises(UpstreamUrlNotAllowedError) as exc_info:
        async with runner.stream(_REQUEST):
            pass
    _assert_private_name_problem(exc_info.value)
    assert seen == []


@pytest.mark.asyncio
async def test_connect_time_rebind_outside_subnets_gets_generic_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("169.254.169.254"))
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    with pytest.raises(UpstreamUrlNotAllowedError) as exc_info:
        await runner.run(_REQUEST)
    assert exc_info.value.type == "invalid_upstream_url"
    assert exc_info.value.detail == BLOCKED_RANGE_MESSAGE
    assert seen == []


@pytest.mark.asyncio
async def test_connect_time_public_host_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("93.184.216.34"))
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    result = await runner.run(RunnerRequest(method="GET", url="https://api.example.com/v1"))
    assert result.status_code == 204
    assert len(seen) == 1


def _nxdomain(*_a: Any, **_k: Any) -> Any:
    raise socket.gaierror("nope")


@pytest.mark.asyncio
async def test_connect_time_unresolvable_host_is_a_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A host the pinning transport cannot resolve is a typed, pre-send connect
    # failure (504), never an unhandled ValueError (a 500).
    monkeypatch.setattr(socket, "getaddrinfo", _nxdomain)
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    with pytest.raises(UpstreamTimeoutError) as exc_info:
        await runner.run(RunnerRequest(method="GET", url="https://gone.example.com/v1"))
    assert exc_info.value.type == "upstream_connect_failed"
    assert exc_info.value.pre_send is True
    assert seen == []


@pytest.mark.asyncio
async def test_connect_time_stream_unresolvable_host_is_a_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _nxdomain)
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    with pytest.raises(UpstreamTimeoutError) as exc_info:
        async with runner.stream(RunnerRequest(method="GET", url="https://gone.example.com/v1")):
            pass
    assert exc_info.value.type == "upstream_connect_failed"
    assert seen == []


# --- the resilience envelope ---------------------------------------------------------


class _RefusingRunner:
    """A streaming runner whose every call is refused by the egress policy."""

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> Any:
        return MagicMock()

    def _refuse(self) -> UpstreamUrlNotAllowedError:
        self.calls += 1
        return upstream_url_not_allowed(PrivateNameNotAllowedError(_HOST), authorized=True)

    async def run(self, request: RunnerRequest) -> RunnerResult:
        raise self._refuse()

    @asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        raise self._refuse()
        yield  # pragma: no cover - an async generator body


def _breaker() -> CircuitBreaker:
    return CircuitBreaker(
        MemoryStateBackend(), failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15
    )


@pytest.mark.asyncio
async def test_egress_refusal_is_not_retried_even_for_idempotent_method() -> None:
    inner = _RefusingRunner()
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)
    with pytest.raises(UpstreamUrlNotAllowedError):
        await runner.run(_REQUEST)
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_egress_refusal_does_not_count_against_the_circuit() -> None:
    inner = _RefusingRunner()
    runner = CircuitBreakerRunner(inner, _breaker(), enforcement_mode="blocking")
    for _ in range(4):
        with pytest.raises(UpstreamUrlNotAllowedError):
            await runner.run(_REQUEST)
    # Never shed with CircuitOpenError: a policy refusal says nothing about upstream health.
    assert inner.calls == 4


@pytest.mark.asyncio
async def test_streaming_egress_refusal_does_not_count_against_the_circuit() -> None:
    inner = _RefusingRunner()
    runner = CircuitBreakerRunner(inner, _breaker(), enforcement_mode="blocking")
    for _ in range(4):
        with pytest.raises(UpstreamUrlNotAllowedError):
            async with runner.stream(_REQUEST):
                pass
    assert inner.calls == 4


# --- the queued path (the async execution worker) ------------------------------------


class _NeverCalledExecutor:
    called = False

    async def execute(self, request: UpstreamExecRequest, *, session: Any) -> UpstreamExecResult:
        self.called = True
        raise AssertionError("a refused upstream URL must not be dispatched")


class _Authorizer:
    def __init__(self, verdict: QueuedExecutionVerdict) -> None:
        self._verdict = verdict
        self.calls = 0

    async def authorize(self, request: QueuedExecutionRequest) -> QueuedExecutionVerdict:
        self.calls += 1
        return self._verdict


async def _run_queued(
    egress: EgressConfig, authorizer: _Authorizer | None = None
) -> tuple[dict[str, Any], _NeverCalledExecutor]:
    executor = _NeverCalledExecutor()
    handler = ExecutionHandler(executor=executor, egress=egress, execution_authorizer=authorizer)
    result = await handler.execute(
        "job_egress",
        object(),
        payload={"upstream_url": f"https://{_HOST}/v1/invoices", "method": "GET"},
        created_by="agnt_1",
        actor_type="agent",
    )
    return result.body, executor


@pytest.mark.asyncio
async def test_queued_private_name_fails_terminally_with_the_sync_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Returning a failed result completes the job: the worker never requeues a
    # deterministic refusal or logs it as a job_failed traceback.
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    authorizer = _Authorizer(QueuedExecutionVerdict(allowed=True))
    with structlog.testing.capture_logs() as logs:
        body, executor = await _run_queued(_SUBNET_ONLY, authorizer)
    assert executor.called is False
    assert authorizer.calls == 1
    assert body["status"] == "failed"
    assert body["http_status"] == 400
    expected = upstream_url_not_allowed(PrivateNameNotAllowedError(_HOST), authorized=True)
    assert body["problem"] == broker_error_problem(expected)
    [entry] = _refusal_logs(logs)
    assert entry["stage"] == "queued"
    assert entry["host"] == _HOST


@pytest.mark.asyncio
async def test_queued_private_name_denied_job_reports_the_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    denial = {"type": "action_denied", "status": 403, "title": "denied", "detail": "denied"}
    body, executor = await _run_queued(
        _SUBNET_ONLY, _Authorizer(QueuedExecutionVerdict(allowed=False, problem=denial))
    )
    assert executor.called is False
    assert body["problem"] == denial


@pytest.mark.asyncio
async def test_queued_blocked_address_fails_terminally_with_generic_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("169.254.169.254"))
    body, executor = await _run_queued(_SUBNET_ONLY)
    assert executor.called is False
    assert body["problem"]["type"] == "invalid_upstream_url"
    assert body["problem"]["detail"] == BLOCKED_RANGE_MESSAGE


@pytest.mark.parametrize("authorized", [True, False])
def test_worker_problem_matches_the_broker_handler_body(authorized: bool) -> None:
    exc = PrivateNameNotAllowedError(_HOST)
    assert egress_refusal_problem(egress_refusal(exc, authorized=authorized)) == (
        broker_error_problem(upstream_url_not_allowed(exc, authorized=authorized))
    )


# --- CLI / MCP hint parity ----------------------------------------------------------


def test_mcp_egress_hint_matches_the_go_twin() -> None:
    # The Go brokerEgressAllowlistHint is tested against the same file.
    want = _HINT_FIXTURE.read_text("utf-8").rstrip("\n")
    assert want == mcp_execute._BROKER_EGRESS_ALLOWLIST_HINT
