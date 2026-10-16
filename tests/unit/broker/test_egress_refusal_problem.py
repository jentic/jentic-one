"""Broker problem+json for egress-policy refusals (#1722).

A DNS name that resolves into ``broker.egress.allowed_private_subnets`` but
matches no ``broker.egress.allowed_internal_domains`` suffix is refused with its
own ``private_name_not_in_allowed_domains`` type, a detail naming the allowlist
that lacks the entry, and a ``prompt_human`` directive naming the config key —
on both the pre-flight check (``_handle``) and the connect-time DNS-pinning
transport (``HttpRunner``). Every other refusal keeps ``invalid_upstream_url``.
Neither shape ever carries the resolved internal address.
"""

from __future__ import annotations

import json
import socket
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult
from jentic_one.broker.adapters.runners.circuit import CircuitBreakerRunner
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner
from jentic_one.broker.core.exceptions import (
    OperationNotFoundError,
    UpstreamUrlNotAllowedError,
    upstream_url_not_allowed,
)
from jentic_one.broker.core.problem import broker_error_problem
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.egress import DnsPinningTransport
from jentic_one.shared.resilience import CircuitBreaker
from jentic_one.shared.state.backend import MemoryStateBackend
from jentic_one.shared.url_validation import EgressPolicyError, PrivateNameNotAllowedError

_HOST = "billing.svc.cluster.local"
_INTERNAL_IP = "10.50.2.10"
_SUBNET_ONLY = EgressConfig(allowed_private_subnets=["10.50.0.0/16"])


def _resolving_to(*ips: str) -> Any:
    def _inner(_host: str, *_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0)) for ip in ips]

    return _inner


def _assert_private_name_problem(exc: UpstreamUrlNotAllowedError) -> None:
    body = broker_error_problem(exc)
    assert body["status"] == 400
    assert body["type"] == "private_name_not_in_allowed_domains"
    assert body["error_origin"] == "broker"
    assert _HOST in body["detail"]
    assert "broker.egress.allowed_internal_domains" in body["detail"]
    directive = body["agent_directive"]
    assert directive["strategy"] == "prompt_human"
    assert directive["parameters"] == {
        "host": _HOST,
        "config_key": "broker.egress.allowed_internal_domains",
        "env_var": "JENTIC__BROKER__EGRESS__ALLOWED_INTERNAL_DOMAINS",
    }
    assert "broker.egress.allowed_internal_domains" in directive["human_readable_instruction"]
    # The resolved internal address and the allowlisted CIDR never reach the caller.
    rendered = json.dumps(body)
    assert _INTERNAL_IP not in rendered
    assert "10.50.0.0" not in rendered


# --- the mapping ------------------------------------------------------------------


def test_private_name_refusal_maps_to_distinct_problem() -> None:
    _assert_private_name_problem(upstream_url_not_allowed(PrivateNameNotAllowedError(_HOST)))


@pytest.mark.parametrize(
    "exc",
    [
        EgressPolicyError("upstream URL resolves to a blocked address range"),
        ValueError("unsupported scheme: ftp"),
    ],
)
def test_other_refusals_keep_invalid_upstream_url(exc: ValueError) -> None:
    body = broker_error_problem(upstream_url_not_allowed(exc))
    assert body["status"] == 400
    assert body["type"] == "invalid_upstream_url"
    assert body["detail"] == str(exc)
    assert "agent_directive" not in body


# --- pre-flight (the execute route) -------------------------------------------------


class _MissResolver:
    """A registry resolver that serves no operation (the route then answers 404)."""

    async def resolve_operation(self, **_kwargs: Any) -> None:
        return None


def _request_for(host: str) -> Request:
    app = FastAPI()
    app.state.broker_registry_resolver = _MissResolver()
    path = f"/{host}/api"
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "app": app,
    }
    return Request(scope)


async def _run_handle(host: str, egress: EgressConfig) -> None:
    ctx = MagicMock()
    ctx.config.broker.egress = egress
    await _handle(
        _request_for(host),
        "GET",
        ctx,
        Identity(sub="agnt_test", permissions=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        None,
    )


@pytest.mark.asyncio
async def test_preflight_private_name_outside_domains_gets_distinct_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    with pytest.raises(UpstreamUrlNotAllowedError) as exc_info:
        await _run_handle(_HOST, _SUBNET_ONLY)
    _assert_private_name_problem(exc_info.value)


@pytest.mark.asyncio
async def test_preflight_private_ip_outside_subnets_keeps_generic_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("192.168.1.5"))
    with pytest.raises(UpstreamUrlNotAllowedError) as exc_info:
        await _run_handle(_HOST, _SUBNET_ONLY)
    assert exc_info.value.type == "invalid_upstream_url"
    assert exc_info.value.directive is None


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
        await _run_handle(_HOST, egress)


# --- connect time (the DNS-pinning transport under HttpRunner) ----------------------


def _pinned_runner(egress: EgressConfig) -> tuple[HttpRunner, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204, stream=httpx.ByteStream(b""))

    transport = DnsPinningTransport(httpx.MockTransport(_handler), egress)
    return HttpRunner(httpx.AsyncClient(transport=transport)), seen


@pytest.mark.asyncio
async def test_connect_time_private_name_outside_domains_gets_distinct_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    with pytest.raises(UpstreamUrlNotAllowedError) as exc_info:
        await runner.run(RunnerRequest(method="GET", url=f"https://{_HOST}/api"))
    _assert_private_name_problem(exc_info.value)
    assert seen == []


@pytest.mark.asyncio
async def test_connect_time_stream_private_name_outside_domains_gets_distinct_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(_INTERNAL_IP))
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    with pytest.raises(UpstreamUrlNotAllowedError) as exc_info:
        async with runner.stream(RunnerRequest(method="GET", url=f"https://{_HOST}/api")):
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
        await runner.run(RunnerRequest(method="GET", url=f"https://{_HOST}/api"))
    assert exc_info.value.type == "invalid_upstream_url"
    assert exc_info.value.detail == "upstream URL resolves to a blocked address range"
    assert seen == []


@pytest.mark.asyncio
async def test_connect_time_public_host_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("93.184.216.34"))
    runner, seen = _pinned_runner(_SUBNET_ONLY)
    result = await runner.run(RunnerRequest(method="GET", url="https://api.example.com/v1"))
    assert result.status_code == 204
    assert len(seen) == 1


# --- the resilience envelope ---------------------------------------------------------


class _RefusingRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, request: RunnerRequest) -> RunnerResult:
        self.calls += 1
        raise upstream_url_not_allowed(PrivateNameNotAllowedError(_HOST))


@pytest.mark.asyncio
async def test_egress_refusal_is_not_retried_even_for_idempotent_method() -> None:
    inner = _RefusingRunner()
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)
    with pytest.raises(UpstreamUrlNotAllowedError):
        await runner.run(RunnerRequest(method="GET", url=f"https://{_HOST}/api"))
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_egress_refusal_does_not_count_against_the_circuit() -> None:
    breaker = CircuitBreaker(
        MemoryStateBackend(), failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15
    )
    inner = _RefusingRunner()
    runner = CircuitBreakerRunner(inner, breaker, enforcement_mode="blocking")
    for _ in range(4):
        with pytest.raises(UpstreamUrlNotAllowedError):
            await runner.run(RunnerRequest(method="GET", url=f"https://{_HOST}/api"))
    # Never shed with CircuitOpenError: a policy refusal says nothing about upstream health.
    assert inner.calls == 4
