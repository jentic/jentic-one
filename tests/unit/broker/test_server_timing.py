"""Unit tests for the broker's opt-in ``Server-Timing`` header (#1717).

Covers the request-scoped recorder (format, ordering, accumulation, the
``finally``-recording ``stage()`` handle), the middleware's emission rules
(authenticated callers only; names without durations on a broker error raised
before the upstream call; the upstream's ``Server-Timing`` forwarded with the
broker's ``jentic-`` field appended and upstream entries claiming that
namespace dropped; streaming responses carrying only pre-body stages), the
config gate on the broker app (including a real load-shed ``503``), and the
disabled path allocating nothing.

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

import httpx
import pytest
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.errors import UpstreamTimeoutError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.core.server_timing import (
    ServerTimingRecorder,
    ServerTimingStage,
    StageTimer,
    activate,
    current_recorder,
    deactivate,
    stage,
    stage_end,
    stage_start,
)
from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import DualTokenValidator, JwtTokenValidator, JwtVerifier
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.broker.web.deps import RequireExecuteAccess
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.server_timing import ServerTimingMiddleware
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.discovery.errors import OperationNotFoundError
from jentic_one.shared.models import ActorType
from jentic_one.shared.resilience.rate_limit import RateLimiter
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.state.backend import MemoryStateBackend
from tests.unit.fake_tx import SessionScope

_JWT_SECRET = "server-timing-test-secret-32-bytes-long!"  # pragma: allowlist secret
_UPSTREAM_TIMING = 'db;dur=12.5, cache;desc="hit"'
_ENTRY_RE = re.compile(r"^(?P<name>jentic-[a-z]+);dur=(?P<dur>\d+\.\d)$")


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

    assert recorder.header_value() == (
        "jentic-auth;dur=1.2, jentic-discover;dur=0.8, jentic-upstream;dur=20.0"
    )
    assert recorder.header_value(with_durations=False) == (
        "jentic-auth, jentic-discover, jentic-upstream"
    )


def test_every_stage_name_is_in_the_broker_namespace() -> None:
    assert all(stage.value.startswith("jentic-") for stage in ServerTimingStage)


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


def test_stage_records_a_stage_that_raises() -> None:
    """A failing stage is still reported, so a slow failure shows where time went."""
    recorder = ServerTimingRecorder()
    token = activate(recorder)
    try:
        with pytest.raises(RuntimeError), stage(ServerTimingStage.DISCOVER) as timer:
            raise RuntimeError("discovery failed")
    finally:
        deactivate(token)

    assert recorder.has(ServerTimingStage.DISCOVER)
    assert isinstance(timer, StageTimer)
    assert timer.duration_ms == pytest.approx(recorder.duration_ms(ServerTimingStage.DISCOVER))


@pytest.mark.asyncio
async def test_stage_as_async_context_manager_covers_the_whole_block() -> None:
    recorder = ServerTimingRecorder()
    token = activate(recorder)
    try:
        with pytest.raises(RuntimeError):
            async with stage(ServerTimingStage.PERSIST):
                await asyncio.sleep(0.01)
                raise RuntimeError("commit failed")
    finally:
        deactivate(token)

    assert recorder.duration_ms(ServerTimingStage.PERSIST) >= 10 * 0.9


def test_stage_end_returns_the_recorded_duration() -> None:
    recorder = ServerTimingRecorder()
    token = activate(recorder)
    try:
        duration = stage_end(ServerTimingStage.AUTH, stage_start())
    finally:
        deactivate(token)
    assert duration == recorder.duration_ms(ServerTimingStage.AUTH)
    assert recorder.elapsed_ms() >= duration


def test_hooks_are_inert_without_a_recorder() -> None:
    assert current_recorder() is None
    assert stage_start() is None
    assert stage_end(ServerTimingStage.AUTH, None) is None
    # No recorder → still a no-op even with a real start reading.
    assert stage_end(ServerTimingStage.AUTH, time.perf_counter()) is None
    with stage(ServerTimingStage.CRED) as timer:
        pass
    assert timer.duration_ms is None
    assert current_recorder() is None


def test_disabled_stage_allocates_nothing() -> None:
    """With no recorder every ``stage()`` is the same shared no-op handle.

    The disabled path is one context-variable read and no allocation — pinned
    structurally rather than by wall clock, so a loaded runner cannot flake it.
    """
    assert stage(ServerTimingStage.DISCOVER) is stage(ServerTimingStage.UPSTREAM)


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


def _app(
    *,
    enabled: bool,
    identity: Identity | None = None,
    rate_limiter: RateLimiter | None = None,
    emit_header: bool = True,
) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    install_broker_error_handlers(app)

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

    @app.get("/spoofing-upstream")
    async def spoofing_upstream(_identity: RequireExecuteAccess) -> Response:
        with stage(ServerTimingStage.UPSTREAM):
            pass
        return Response(content=b"ok", headers={"server-timing": _SPOOFING_UPSTREAM_TIMING})

    @app.get("/discover-miss")
    async def discover_miss(_identity: RequireExecuteAccess) -> Response:
        with stage(ServerTimingStage.DISCOVER):
            raise OperationNotFoundError(detail="Operation not found — unregistered upstream URL.")

    @app.get("/upstream-timeout")
    async def upstream_timeout(_identity: RequireExecuteAccess) -> Response:
        with stage(ServerTimingStage.UPSTREAM):
            raise UpstreamTimeoutError(detail="upstream timed out", type="upstream_timeout")

    @app.get("/recorder-elapsed")
    async def recorder_elapsed(_identity: RequireExecuteAccess) -> Response:
        recorder = current_recorder()
        assert recorder is not None
        return Response(content=str(recorder.has(ServerTimingStage.AUTH)).encode())

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
        app.add_middleware(ServerTimingMiddleware, emit_header=emit_header)
    if rate_limiter is not None:
        app.state.broker_rate_limiter = rate_limiter

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
_SPOOFING_UPSTREAM_TIMING = 'db;dur=12.5, jentic-total;dur=0.1, cache;desc="a, jentic-x"'


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
    assert list(stages) == ["jentic-auth", "jentic-upstream", "jentic-total"]
    assert all(dur >= 0.0 for dur in stages.values())
    assert stages["jentic-total"] >= stages["jentic-auth"]


def test_enabled_without_upstream_header_emits_single_field() -> None:
    resp = _app(enabled=True).get("/plain", headers=_AUTH)
    assert list(_parse(resp.headers["server-timing"])) == ["jentic-auth", "jentic-total"]


def test_upstream_entries_claiming_the_broker_namespace_are_dropped() -> None:
    """Every ``jentic-*`` metric a client sees is the broker's own.

    Other upstream entries survive byte-for-byte, including a quoted ``desc``
    whose text merely mentions the prefix.
    """
    resp = _app(enabled=True).get("/spoofing-upstream", headers=_AUTH)
    fields = resp.headers.get_list("server-timing")
    assert fields[0] == 'db;dur=12.5, cache;desc="a, jentic-x"'
    assert list(_parse(fields[1])) == ["jentic-auth", "jentic-upstream", "jentic-total"]


def test_broker_error_before_upstream_lists_stage_names_without_durations() -> None:
    """A 404 from discovery says where the request stopped, not how long it took."""
    resp = _app(enabled=True).get("/discover-miss", headers=_AUTH)
    assert resp.status_code == 404
    assert resp.headers["server-timing"] == "jentic-auth, jentic-discover, jentic-total"


def test_failure_after_the_upstream_call_carries_durations() -> None:
    """A transport failure (504) reached the upstream: the failing stage is timed."""
    resp = _app(enabled=True).get("/upstream-timeout", headers=_AUTH)
    assert resp.status_code == 504
    stages = _parse(resp.headers["server-timing"])
    assert list(stages) == ["jentic-auth", "jentic-upstream", "jentic-total"]


def test_emit_header_false_keeps_the_recorder_without_a_header() -> None:
    """The recorder is active for in-process consumers; nothing reaches the caller."""
    resp = _app(enabled=True, emit_header=False).get("/recorder-elapsed", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.content == b"True"
    assert "server-timing" not in resp.headers


def test_streaming_carries_only_stages_known_before_headers() -> None:
    resp = _app(enabled=True).get("/streaming", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.content == b"chunk-1chunk-2"

    fields = resp.headers.get_list("server-timing")
    assert fields[0] == _UPSTREAM_TIMING
    stages = _parse(fields[1])
    assert list(stages) == ["jentic-auth", "jentic-upstream", "jentic-total"]
    assert "jentic-persist" not in stages


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


def test_rate_limited_caller_gets_no_timing() -> None:
    """The 429 branch runs after the auth stage started; it must not record it."""
    limiter = RateLimiter(MemoryStateBackend(), default_rpm=1, burst=1)
    client = _app(enabled=True, rate_limiter=limiter)

    allowed = client.get("/plain", headers=_AUTH)
    assert allowed.status_code == 200
    assert "server-timing" in allowed.headers

    limited = client.get("/plain", headers=_AUTH)
    assert limited.status_code == 429
    assert "retry-after" in limited.headers
    assert "server-timing" not in limited.headers


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


@pytest.mark.asyncio
async def test_load_shed_503_carries_no_timing(sample_config_dict: dict[str, Any]) -> None:
    """The real broker app at capacity sheds before the recorder runs."""
    sample_config_dict.setdefault("broker", {}).setdefault("resilience", {})["max_in_flight"] = 0
    app = _broker_app(sample_config_dict, server_timing=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        resp = await client.get("/api.example.com/v1/things", headers=_AUTH)
    assert resp.status_code == 503
    assert resp.json()["type"] == "server_at_capacity"
    assert "server-timing" not in resp.headers


# ---------------------------------------------------------------------------
# run_execution: ``persist`` timed around its own admin transaction
# ---------------------------------------------------------------------------

_UPSTREAM_S = 0.08
_PERSIST_S = 0.01


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
