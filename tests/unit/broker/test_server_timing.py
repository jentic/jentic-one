"""Unit tests for the broker's opt-in ``Server-Timing`` header (#1717).

Covers the request-scoped recorder (format, ordering, accumulation), the
middleware's emission rules (authenticated callers only, upstream
``Server-Timing`` kept verbatim with the broker's field appended, streaming
responses carrying only pre-body stages), the config gate on the broker app,
and the disabled path's cost.

``run_execution``'s own stages (``upstream`` and ``persist``, on both the
completed and the ``BrokerError`` path, and inert with no recorder as on the
async worker) are covered here against a timed transaction stand-in.

The full execute path (real discovery → authorization → credential injection →
pipeline → persistence) is covered in
``tests/integration/broker/test_server_timing.py``.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import UpstreamTimeoutError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.core.server_timing import (
    ServerTimingRecorder,
    ServerTimingStage,
    activate,
    current_recorder,
    deactivate,
    stage_end,
    stage_start,
)
from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import DualTokenValidator, JwtTokenValidator, JwtVerifier
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.broker.web.deps import RequireExecuteAccess
from jentic_one.broker.web.server_timing import ServerTimingMiddleware
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import OperationInfo
from tests.unit.fake_tx import SessionScope

_JWT_SECRET = "server-timing-test-secret-32-bytes-long!"  # pragma: allowlist secret
_UPSTREAM_TIMING = 'db;dur=12.5, cache;desc="hit"'
_ENTRY_RE = re.compile(r"^(?P<name>[a-z]+);dur=(?P<dur>\d+\.\d)$")


def _parse(value: str) -> dict[str, float]:
    """Parse the broker's own field into ``{stage: duration_ms}`` (strict format)."""
    out: dict[str, float] = {}
    for entry in value.split(", "):
        match = _ENTRY_RE.fullmatch(entry)
        assert match is not None, f"malformed Server-Timing entry: {entry!r}"
        out[match["name"]] = float(match["dur"])
    return out


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


def test_header_value_orders_stages_and_formats_milliseconds() -> None:
    recorder = ServerTimingRecorder()
    recorder.add(ServerTimingStage.UPSTREAM, 20.04)
    recorder.add(ServerTimingStage.AUTH, 1.25)
    recorder.add(ServerTimingStage.DISCOVER, 0.8)

    assert recorder.header_value() == "auth;dur=1.2, discover;dur=0.8, upstream;dur=20.0"


def test_repeated_stage_accumulates() -> None:
    recorder = ServerTimingRecorder()
    recorder.add(ServerTimingStage.DISCOVER, 1.0)
    recorder.add(ServerTimingStage.DISCOVER, 2.5)
    assert recorder.duration_ms(ServerTimingStage.DISCOVER) == pytest.approx(3.5)
    assert recorder.duration_ms(ServerTimingStage.RULES) == 0.0


def test_stage_hooks_record_into_the_active_recorder() -> None:
    recorder = ServerTimingRecorder()
    token = activate(recorder)
    try:
        assert current_recorder() is recorder
        started = stage_start()
        assert started is not None
        stage_end(ServerTimingStage.CRED, started)
    finally:
        deactivate(token)

    assert recorder.has(ServerTimingStage.CRED)
    assert recorder.duration_ms(ServerTimingStage.CRED) >= 0.0
    assert current_recorder() is None


def test_hooks_are_inert_without_a_recorder() -> None:
    assert current_recorder() is None
    assert stage_start() is None
    stage_end(ServerTimingStage.AUTH, None)
    stage_end(ServerTimingStage.AUTH, time.perf_counter())  # no recorder → still a no-op
    assert current_recorder() is None


def test_disabled_hooks_cost_is_negligible() -> None:
    """With Server-Timing off every hook is one context-variable read.

    The bound is deliberately loose (≈ 100x headroom on a laptop) so it only
    trips if the disabled path grows real work — an allocation per call, a
    config lookup, a lock.
    """
    iterations = 100_000
    start = time.perf_counter()
    for _ in range(iterations):
        stage_end(ServerTimingStage.DISCOVER, stage_start())
    per_pair_us = (time.perf_counter() - start) / iterations * 1e6
    assert per_pair_us < 5.0, f"disabled stage hooks cost {per_pair_us:.2f}µs per pair"


# ---------------------------------------------------------------------------
# Middleware (real auth dependency, stubbed token resolver)
# ---------------------------------------------------------------------------


def _identity(permissions: list[str] | None = None) -> Identity:
    return Identity(
        sub="agnt_timing",
        actor_type=ActorType.AGENT,
        permissions=permissions if permissions is not None else [BROKER_EXECUTE_PERMISSION],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=True,
    )


def _app(*, enabled: bool, identity: Identity | None = None) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/buffered")
    async def buffered(_identity: RequireExecuteAccess) -> Response:
        started = stage_start()
        stage_end(ServerTimingStage.UPSTREAM, started)
        return Response(content=b"ok", headers={"server-timing": _UPSTREAM_TIMING})

    @app.get("/plain")
    async def plain(_identity: RequireExecuteAccess) -> Response:
        return Response(content=b"ok")

    @app.get("/streaming")
    async def streaming(_identity: RequireExecuteAccess) -> StreamingResponse:
        stage_end(ServerTimingStage.UPSTREAM, stage_start())

        async def body() -> AsyncIterator[bytes]:
            yield b"chunk-1"
            # Recorded after the headers went out — must not reach the header.
            stage_end(ServerTimingStage.PERSIST, stage_start())
            yield b"chunk-2"

        return StreamingResponse(body(), headers={"server-timing": _UPSTREAM_TIMING})

    if enabled:
        app.add_middleware(ServerTimingMiddleware)

    resolver = AsyncMock()
    resolver.resolve_access_token = AsyncMock(
        return_value=identity if identity is not None else _identity()
    )
    app.state.broker_token_validator = DualTokenValidator(
        opaque=CachedTokenValidator(resolver=resolver, cache_ttl_seconds=60.0),
        jwt=JwtTokenValidator(verifier=JwtVerifier(secret=_JWT_SECRET)),
    )
    return TestClient(app, raise_server_exceptions=False)


_AUTH = {"Authorization": "Bearer at_valid"}


def test_disabled_emits_no_header_and_keeps_upstream_verbatim() -> None:
    resp = _app(enabled=False).get("/buffered", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.headers.get_list("server-timing") == [_UPSTREAM_TIMING]

    plain = _app(enabled=False).get("/plain", headers=_AUTH)
    assert "server-timing" not in plain.headers


def test_enabled_appends_broker_field_after_upstream_field() -> None:
    resp = _app(enabled=True).get("/buffered", headers=_AUTH)
    assert resp.status_code == 200

    fields = resp.headers.get_list("server-timing")
    assert len(fields) == 2
    assert fields[0] == _UPSTREAM_TIMING  # upstream's metrics, untouched, first
    stages = _parse(fields[1])
    assert list(stages) == ["auth", "upstream", "total"]
    assert all(dur >= 0.0 for dur in stages.values())
    assert stages["total"] >= stages["auth"]


def test_enabled_without_upstream_header_emits_single_field() -> None:
    resp = _app(enabled=True).get("/plain", headers=_AUTH)
    assert list(_parse(resp.headers["server-timing"])) == ["auth", "total"]


def test_streaming_carries_only_stages_known_before_headers() -> None:
    resp = _app(enabled=True).get("/streaming", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.content == b"chunk-1chunk-2"

    fields = resp.headers.get_list("server-timing")
    assert fields[0] == _UPSTREAM_TIMING
    stages = _parse(fields[1])
    assert list(stages) == ["auth", "upstream", "total"]
    assert "persist" not in stages


@pytest.mark.parametrize(
    ("headers", "identity", "expected_status"),
    [
        ({}, None, 401),
        (_AUTH, _identity(permissions=["read:only"]), 403),
    ],
    ids=["unauthenticated", "missing-execute-permission"],
)
def test_rejected_callers_get_no_timing(
    headers: dict[str, str], identity: Identity | None, expected_status: int
) -> None:
    """No timing oracle before the caller is authenticated and authorized."""
    resp = _app(enabled=True, identity=identity).get("/plain", headers=headers)
    assert resp.status_code == expected_status
    assert "server-timing" not in resp.headers


def test_infra_routes_get_no_timing() -> None:
    resp = _app(enabled=True).get("/health")
    assert resp.status_code == 200
    assert "server-timing" not in resp.headers


# ---------------------------------------------------------------------------
# Config gate on the real broker app
# ---------------------------------------------------------------------------


def _broker_app(sample_config_dict: dict[str, Any], *, server_timing: bool | None) -> FastAPI:
    if server_timing is not None:
        sample_config_dict.setdefault("broker", {})["server_timing"] = server_timing
    config = AppConfig.model_validate(sample_config_dict)
    ctx = Context(config, allowed_dbs=_expand_allowed_dbs(["broker"], config))
    return create_broker_app(ctx)


@pytest.mark.parametrize(
    ("server_timing", "installed"),
    [(None, False), (False, False), (True, True)],
    ids=["default", "off", "on"],
)
def test_middleware_installed_only_when_enabled(
    sample_config_dict: dict[str, Any], server_timing: bool | None, installed: bool
) -> None:
    app = _broker_app(sample_config_dict, server_timing=server_timing)
    names = [m.cls.__name__ for m in app.user_middleware]  # type: ignore[attr-defined]
    assert (ServerTimingMiddleware.__name__ in names) is installed


def test_middleware_sits_inside_admission_control(sample_config_dict: dict[str, Any]) -> None:
    """A load-shed 503 is answered before the recorder exists — no timing on it."""
    app = _broker_app(sample_config_dict, server_timing=True)
    names = [m.cls.__name__ for m in app.user_middleware]  # type: ignore[attr-defined]
    assert names.index("AdmissionControlMiddleware") < names.index("ServerTimingMiddleware")


# ---------------------------------------------------------------------------
# run_execution: ``persist`` timed around its own admin transaction
# ---------------------------------------------------------------------------

_UPSTREAM_S = 0.2
_PERSIST_S = 0.02


class _SlowRunner(UpstreamRunner):
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def run(self, request: RunnerRequest) -> RunnerResult:
        await asyncio.sleep(_UPSTREAM_S)
        if self._fail:
            raise UpstreamTimeoutError(detail="upstream timed out", type="upstream_timeout")
        return RunnerResult(
            status_code=200, headers={}, body=b"ok", content_type="text/plain", duration_ms=1
        )


class _TimedScope(SessionScope):
    """A transaction whose body takes ``_PERSIST_S`` — the write ``persist`` must time."""

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        async with super().transaction() as session:
            await asyncio.sleep(_PERSIST_S)
            yield session


def _exec_ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/things",
        method="GET",
        trace_id="a" * 32,
        toolkit_id="tk_test000000000000000000",
        operation=OperationInfo(id="getThing", path="/v1/things", method="GET"),
        api_vendor="example",
        api_name="api",
        api_version="1.0.0",
        prefer=None,
        pinned_revisions=None,
    )


async def _run(*, fail: bool) -> _TimedScope:
    session = AsyncMock()
    session.add = MagicMock()
    scope = _TimedScope(session)
    with (
        patch(
            "jentic_one.broker.services.execution.service.record_execution",
            new_callable=AsyncMock,
            return_value="exec_test",
        ),
        patch(
            "jentic_one.broker.services.execution.service._emit_execution_lifecycle",
            new_callable=AsyncMock,
        ),
    ):
        if fail:
            with pytest.raises(UpstreamTimeoutError):
                await _run_execution(scope, fail=True)
        else:
            await _run_execution(scope, fail=False)
    return scope


async def _run_execution(scope: _TimedScope, *, fail: bool) -> None:
    await run_execution(
        _exec_ctx_req(),
        body=None,
        headers=None,
        admin_db=scope,
        broker=default_broker(_SlowRunner(fail=fail)),
        actor_id="agt_timing",
        actor_type="agent",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True], ids=["completed", "broker-error"])
async def test_run_execution_times_persist_apart_from_upstream(fail: bool) -> None:
    """``persist`` is the admin transaction alone — on the completed path and on
    the ``BrokerError`` path that commits a FAILED record before re-raising."""
    recorder = ServerTimingRecorder()
    token = activate(recorder)
    try:
        scope = await _run(fail=fail)
    finally:
        deactivate(token)

    assert scope.opened == 1
    persist_ms = recorder.duration_ms(ServerTimingStage.PERSIST)
    upstream_ms = recorder.duration_ms(ServerTimingStage.UPSTREAM)
    assert persist_ms >= _PERSIST_S * 1000 * 0.9
    # Disjoint: the upstream wait is not folded into persist (nor vice versa).
    assert persist_ms < _UPSTREAM_S * 1000
    assert upstream_ms >= _UPSTREAM_S * 1000 * 0.9


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True], ids=["completed", "broker-error"])
async def test_run_execution_without_recorder_records_nothing(fail: bool) -> None:
    """The async worker runs ``run_execution`` with no recorder: the hooks are inert."""
    assert current_recorder() is None
    scope = await _run(fail=fail)
    assert scope.opened == 1
    assert current_recorder() is None
