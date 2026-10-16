"""Integration test: the per-request timing recorder on the real execute route.

It feeds both the opt-in ``Server-Timing`` header (#1717) and the
``broker.execution.overhead_ms`` histogram (#1716). Drives the broker's
catch-all ``proxy`` route end-to-end — the real auth dependency, discovery,
authorization (credential selection against the control DB), credential
injection (real decryption), the execution pipeline, and the admin-DB
execution record — behind :class:`ServerTimingMiddleware`, against a real
local HTTP upstream over a socket that sends its own ``Server-Timing``.

Only the out-of-process / injected seams are stand-ins: the opaque-token
resolver, the registry resolver, the binding deriver, and the rule evaluator
(all app-state protocols the broker is wired with at startup).
"""

from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Response
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader
from sqlalchemy import delete, select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.circuit import CircuitBreakerRunner
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner
from jentic_one.broker.core.exceptions import UpstreamTimeoutError
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.server_timing import (
    ServerTimingRecorder,
    ServerTimingStage,
    current_recorder,
)
from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import DualTokenValidator, JwtTokenValidator, JwtVerifier
from jentic_one.broker.services.execution import overhead, service
from jentic_one.broker.web.deps import get_http_runner
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers import execute
from jentic_one.broker.web.server_timing import ServerTimingMiddleware
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.broker.execution import (
    ExecutionContext,
    ExecutionOutcome,
    RunnerRequest,
    RunnerResult,
    StreamingOutcome,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.shared.broker.protocols import (
    BoundCredential,
    CredentialDerivation,
    ResolveResult,
    RuleEvaluation,
    RuleVerdict,
)
from jentic_one.shared.broker.schemas import ExecuteRequestContext
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, ExecutionStatus, StoredCredentialType
from jentic_one.shared.resilience import CircuitBreaker
from jentic_one.shared.schemas import APIReference, OperationInfo
from jentic_one.shared.state.backend import MemoryStateBackend
from tests.harness.metrics import capture_histogram, histogram_points

pytestmark = pytest.mark.integration

_JWT_SECRET = "server-timing-integ-secret-32-bytes!!!"  # pragma: allowlist secret
_CRED_ID = "cred_server_timing_integ"
_AGENT = "agnt_server_timing_integ"
_VENDOR = "timing-example-com"
_UPSTREAM_TIMING = 'db;dur=12.5, cache;desc="hit"'
_UPSTREAM_BODY = b'{"ok":true}'
_ENTRY_RE = re.compile(r"^(?P<name>jentic-[a-z]+);dur=(?P<dur>\d+\.\d)$")


def _stages(value: str) -> dict[str, float]:
    """Parse the broker's field into ``{stage: duration_ms}`` (strict format, in order)."""
    out: dict[str, float] = {}
    for entry in value.split(", "):
        match = _ENTRY_RE.fullmatch(entry)
        assert match is not None, f"malformed broker Server-Timing entry: {entry!r}"
        out[match["name"]] = float(match["dur"])
    return out


def _stage_names(value: str) -> list[str]:
    return list(_stages(value))


def _assert_persist_is_sane(stages: dict[str, float]) -> None:
    """``jentic-persist`` is the admin write alone: positive, disjoint from upstream.

    Each value is rounded to 0.1 ms, so the sum check allows one rounding step
    per summed stage.
    """
    assert stages["jentic-persist"] > 0.0
    assert stages["jentic-upstream"] + stages["jentic-persist"] <= stages["jentic-total"] + 0.2


@dataclass
class _Upstream:
    port: int = 0
    request_heads: list[dict[str, str]] = field(default_factory=list)
    #: Per-request ``(status, delay before the response head)``, consumed in
    #: order; once empty every request gets an immediate ``200``.
    script: list[tuple[int, float]] = field(default_factory=list)


@pytest.fixture()
async def upstream() -> AsyncGenerator[_Upstream, None]:
    """A minimal HTTP/1.1 upstream that answers with its own ``Server-Timing``."""
    state = _Upstream()

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")[1:]
        state.request_heads.append(
            {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines if ln)}
        )
        status, delay_s = state.script.pop(0) if state.script else (200, 0.0)
        if delay_s:
            await asyncio.sleep(delay_s)
        writer.write(
            f"HTTP/1.1 {status} X\r\n".encode("latin-1")
            + b"Content-Type: application/json\r\n"
            + f"Server-Timing: {_UPSTREAM_TIMING}\r\n".encode("latin-1")
            + f"Content-Length: {len(_UPSTREAM_BODY)}\r\n".encode("latin-1")
            + b"Connection: close\r\n\r\n"
            + _UPSTREAM_BODY
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture()
def refused_port() -> int:
    """A loopback port with nothing listening — the upstream connect is refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
async def ctx(integration_config: AppConfig) -> AsyncGenerator[Context, None]:
    """Integration context whose egress policy admits the loopback upstream."""
    config = integration_config.model_copy(deep=True)
    config.broker.egress.allowed_private_subnets = ["127.0.0.0/8"]
    async with Context(config) as context:
        yield context


@pytest.fixture()
async def seeded_credential(ctx: Context) -> AsyncGenerator[None, None]:
    async def _cleanup() -> None:
        async with ctx.control_db.transaction() as session:
            await session.execute(
                delete(CustomerAPIKey).where(CustomerAPIKey.credential_id == _CRED_ID)
            )
            await session.execute(delete(Credential).where(Credential.id == _CRED_ID))
        async with ctx.admin_db.transaction() as session:
            await session.execute(delete(Event).where(Event.actor_id == _AGENT))
            await session.execute(delete(ExecutionRecord).where(ExecutionRecord.actor_id == _AGENT))

    await _cleanup()
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=_CRED_ID,
                type=StoredCredentialType.API_KEY,
                name="timing-key",
                api_vendor=_VENDOR,
                api_name="timing",
                api_version="1.0.0",
            )
        )
        session.add(
            CustomerAPIKey(
                id=f"key-{_CRED_ID}",
                credential_id=_CRED_ID,
                encrypted_key=ctx.encryption.encrypt("sk-timing"),  # pragma: allowlist secret
                location="header",
                field_name="X-Api-Key",
            )
        )
    yield
    await _cleanup()


class _Resolver:
    async def resolve_operation(
        self, *, method: str, url: str, revision_id: object = None
    ) -> ResolveResult:
        return ResolveResult(
            operation=OperationInfo(id="getThings", path="/v1/things", method="GET"),
            api=APIReference(vendor=_VENDOR, name="timing", version="1.0.0"),
            path_params={},
        )


class _Deriver:
    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        return CredentialDerivation(
            credentials=(BoundCredential(credential_id=_CRED_ID, rule_set_id=None),),
            agent_bound_any=True,
            api_served=True,
            identity_mismatch=None,
        )


class _AllowAll:
    async def evaluate(self, **_: object) -> RuleEvaluation:
        return RuleEvaluation(verdict=RuleVerdict.ALLOW, rules_loaded=1)


class _DenyAll:
    async def evaluate(self, **_: object) -> RuleEvaluation:
        return RuleEvaluation(verdict=RuleVerdict.DENY, rules_loaded=1)


class _BufferedOnly(UpstreamRunner):
    """Hides the runner's ``stream`` so the route takes the buffered path."""

    def __init__(self, inner: UpstreamRunner) -> None:
        self._inner = inner

    async def run(self, request: RunnerRequest) -> RunnerResult:
        return await self._inner.run(request)


class _CaptureRecorder:
    """Inner ASGI middleware keeping each request's recorder for later assertions,
    and the recorder's elapsed time when the response headers left."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        sink: list[ServerTimingRecorder],
        headers_at_ms: list[float],
    ) -> None:
        self._app = app
        self._sink = sink
        self._headers_at_ms = headers_at_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        recorder = current_recorder()
        if recorder is None:
            await self._app(scope, receive, send)
            return
        self._sink.append(recorder)

        async def stamp_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                self._headers_at_ms.append(recorder.elapsed_ms())
            await send(message)

        await self._app(scope, receive, stamp_headers)


def _app(
    ctx: Context,
    runner: UpstreamRunner,
    *,
    server_timing: bool,
    deny: bool = False,
    recorder_only: bool = False,
    recorders: list[ServerTimingRecorder] | None = None,
    headers_at_ms: list[float] | None = None,
    broker: Broker | None = None,
) -> FastAPI:
    """The execute router behind the recorder middleware.

    ``server_timing`` mounts it emitting the header; ``recorder_only`` mounts
    it with ``emit_header=False`` (how the broker runs it for metrics alone).
    """
    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    install_broker_error_handlers(app)
    app.include_router(execute.router)
    if recorders is not None:
        app.add_middleware(
            _CaptureRecorder,
            sink=recorders,
            headers_at_ms=headers_at_ms if headers_at_ms is not None else [],
        )
    if server_timing or recorder_only:
        app.add_middleware(ServerTimingMiddleware, emit_header=server_timing)
    if broker is not None:
        app.state.broker = broker
    app.state.ctx = ctx
    resolver = AsyncMock()
    resolver.resolve_access_token = AsyncMock(
        return_value=Identity(
            sub=_AGENT,
            actor_type=ActorType.AGENT,
            permissions=[BROKER_EXECUTE_PERMISSION],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            active=True,
        )
    )
    app.state.broker_token_validator = DualTokenValidator(
        opaque=CachedTokenValidator(resolver=resolver, cache_ttl_seconds=60.0),
        jwt=JwtTokenValidator(verifier=JwtVerifier(secret=_JWT_SECRET)),
    )
    app.state.broker_registry_resolver = _Resolver()
    app.state.broker_credential_deriver = _Deriver()
    app.state.broker_agent_rule_evaluator = _DenyAll() if deny else _AllowAll()
    app.dependency_overrides[get_http_runner] = lambda: runner
    return app


async def _execute(app: FastAPI, port: int) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        return await client.get(
            f"/http://127.0.0.1:{port}/v1/things",
            headers={"Authorization": "Bearer at_timing"},
        )


@pytest.mark.parametrize("streaming", [False, True], ids=["buffered", "streaming"])
async def test_execute_emits_per_stage_server_timing(
    ctx: Context, seeded_credential: None, upstream: _Upstream, streaming: bool
) -> None:
    async with httpx.AsyncClient() as upstream_client:
        http_runner = HttpRunner(upstream_client)
        runner: UpstreamRunner = http_runner if streaming else _BufferedOnly(http_runner)
        resp = await _execute(_app(ctx, runner, server_timing=True), upstream.port)

    assert resp.status_code == 200, resp.text
    assert resp.content == _UPSTREAM_BODY
    assert resp.headers[JenticHeader.CREDENTIAL_ID.value] == _CRED_ID
    # The credential really was injected (cred stage did real work).
    assert upstream.request_heads[0]["x-api-key"] == "sk-timing"  # pragma: allowlist secret

    fields = resp.headers.get_list("server-timing")
    assert len(fields) == 2, fields
    # The upstream's own field passes through verbatim, ahead of the broker's.
    assert fields[0] == _UPSTREAM_TIMING
    if streaming:
        # Headers leave before the body: persistence happens after, so it is absent.
        assert _stage_names(fields[1]) == [
            "jentic-auth",
            "jentic-egress",
            "jentic-discover",
            "jentic-rules",
            "jentic-body",
            "jentic-cred",
            "jentic-upstream",
            "jentic-total",
        ]
    else:
        assert _stage_names(fields[1]) == [
            "jentic-auth",
            "jentic-egress",
            "jentic-discover",
            "jentic-rules",
            "jentic-body",
            "jentic-cred",
            "jentic-upstream",
            "jentic-persist",
            "jentic-total",
        ]
        _assert_persist_is_sane(_stages(fields[1]))
    broker_field = fields[1]
    for leak in (str(upstream.port), "127.0.0.1", _CRED_ID, _AGENT, "sk-timing"):
        assert leak not in broker_field


async def test_execute_without_middleware_has_only_upstream_timing(
    ctx: Context, seeded_credential: None, upstream: _Upstream
) -> None:
    async with httpx.AsyncClient() as upstream_client:
        runner = _BufferedOnly(HttpRunner(upstream_client))
        resp = await _execute(_app(ctx, runner, server_timing=False), upstream.port)

    assert resp.status_code == 200, resp.text
    assert resp.headers.get_list("server-timing") == [_UPSTREAM_TIMING]


async def test_execute_failed_upstream_reports_persist_of_failed_record(
    ctx: Context, seeded_credential: None, refused_port: int
) -> None:
    """A ``BrokerError`` (connect refused → 502 ``upstream_connect_failed``) still
    commits a FAILED record, and the header times that write as
    ``jentic-persist`` alongside the failed ``jentic-upstream``."""
    async with httpx.AsyncClient() as upstream_client:
        runner = _BufferedOnly(HttpRunner(upstream_client))
        resp = await _execute(_app(ctx, runner, server_timing=True), refused_port)

    assert resp.status_code == 502, resp.text
    assert resp.json()["type"] == "upstream_connect_failed"
    fields = resp.headers.get_list("server-timing")
    assert len(fields) == 1, fields  # no upstream response, so no upstream field
    stages = _stages(fields[0])
    assert list(stages) == [
        "jentic-auth",
        "jentic-egress",
        "jentic-discover",
        "jentic-rules",
        "jentic-body",
        "jentic-cred",
        "jentic-upstream",
        "jentic-persist",
        "jentic-total",
    ]
    _assert_persist_is_sane(stages)
    assert str(refused_port) not in fields[0]

    async with ctx.admin_db.session() as session:
        statuses = (
            (
                await session.execute(
                    select(ExecutionRecord.status).where(ExecutionRecord.actor_id == _AGENT)
                )
            )
            .scalars()
            .all()
        )
    assert list(statuses) == [ExecutionStatus.FAILED]


async def test_execute_failed_upstream_without_middleware_has_no_timing(
    ctx: Context, seeded_credential: None, refused_port: int
) -> None:
    async with httpx.AsyncClient() as upstream_client:
        runner = _BufferedOnly(HttpRunner(upstream_client))
        resp = await _execute(_app(ctx, runner, server_timing=False), refused_port)

    assert resp.status_code == 502, resp.text
    assert "server-timing" not in resp.headers


async def test_circuit_open_503_carries_durations(
    ctx: Context, seeded_credential: None, upstream: _Upstream
) -> None:
    """The circuit-breaker check runs inside ``jentic-upstream``, so a
    circuit-open ``503`` reports durations although no upstream request left."""
    breaker = CircuitBreaker(
        MemoryStateBackend(), failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15
    )
    host = f"127.0.0.1:{upstream.port}"
    await breaker.record(host, ok=False)
    await breaker.record(host, ok=False)
    async with httpx.AsyncClient() as upstream_client:
        runner = CircuitBreakerRunner(_BufferedOnly(HttpRunner(upstream_client)), breaker)
        resp = await _execute(_app(ctx, runner, server_timing=True), upstream.port)

    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "circuit_open"
    assert upstream.request_heads == []
    fields = resp.headers.get_list("server-timing")
    assert len(fields) == 1, fields
    stages = _stages(fields[0])
    assert "jentic-upstream" in stages
    assert "jentic-total" in stages


async def test_rule_deny_lists_stage_names_without_durations(
    ctx: Context, seeded_credential: None, upstream: _Upstream
) -> None:
    """A broker 403 raised before the upstream call says where it stopped — the
    failing ``jentic-rules`` stage included — but carries no durations."""
    async with httpx.AsyncClient() as upstream_client:
        runner = _BufferedOnly(HttpRunner(upstream_client))
        resp = await _execute(_app(ctx, runner, server_timing=True, deny=True), upstream.port)

    assert resp.status_code == 403, resp.text
    assert resp.headers.get_list("server-timing") == [
        "jentic-auth, jentic-egress, jentic-discover, jentic-rules, jentic-total"
    ]
    assert upstream.request_heads == []


# ---------------------------------------------------------------------------
# broker.execution.overhead_ms — derived from the same recorder
# ---------------------------------------------------------------------------

_OVERHEAD = "broker.execution.overhead_ms"
_DURATION = "broker.execution.duration_ms"
_UPSTREAM_DELAY_S = 0.2
_BACKOFF_S = 0.25
#: Upper bound on the time between the route handing back its response and the
#: response headers being sent (the sample is taken in between).
_SEND_GAP_MS = 1.0
#: Broker-side stages recorded before the upstream call on both paths.
_PRE_UPSTREAM_STAGES = (
    ServerTimingStage.AUTH,
    ServerTimingStage.EGRESS,
    ServerTimingStage.DISCOVER,
    ServerTimingStage.RULES,
    ServerTimingStage.BODY,
    ServerTimingStage.CRED,
)


@pytest.fixture()
def overhead_reader() -> Iterator[InMemoryMetricReader]:
    with capture_histogram(overhead, "_execution_overhead", _OVERHEAD) as reader:
        yield reader


@pytest.fixture()
def duration_reader() -> Iterator[InMemoryMetricReader]:
    with capture_histogram(service, "_execution_duration", _DURATION) as reader:
        yield reader


def _single_point(reader: InMemoryMetricReader, name: str) -> HistogramDataPoint:
    (point,) = histogram_points(reader, name)
    assert point.count == 1
    return point


async def _statuses(ctx: Context) -> list[str]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            select(ExecutionRecord.status).where(ExecutionRecord.actor_id == _AGENT)
        )
        return list(rows.scalars().all())


class _FixedBackoff(RetryRunner):
    """The real retry loop with a deterministic backoff (no jitter)."""

    def _backoff(self, attempt: int, *, hint: int | None, deadline: float | None) -> float:
        return _BACKOFF_S


@pytest.mark.parametrize("streaming", [False, True], ids=["buffered", "streaming"])
async def test_overhead_excludes_upstream_and_covers_persistence(
    ctx: Context,
    seeded_credential: None,
    upstream: _Upstream,
    overhead_reader: InMemoryMetricReader,
    streaming: bool,
) -> None:
    """One sample per execution, read from the request's recorder with the header
    off. The sample is exactly the broker time outside ``jentic-upstream``: what
    precedes the upstream headers, plus (buffered) everything after the upstream
    call up to the headers, or (streaming) the persistence after the body."""
    upstream.script.append((200, _UPSTREAM_DELAY_S))
    recorders: list[ServerTimingRecorder] = []
    headers_at_ms: list[float] = []
    async with httpx.AsyncClient() as upstream_client:
        http_runner = HttpRunner(upstream_client)
        runner: UpstreamRunner = http_runner if streaming else _BufferedOnly(http_runner)
        app = _app(
            ctx,
            runner,
            server_timing=False,
            recorder_only=True,
            recorders=recorders,
            headers_at_ms=headers_at_ms,
        )
        resp = await _execute(app, upstream.port)

    assert resp.status_code == 200, resp.text
    assert resp.headers.get_list("server-timing") == [_UPSTREAM_TIMING]
    (recorder,) = recorders
    (headers_at,) = headers_at_ms
    point = _single_point(overhead_reader, _OVERHEAD)
    assert dict(point.attributes or {}) == {
        "operation": "getThings",
        "mode": "streaming" if streaming else "buffered",
    }
    upstream_ms = recorder.duration_ms(ServerTimingStage.UPSTREAM)
    persist_ms = recorder.duration_ms(ServerTimingStage.PERSIST)
    assert upstream_ms >= _UPSTREAM_DELAY_S * 1000
    assert persist_ms > 0.0  # the streaming write runs after the body, still recorded
    # Broker time up to the response headers; the sample is taken a little
    # before they leave (after the route returns, before the response is sent).
    to_headers_ms = headers_at - upstream_ms
    post_ms = persist_ms if streaming else 0.0
    assert to_headers_ms - _SEND_GAP_MS <= point.sum - post_ms <= to_headers_ms
    assert point.sum < _UPSTREAM_DELAY_S * 1000
    if not streaming:
        # The buffered record commits before the sample, so it is inside it.
        assert point.sum >= persist_ms + sum(recorder.duration_ms(s) for s in _PRE_UPSTREAM_STAGES)
    assert await _statuses(ctx) == [ExecutionStatus.COMPLETED]


async def test_retry_and_backoff_are_upstream_window_not_overhead_or_duration(
    ctx: Context,
    seeded_credential: None,
    upstream: _Upstream,
    overhead_reader: InMemoryMetricReader,
    duration_reader: InMemoryMetricReader,
) -> None:
    """A 503 → backoff → 200 retry: ``duration_ms`` is the final attempt only,
    ``overhead_ms`` excludes the whole ``jentic-upstream`` window, so the retry
    attempt and the backoff sleep are in neither histogram."""
    upstream.script.append((503, 0.0))
    recorders: list[ServerTimingRecorder] = []
    async with httpx.AsyncClient() as upstream_client:
        runner = _BufferedOnly(_FixedBackoff(HttpRunner(upstream_client), max_attempts=2))
        app = _app(ctx, runner, server_timing=False, recorder_only=True, recorders=recorders)
        resp = await _execute(app, upstream.port)

    assert resp.status_code == 200, resp.text
    assert len(upstream.request_heads) == 2
    (recorder,) = recorders
    upstream_window_ms = recorder.duration_ms(ServerTimingStage.UPSTREAM)
    duration = _single_point(duration_reader, _DURATION)
    overhead_point = _single_point(overhead_reader, _OVERHEAD)
    assert upstream_window_ms >= _BACKOFF_S * 1000
    assert duration.sum < _BACKOFF_S * 1000
    assert overhead_point.sum < _BACKOFF_S * 1000
    assert upstream_window_ms - duration.sum >= _BACKOFF_S * 1000 - 1  # in neither (int ms)


class _OpenFailureBroker:
    """A broker whose stream fails to open after delivering a FAILED outcome to the
    persistence callback first — the delivery shape of a stream-open failure."""

    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        raise AssertionError("buffered path not expected")

    async def execute_streaming(
        self,
        runner: StreamingUpstreamRunner,
        request: RunnerRequest,
        ctx_req: ExecuteRequestContext,
        execution_id: str,
        *,
        transfer_deadline_s: float,
        background_callback: Callable[[StreamingOutcome], Awaitable[None]] | None = None,
    ) -> Response:
        assert background_callback is not None
        await background_callback(
            StreamingOutcome(
                execution_id=execution_id, http_status=502, error="upstream_connect_failed"
            )
        )
        raise UpstreamTimeoutError(
            detail="The upstream connection could not be established in time.",
            type="upstream_connect_failed",
        )


@pytest.mark.parametrize("streaming", [False, True], ids=["buffered", "streaming-open"])
async def test_failed_upstream_records_no_overhead(
    ctx: Context,
    seeded_credential: None,
    refused_port: int,
    overhead_reader: InMemoryMetricReader,
    streaming: bool,
) -> None:
    """A call that never got an upstream response persists its FAILED record but
    emits no overhead sample, on both paths."""
    async with httpx.AsyncClient() as upstream_client:
        http_runner = HttpRunner(upstream_client)
        runner: UpstreamRunner = http_runner if streaming else _BufferedOnly(http_runner)
        app = _app(
            ctx,
            runner,
            server_timing=False,
            recorder_only=True,
            broker=_OpenFailureBroker() if streaming else None,
        )
        resp = await _execute(app, refused_port)

    assert resp.status_code == 504, resp.text
    assert resp.json()["type"] == "upstream_connect_failed"
    assert await _statuses(ctx) == [ExecutionStatus.FAILED]
    assert histogram_points(overhead_reader, _OVERHEAD) == []
