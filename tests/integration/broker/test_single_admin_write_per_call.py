"""Integration tests: one admin write session per sync broker call (#1702).

The ``credential.accessed`` audit event is deferred into the execution record's
transaction, so a successful call costs one admin-DB transaction instead of
two. These tests drive the router's buffered and streaming handlers against a
real local HTTP upstream over a real socket and the real admin DB, counting
admin-engine transactions, and pin that the audit event is still written on
every failure path (upstream connect failure, a mid-stream upstream failure,
and a raise inside the rolled-back persistence transaction).
"""

from __future__ import annotations

import asyncio
import secrets
import socket
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, event, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import (
    _assemble_response,
    _execute_buffered,
    _handle_streaming,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.events.credential_access import PendingCredentialAccess
from jentic_one.shared.models import ActorType, ExecutionStatus, StoredCredentialType
from jentic_one.shared.models.events import EventType
from jentic_one.shared.schemas import OperationInfo

pytestmark = pytest.mark.integration

_AGENT = "agnt_single_admin_write"
_CREDENTIAL_ID = "cred_single_admin_write"
_VENDOR = "single-write-vendor"
_API_NAME = "things"
_API_VERSION = "v1"
_IDENTITY = Identity(
    sub=_AGENT,
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)


@dataclass
class _Upstream:
    port: int
    seen_api_keys: list[str]


def _serve(*, truncate_body: bool) -> Any:
    """A minimal HTTP/1.1 upstream; ``truncate_body`` closes mid-body."""

    async def _handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, seen: list[str]
    ) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        for line in head.decode("latin-1").split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "x-api-key":
                seen.append(value.strip())
        body = b'{"ok": true}'
        declared = len(body) + (64 if truncate_body else 0)
        writer.write(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {declared}\r\n"
            "Connection: close\r\n\r\n".encode("latin-1")
            + body
        )
        await writer.drain()
        writer.close()

    return _handler


async def _start_upstream(*, truncate_body: bool = False) -> tuple[asyncio.Server, _Upstream]:
    seen: list[str] = []
    handler = _serve(truncate_body=truncate_body)
    server = await asyncio.start_server(lambda r, w: handler(r, w, seen), "127.0.0.1", 0)
    return server, _Upstream(port=server.sockets[0].getsockname()[1], seen_api_keys=seen)


@pytest.fixture()
async def upstream() -> AsyncGenerator[_Upstream, None]:
    server, state = await _start_upstream()
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture()
async def truncating_upstream() -> AsyncGenerator[_Upstream, None]:
    server, state = await _start_upstream(truncate_body=True)
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
                name="Single write key",
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


@pytest.fixture()
async def trace_id(admin_db: DatabaseSession) -> AsyncGenerator[str, None]:
    """A fresh trace id; this test's events and execution rows are removed after."""
    value = secrets.token_hex(16)
    yield value
    async with admin_db.transaction() as session:
        await session.execute(delete(Event).where(Event.trace_id == value))
        await session.execute(delete(ExecutionRecord).where(ExecutionRecord.trace_id == value))


@dataclass
class _AdminWrites:
    checkouts: int = 0
    transactions: int = 0


@contextmanager
def _count_admin_writes(admin_db: DatabaseSession) -> Iterator[_AdminWrites]:
    """Count admin-engine pool checkouts and transactions (BEGIN) in the block."""
    counts = _AdminWrites()
    engine = admin_db.engine.sync_engine

    def _on_checkout(*_: Any) -> None:
        counts.checkouts += 1

    def _on_begin(*_: Any) -> None:
        counts.transactions += 1

    event.listen(engine.pool, "checkout", _on_checkout)
    event.listen(engine, "begin", _on_begin)
    try:
        yield counts
    finally:
        event.remove(engine.pool, "checkout", _on_checkout)
        event.remove(engine, "begin", _on_begin)


class _RaisingBroker:
    """An injected ``Broker`` whose call raises a non-domain error.

    Models a programming error inside the persistence transaction: the
    transaction rolls back, taking the flushed audit event with it.
    """

    async def execute(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("broker exploded")

    async def execute_streaming(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("broker exploded")


def _app(ctx: Context, runner: HttpRunner, upstream_url: str, trace_id: str) -> FastAPI:
    app = FastAPI()

    def _ctx_req(request: Request) -> ExecuteRequestContext:
        return ExecuteRequestContext(
            upstream_url=upstream_url,
            method=request.method,
            trace_id=trace_id,
            operation=OperationInfo(id="getThing", path="/thing", method="GET"),
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
        )

    @app.get("/buffered")
    async def buffered(request: Request) -> Response:
        ctx_req = _ctx_req(request)
        outcome = await _execute_buffered(request, ctx_req, ctx, _IDENTITY, runner, None)
        return _assemble_response(outcome, ctx_req)

    @app.get("/streaming")
    async def streaming(request: Request) -> Response:
        return await _handle_streaming(
            request,
            _ctx_req(request),
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


async def _lifecycle_events(admin_db: DatabaseSession, trace_id: str) -> list[Event]:
    async with admin_db.session() as session:
        rows = await session.execute(
            select(Event).where(
                Event.trace_id == trace_id,
                Event.type.in_([EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED]),
            )
        )
        return list(rows.scalars())


async def _execution(admin_db: DatabaseSession, trace_id: str) -> ExecutionRecord | None:
    async with admin_db.session() as session:
        rows = await session.execute(
            select(ExecutionRecord).where(ExecutionRecord.trace_id == trace_id)
        )
        return rows.scalar_one_or_none()


@pytest.mark.parametrize("path", ["/buffered", "/streaming"])
async def test_successful_call_uses_one_admin_write_session(
    integration_context: Context,
    upstream: _Upstream,
    seeded_credential: str,
    trace_id: str,
    path: str,
) -> None:
    """A successful credentialed call opens exactly one admin session/transaction,
    which carries the access audit event, the execution record and its event."""
    admin_db = integration_context.admin_db
    async with httpx.AsyncClient() as upstream_client:
        app = _app(
            integration_context,
            HttpRunner(upstream_client),
            f"http://127.0.0.1:{upstream.port}/thing",
            trace_id,
        )
        with _count_admin_writes(admin_db) as writes:
            resp = await _call(app, path)

    assert resp.status_code == 200
    assert upstream.seen_api_keys == [seeded_credential], "the credential reached the upstream"
    assert resp.headers[JenticHeader.CREDENTIAL_ID.value] == _CREDENTIAL_ID
    assert writes.checkouts == 1, writes
    assert writes.transactions == 1, writes

    access = await _access_events(admin_db, trace_id)
    lifecycle = await _lifecycle_events(admin_db, trace_id)
    record = await _execution(admin_db, trace_id)
    assert len(access) == 1
    assert access[0].actor_id == _AGENT
    assert access[0].data["credential_id"] == _CREDENTIAL_ID
    assert [e.type for e in lifecycle] == [EventType.EXECUTION_COMPLETED]
    assert record is not None and record.status == ExecutionStatus.COMPLETED
    assert record.credential_id == _CREDENTIAL_ID
    # The access event keeps the moment of access (before the upstream call),
    # so it sorts before the execution's lifecycle event.
    assert access[0].created_at <= lifecycle[0].created_at
    assert access[0].created_at <= record.started_at


@pytest.mark.parametrize("path", ["/buffered", "/streaming"])
async def test_upstream_connect_failure_still_audits_credential_access(
    integration_context: Context,
    closed_port: int,
    seeded_credential: str,
    trace_id: str,
    path: str,
) -> None:
    """The credential was injected, the upstream refused the connection: the
    access event is written exactly once (with the FAILED record on the
    buffered path, on its own on the streaming path, which opens no stream)."""
    admin_db = integration_context.admin_db
    async with httpx.AsyncClient() as upstream_client:
        app = _app(
            integration_context,
            HttpRunner(upstream_client),
            f"http://127.0.0.1:{closed_port}/thing",
            trace_id,
        )
        with _count_admin_writes(admin_db) as writes:
            resp = await _call(app, path)

    assert resp.status_code >= 500
    assert len(await _access_events(admin_db, trace_id)) == 1
    assert writes.transactions == 1, writes
    if path == "/buffered":
        record = await _execution(admin_db, trace_id)
        assert record is not None and record.status == ExecutionStatus.FAILED


async def test_mid_stream_upstream_failure_still_audits_credential_access(
    integration_context: Context,
    truncating_upstream: _Upstream,
    seeded_credential: str,
    trace_id: str,
) -> None:
    """The upstream dies mid-body after the response started: the body raises,
    Starlette skips the persistence background task, and the response's exit
    guard still writes the access event (no execution record is persisted on
    this path, so the guard is the only writer)."""
    admin_db = integration_context.admin_db
    async with httpx.AsyncClient() as upstream_client:
        app = _app(
            integration_context,
            HttpRunner(upstream_client),
            f"http://127.0.0.1:{truncating_upstream.port}/thing",
            trace_id,
        )
        await _call(app, "/streaming")

    assert truncating_upstream.seen_api_keys == [seeded_credential]
    assert await _execution(admin_db, trace_id) is None
    assert len(await _access_events(admin_db, trace_id)) == 1


@pytest.mark.parametrize("path", ["/buffered", "/streaming"])
async def test_raise_after_injection_still_audits_credential_access(
    integration_context: Context,
    upstream: _Upstream,
    seeded_credential: str,
    trace_id: str,
    path: str,
) -> None:
    """A non-domain raise after injection (the persistence transaction rolls
    back on the buffered path; no response exists on the streaming path)
    leaves no execution record, but the access event is still written."""
    admin_db = integration_context.admin_db
    async with httpx.AsyncClient() as upstream_client:
        app = _app(
            integration_context,
            HttpRunner(upstream_client),
            f"http://127.0.0.1:{upstream.port}/thing",
            trace_id,
        )
        app.state.broker = _RaisingBroker()
        resp = await _call(app, path)

    assert resp.status_code == 500
    assert await _execution(admin_db, trace_id) is None
    assert len(await _access_events(admin_db, trace_id)) == 1


async def test_pending_access_is_written_once_across_rollback_and_guard(
    admin_db: DatabaseSession, trace_id: str
) -> None:
    """A flush in a rolled-back transaction leaves the event pending; the
    standalone guard writes it; a second guard after the commit is a no-op."""
    pending = PendingCredentialAccess(
        actor_id=_AGENT,
        actor_type=ActorType.AGENT.value,
        credential_id=_CREDENTIAL_ID,
        provider=_VENDOR,
        wire_type="api_key",
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        trace_id=trace_id,
    )

    with pytest.raises(RuntimeError):
        async with admin_db.transaction() as session:
            await pending.emit(session)
            await pending.emit(session)  # idempotent within one transaction
            raise RuntimeError("persistence failed")
    assert not pending.recorded
    assert await _access_events(admin_db, trace_id) == []

    await pending.record_standalone(admin_db)
    assert pending.recorded
    await pending.record_standalone(admin_db)

    events = await _access_events(admin_db, trace_id)
    assert len(events) == 1
    assert events[0].created_at == pending.accessed_at
