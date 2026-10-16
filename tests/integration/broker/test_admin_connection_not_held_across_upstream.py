"""Integration tests: no admin-DB connection is held across the upstream call (#1702).

The buffered sync path, the streaming path and the async worker all write the
execution record in an admin transaction opened only after the upstream call
returns or raises. These tests drive them against a real local HTTP upstream
over a real socket and the real admin DB, and observe the admin pool while the
upstream is still answering. On SQLite a transaction opens with ``BEGIN
IMMEDIATE``, so an admin transaction held across the call would also hold the
database write lock and block every other admin writer, including the
``credential.accessed`` audit write of a concurrent call.

They also pin the audit-before-use contract of ``CredentialService.inject``:
the ``credential.accessed`` event is committed before the upstream request is
sent, and a failed audit write fails the injection (nothing is sent).
"""

from __future__ import annotations

import asyncio
import secrets
import socket
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, event, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.execution.executor import PipelineExecutor
from jentic_one.broker.web.routers.execute import (
    _assemble_response,
    _execute_buffered,
    _handle_streaming,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import EgressConfig, WorkerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.protocols import QueuedExecutionRequest, QueuedExecutionVerdict
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import (
    ActorType,
    ExecutionStatus,
    JobKind,
    JobStatus,
    StoredCredentialType,
)
from jentic_one.shared.models.events import EventType
from jentic_one.shared.schemas import OperationInfo

pytestmark = pytest.mark.integration

_AGENT = "agnt_no_admin_hold"
_CREDENTIAL_ID = "cred_no_admin_hold"
_VENDOR = "no-admin-hold-vendor"
_API_NAME = "things"
_API_VERSION = "v1"
_IDENTITY = Identity(
    sub=_AGENT,
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)
#: How long the upstream waits for every concurrent call to arrive. Longer than
#: SQLite's 5 s busy timeout, so a write lock held across one call fails the
#: other calls' audit writes before they can reach the upstream.
_ARRIVAL_TIMEOUT_S = 8.0


@dataclass
class _AdminTransactions:
    count: int = 0


class _OpenSessions:
    """Wraps the admin session factory and counts the sessions currently open.

    A session holds no pooled connection on Postgres until its first statement,
    so the pool alone cannot show a transaction that is open but idle; this
    counts the sessions themselves (every ``session()``/``transaction()``).
    """

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self.open = 0

    def __call__(self) -> _OpenSessions._Tracked:
        return _OpenSessions._Tracked(self)

    class _Tracked:
        def __init__(self, owner: _OpenSessions) -> None:
            self._owner = owner
            self._cm = owner._factory()

        async def __aenter__(self) -> Any:
            session = await self._cm.__aenter__()
            self._owner.open += 1
            return session

        async def __aexit__(self, *exc: Any) -> Any:
            self._owner.open -= 1
            return await self._cm.__aexit__(*exc)


@dataclass
class _Observation:
    """What the admin DB looked like when a request reached the upstream."""

    api_key: str
    admin_checked_out: int
    admin_sessions_open: int
    committed_access_events: int
    other_admin_write: str


@dataclass
class _Upstream:
    """A real HTTP/1.1 upstream that answers once ``expected`` requests arrived."""

    admin_db: DatabaseSession
    trace_id: str
    expected: int = 1
    port: int = 0
    seen: list[_Observation] = field(default_factory=list)
    all_arrived: asyncio.Event = field(default_factory=asyncio.Event)
    #: Set by the test to subtract the upstream's own probe transactions.
    transactions: _AdminTransactions | None = None
    probe_transactions: int = 0
    sessions: _OpenSessions | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        api_key = ""
        for line in head.decode("latin-1").split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "x-api-key":
                api_key = value.strip()
        # Sample the pool before this handler's own queries check a connection out.
        checked_out = _checked_out(self.admin_db)
        sessions_open = self.sessions.open if self.sessions else 0
        before = self.transactions.count if self.transactions else 0
        committed = len(await _access_events(self.admin_db, self.trace_id))
        other_write = await _try_admin_write(self.admin_db)
        if self.transactions is not None:
            self.probe_transactions += self.transactions.count - before
        self.seen.append(_Observation(api_key, checked_out, sessions_open, committed, other_write))
        if len(self.seen) >= self.expected:
            self.all_arrived.set()
        try:
            async with asyncio.timeout(_ARRIVAL_TIMEOUT_S):
                await self.all_arrived.wait()
            status = "200 OK"
        except TimeoutError:
            status = "504 Gateway Timeout"
        body = b'{"ok": true}'
        writer.write(
            f"HTTP/1.1 {status}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode("latin-1")
            + body
        )
        await writer.drain()
        writer.close()


def _checked_out(admin_db: DatabaseSession) -> int:
    pool: Any = admin_db.engine.sync_engine.pool
    return int(pool.checkedout())


async def _try_admin_write(admin_db: DatabaseSession) -> str:
    """Open (and commit) an admin write transaction, bounded well under the busy timeout."""
    try:
        async with asyncio.timeout(2.0), admin_db.transaction() as session:
            await session.execute(select(Event.id).limit(1))
    except Exception as exc:
        return type(exc).__name__
    return "ok"


@pytest.fixture()
async def trace_id(admin_db: DatabaseSession) -> AsyncGenerator[str, None]:
    """A fresh trace id; this test's events and execution rows are removed after."""
    value = secrets.token_hex(16)
    yield value
    async with admin_db.transaction() as session:
        await session.execute(delete(Event).where(Event.trace_id == value))
        await session.execute(delete(ExecutionRecord).where(ExecutionRecord.trace_id == value))


@pytest.fixture()
def open_admin_sessions(admin_db: DatabaseSession) -> Iterator[_OpenSessions]:
    """Count open admin sessions for the test (the real factory, instrumented)."""
    original = admin_db.session_factory
    counter = _OpenSessions(original)
    admin_db._session_factory = counter  # type: ignore[assignment]
    try:
        yield counter
    finally:
        # A test that reconnects the DB installs a fresh factory; keep that one.
        if admin_db._session_factory is counter:  # type: ignore[comparison-overlap]
            admin_db._session_factory = original


@pytest.fixture()
async def upstream(
    admin_db: DatabaseSession, trace_id: str, open_admin_sessions: _OpenSessions
) -> AsyncGenerator[_Upstream, None]:
    state = _Upstream(admin_db=admin_db, trace_id=trace_id, sessions=open_admin_sessions)
    server = await asyncio.start_server(state.handle, "127.0.0.1", 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture()
def closed_port() -> int:
    """A local port with nothing listening (connection refused)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
async def seeded_credential(integration_context: Context) -> AsyncGenerator[str, None]:
    """Seed the stored API-key credential the calls inject; yield its secret."""
    secret = f"sk-{secrets.token_hex(8)}"  # pragma: allowlist secret

    async def _cleanup() -> None:
        async with integration_context.control_db.transaction() as session:
            await session.execute(
                delete(CustomerAPIKey).where(CustomerAPIKey.credential_id == _CREDENTIAL_ID)
            )
            await session.execute(delete(Credential).where(Credential.id == _CREDENTIAL_ID))

    await _cleanup()
    async with integration_context.control_db.transaction() as session:
        session.add(
            Credential(
                id=_CREDENTIAL_ID,
                type=StoredCredentialType.API_KEY,
                name="No admin hold key",
                api_vendor=_VENDOR,
                api_name=_API_NAME,
                api_version=_API_VERSION,
            )
        )
        session.add(
            CustomerAPIKey(
                id=f"key-{_CREDENTIAL_ID}",
                credential_id=_CREDENTIAL_ID,
                encrypted_key=integration_context.encryption.encrypt(secret),
                location="header",
                field_name="X-Api-Key",
            )
        )
    yield secret
    await _cleanup()


@contextmanager
def _count_admin_transactions(admin_db: DatabaseSession) -> Iterator[_AdminTransactions]:
    """Count admin-engine transactions (BEGIN) opened in the block."""
    counts = _AdminTransactions()
    engine = admin_db.engine.sync_engine

    def _on_begin(*_: Any) -> None:
        counts.count += 1

    event.listen(engine, "begin", _on_begin)
    try:
        yield counts
    finally:
        event.remove(engine, "begin", _on_begin)


def _ctx_req(upstream_url: str, trace_id: str, method: str) -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url=upstream_url,
        method=method,
        trace_id=trace_id,
        operation=OperationInfo(id="getThing", path="/thing", method="GET"),
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
    )


def _app(ctx: Context, runner: HttpRunner, upstream_url: str, trace_id: str) -> FastAPI:
    app = FastAPI()

    @app.get("/buffered")
    async def buffered(request: Request) -> Response:
        ctx_req = _ctx_req(upstream_url, trace_id, request.method)
        outcome = await _execute_buffered(request, ctx_req, ctx, _IDENTITY, runner, None)
        return _assemble_response(outcome, ctx_req)

    @app.get("/streaming")
    async def streaming(request: Request) -> Response:
        return await _handle_streaming(
            request,
            _ctx_req(upstream_url, trace_id, request.method),
            ctx,
            _IDENTITY,
            runner,
            ctx.config.broker.resilience.upstream,
        )

    return app


async def _call(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        return await client.get(path)


async def _access_events(admin_db: DatabaseSession, trace_id: str) -> list[Event]:
    async with admin_db.session() as session:
        rows = await session.execute(
            select(Event).where(
                Event.trace_id == trace_id, Event.type == EventType.CREDENTIAL_ACCESSED
            )
        )
        return list(rows.scalars())


async def _execution(admin_db: DatabaseSession, trace_id: str) -> ExecutionRecord | None:
    async with admin_db.session() as session:
        rows = await session.execute(
            select(ExecutionRecord).where(ExecutionRecord.trace_id == trace_id)
        )
        return rows.scalar_one_or_none()


def _assert_no_admin_hold(seen: list[_Observation], secret: str, *, sole_call: bool = True) -> None:
    for observation in seen:
        assert observation.api_key == secret, "the credential reached the upstream"
        if sole_call:
            # With concurrent calls another call's own audit write may be in flight.
            assert observation.admin_checked_out == 0, "an admin connection was held"
            assert observation.admin_sessions_open == 0, "an admin session was open"
        assert observation.other_admin_write == "ok", "another admin writer was blocked"
        # Audit-before-use: the access event was committed before the request went out.
        assert observation.committed_access_events >= 1


@pytest.mark.parametrize("path", ["/buffered", "/streaming"])
async def test_slow_upstream_holds_no_admin_connection(
    integration_context: Context,
    upstream: _Upstream,
    seeded_credential: str,
    trace_id: str,
    path: str,
) -> None:
    """While the upstream is answering, no admin connection is checked out, another
    admin writer is not blocked, and ``credential.accessed`` is already committed.

    The record is written afterwards: one transaction for the access audit and
    one for the execution record (the streaming record is written once the body
    has been sent).
    """
    admin_db = integration_context.admin_db
    async with httpx.AsyncClient() as upstream_client:
        app = _app(
            integration_context,
            HttpRunner(upstream_client),
            f"http://127.0.0.1:{upstream.port}/thing",
            trace_id,
        )
        with _count_admin_transactions(admin_db) as transactions:
            upstream.transactions = transactions
            resp = await _call(app, path)

    assert resp.status_code == 200
    assert len(upstream.seen) == 1
    _assert_no_admin_hold(upstream.seen, seeded_credential)
    assert upstream.seen[0].committed_access_events == 1
    assert transactions.count - upstream.probe_transactions == 2, transactions

    record = await _execution(admin_db, trace_id)
    assert record is not None and record.status == ExecutionStatus.COMPLETED
    assert record.credential_id == _CREDENTIAL_ID
    assert len(await _access_events(admin_db, trace_id)) == 1


async def test_concurrent_slow_calls_all_reach_the_upstream(
    integration_context: Context,
    upstream: _Upstream,
    seeded_credential: str,
    trace_id: str,
) -> None:
    """Concurrent buffered calls are all in flight at the upstream at once.

    The upstream answers only after every call arrived. If a call held an admin
    transaction across its upstream wait, the next call's audit write could not
    commit (on SQLite the write lock is taken) and it would never be sent.
    """
    upstream.expected = 3
    admin_db = integration_context.admin_db
    async with httpx.AsyncClient() as upstream_client:
        app = _app(
            integration_context,
            HttpRunner(upstream_client),
            f"http://127.0.0.1:{upstream.port}/thing",
            trace_id,
        )
        responses = await asyncio.gather(*(_call(app, "/buffered") for _ in range(3)))

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert len(upstream.seen) == 3
    _assert_no_admin_hold(upstream.seen, seeded_credential, sole_call=False)
    assert len(await _access_events(admin_db, trace_id)) == 3


async def test_upstream_failure_persists_failed_record(
    integration_context: Context,
    closed_port: int,
    seeded_credential: str,
    trace_id: str,
) -> None:
    """A BrokerError (connection refused) still commits the FAILED record and its
    event after the call, and the access event committed before it stands."""
    admin_db = integration_context.admin_db
    async with httpx.AsyncClient() as upstream_client:
        app = _app(
            integration_context,
            HttpRunner(upstream_client),
            f"http://127.0.0.1:{closed_port}/thing",
            trace_id,
        )
        resp = await _call(app, "/buffered")

    assert resp.status_code >= 500
    record = await _execution(admin_db, trace_id)
    assert record is not None and record.status == ExecutionStatus.FAILED
    assert record.credential_id == _CREDENTIAL_ID
    assert len(await _access_events(admin_db, trace_id)) == 1
    async with admin_db.session() as session:
        failed = await session.execute(
            select(Event).where(
                Event.trace_id == trace_id, Event.type == EventType.EXECUTION_FAILED
            )
        )
        assert len(list(failed.scalars())) == 1
    assert _checked_out(admin_db) == 0


async def test_failed_access_audit_fails_the_injection(
    integration_context: Context,
    upstream: _Upstream,
    seeded_credential: str,
    trace_id: str,
) -> None:
    """With the admin DB unavailable the access event cannot be committed, so the
    injection fails and the credential is never sent upstream."""
    admin_db = integration_context.admin_db
    async with httpx.AsyncClient() as upstream_client:
        app = _app(
            integration_context,
            HttpRunner(upstream_client),
            f"http://127.0.0.1:{upstream.port}/thing",
            trace_id,
        )
        await admin_db.close()
        try:
            resp = await _call(app, "/buffered")
        finally:
            await admin_db.connect()

    assert resp.status_code == 500
    assert upstream.seen == [], "the credential was used without a committed audit event"
    assert await _access_events(admin_db, trace_id) == []
    assert await _execution(admin_db, trace_id) is None


class _AllowBoundCredential:
    """The run-time re-authorizer, allowing the seeded credential as the boundary."""

    async def authorize(self, request: QueuedExecutionRequest) -> QueuedExecutionVerdict:
        return QueuedExecutionVerdict(allowed=True, allowed_credential_ids=(_CREDENTIAL_ID,))


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _clean() -> None:
        async with admin_db.transaction() as session:
            await session.execute(delete(JobResult))
            await session.execute(delete(Job))

    await _clean()
    yield
    await _clean()


async def test_queued_execution_holds_no_admin_connection(
    integration_context: Context,
    upstream: _Upstream,
    seeded_credential: str,
    trace_id: str,
    clean_jobs: None,
) -> None:
    """The worker runs a credentialed job with no admin transaction open around the
    handler: the injection's audit write commits, no admin connection is held
    while the upstream answers, and the record + job result are written after."""
    ctx = integration_context
    admin_db = ctx.admin_db
    async with admin_db.transaction() as session:
        job = Job(
            kind=JobKind.EXECUTION,
            status=JobStatus.QUEUED,
            created_by=_AGENT,
            actor_type=ActorType.AGENT.value,
            payload={
                "execution_id": f"exec_{secrets.token_hex(8)}",
                "upstream_url": f"http://127.0.0.1:{upstream.port}/thing",
                "method": "GET",
                "trace_id": trace_id,
                "api_vendor": _VENDOR,
                "api_name": _API_NAME,
                "api_version": _API_VERSION,
                "operation": {"id": "getThing", "path": "/thing", "method": "GET"},
            },
        )
        session.add(job)
        await session.flush()
        job_id = job.id

    async with httpx.AsyncClient() as upstream_client:
        registry = RunnerRegistry()
        registry.register(["http", "https"], HttpRunner(upstream_client), required=True)
        handler = ExecutionHandler(
            executor=PipelineExecutor(registry),
            credential_injector=CredentialService(ctx),
            execution_authorizer=_AllowBoundCredential(),
            # The test upstream listens on loopback, which the default policy blocks.
            egress=EgressConfig(allowed_private_subnets=["127.0.0.0/8"]),
        )
        handlers = JobHandlerRegistry()
        handlers.register(JobKind.EXECUTION, handler)
        worker = WorkerLoop(admin_db, handlers, worker_config=WorkerConfig())
        assert await worker._tick() is True

    assert len(upstream.seen) == 1
    _assert_no_admin_hold(upstream.seen, seeded_credential)
    async with admin_db.session() as session:
        stored = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        result = (
            await session.execute(select(JobResult).where(JobResult.job_id == job_id))
        ).scalar_one()
    assert stored.status == JobStatus.COMPLETED
    assert result.body["http_status"] == 200
    record = await _execution(admin_db, trace_id)
    assert record is not None and record.status == ExecutionStatus.COMPLETED
    assert record.credential_id == _CREDENTIAL_ID
    assert len(await _access_events(admin_db, trace_id)) == 1
