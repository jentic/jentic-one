"""Integration test: the buffered execute path holds no admin DB lock across the upstream call.

Drives the router's ``_handle`` (buffered sync branch) against a real local
HTTP upstream over a real socket that answers after a delay, with real admin and
control databases and a real seeded credential. Discovery, binding derivation
and rule evaluation are the cross-surface protocol seams, so they are stubbed
with fixed answers.

While the upstream is waiting, the admin database must not have a connection
checked out for the execution: on SQLite an open write transaction holds the
database write lock (``BEGIN IMMEDIATE``), which would serialise concurrent
executions and stall every other admin write for the length of the upstream
round-trip.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, event, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    BoundCredential,
    CredentialDerivation,
    ResolveResult,
    RevisionPinResult,
    RuleEvaluation,
    RuleVerdict,
)
from jentic_one.shared.config import AppConfig, EgressConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, ExecutionStatus, StoredCredentialType
from jentic_one.shared.schemas import APIReference, OperationInfo

pytestmark = pytest.mark.integration

_DELAY_S = 1.5
_API = APIReference(vendor="slow.example.com", name="slow", version="v1")
_CRED_ID = "cred_tx_scope_1740"
_IDENTITY = Identity(
    sub="agnt_tx_scope_1740",
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)


@dataclass
class _Upstream:
    """A delayed HTTP/1.1 upstream on a real socket.

    Records, as each request arrives, how many admin DB connections are checked
    out — the connections an execution would be holding across the upstream wait.
    """

    port: int = 0
    checked_out: int = 0
    checkouts_on_arrival: list[int] = field(default_factory=list)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    fail: bool = False


@pytest.fixture()
async def upstream() -> AsyncGenerator[_Upstream, None]:
    state = _Upstream()

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        state.checkouts_on_arrival.append(state.checked_out)
        state.arrived.set()
        await asyncio.sleep(_DELAY_S)
        if not state.fail:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
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
async def ctx(integration_config: AppConfig, upstream: _Upstream) -> AsyncGenerator[Context, None]:
    """A Context whose egress policy admits the loopback upstream, buffered path only.

    Streaming passthrough is off so a sync request without an Idempotency-Key
    takes the buffered branch under test. Admin pool checkouts are counted on
    ``upstream``.
    """
    config = integration_config.model_copy(deep=True)
    config.broker.egress = EgressConfig(allowed_private_subnets=["127.0.0.0/8"])
    config.broker.resilience.upstream.stream_passthrough_enabled = False
    async with Context(config) as context:
        engine = context.admin_db.engine.sync_engine

        def _on_checkout(*_: Any) -> None:
            upstream.checked_out += 1

        def _on_checkin(*_: Any) -> None:
            upstream.checked_out -= 1

        event.listen(engine, "checkout", _on_checkout)
        event.listen(engine, "checkin", _on_checkin)
        await _cleanup(context)
        await _seed_credential(context)
        try:
            yield context
        finally:
            event.remove(engine, "checkout", _on_checkout)
            event.remove(engine, "checkin", _on_checkin)
            await _cleanup(context)


async def _seed_credential(ctx: Context) -> None:
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=_CRED_ID,
                type=StoredCredentialType.API_KEY,
                name="slow",
                api_vendor=_API.vendor,
                api_name=_API.name,
                api_version=_API.version,
            )
        )
        session.add(
            CustomerAPIKey(
                id=f"key-{_CRED_ID}",
                credential_id=_CRED_ID,
                encrypted_key=ctx.encryption.encrypt("sk-slow"),  # pragma: allowlist secret
                location="header",
                field_name="X-Api-Key",
            )
        )


async def _cleanup(ctx: Context) -> None:
    async with ctx.control_db.transaction() as session:
        await session.execute(
            delete(CustomerAPIKey).where(CustomerAPIKey.credential_id == _CRED_ID)
        )
        await session.execute(delete(Credential).where(Credential.id == _CRED_ID))
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            delete(ExecutionRecord).where(ExecutionRecord.actor_id == _IDENTITY.sub)
        )
        await session.execute(delete(Event).where(Event.actor_id == _IDENTITY.sub))


class _Resolver:
    """Resolves every URL to one fixed operation of ``_API``."""

    async def resolve_operation(
        self, *, method: str, url: str, revision_id: Any = None
    ) -> ResolveResult:
        return ResolveResult(
            operation=OperationInfo(id="op_slow", path="/slow", method=method),
            api=_API,
            path_params={},
        )

    async def resolve_revision_pin(self, **_: Any) -> RevisionPinResult:  # pragma: no cover
        raise AssertionError("no revision pins in this test")


class _Deriver:
    """The agent is bound to the seeded credential."""

    async def derive_credentials(self, **_: Any) -> CredentialDerivation:
        return CredentialDerivation(
            credentials=(BoundCredential(credential_id=_CRED_ID, rule_set_id=None),),
            agent_bound_any=True,
            api_served=True,
            identity_mismatch=None,
        )


class _AllowAll:
    async def evaluate(self, **_: Any) -> RuleEvaluation:
        return RuleEvaluation(verdict=RuleVerdict.ALLOW, rules_loaded=1)


def _app(ctx: Context, runner: HttpRunner) -> FastAPI:
    app = FastAPI()
    app.state.broker_registry_resolver = _Resolver()

    @app.get("/{upstream_url:path}")
    async def proxy(upstream_url: str, request: Request) -> Response:
        return await _handle(request, "GET", ctx, _IDENTITY, _Deriver(), _AllowAll(), runner, None)

    return app


async def _records(ctx: Context) -> list[ExecutionRecord]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(ExecutionRecord).where(ExecutionRecord.actor_id == _IDENTITY.sub)
        )
        return list(result.scalars())


async def _timed_admin_write(ctx: Context) -> float:
    """Open (and commit) an admin write transaction; return how long it took."""
    t0 = time.perf_counter()
    async with ctx.admin_db.transaction():
        pass
    return time.perf_counter() - t0


async def test_buffered_execute_holds_no_admin_connection_during_upstream_call(
    ctx: Context, upstream: _Upstream
) -> None:
    """No admin connection is checked out while the upstream is answering.

    So another admin write is not blocked by the in-flight execution, and the
    execution record is still written once the upstream answers.
    """
    async with httpx.AsyncClient() as upstream_client:
        transport = httpx.ASGITransport(app=_app(ctx, HttpRunner(upstream_client)))
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            call = asyncio.create_task(client.get(f"/http://127.0.0.1:{upstream.port}/slow"))
            await asyncio.wait_for(upstream.arrived.wait(), timeout=10)
            write_s = await _timed_admin_write(ctx)
            resp = await call

    assert resp.status_code == 200, resp.text
    assert write_s < _DELAY_S / 2, f"admin write blocked for {write_s:.2f}s"
    assert upstream.checkouts_on_arrival == [0]
    records = await _records(ctx)
    assert [(r.status, r.http_status) for r in records] == [(ExecutionStatus.COMPLETED, 200)]


async def test_concurrent_buffered_executes_do_not_serialise(
    ctx: Context, upstream: _Upstream
) -> None:
    """Two concurrent buffered executes overlap their upstream waits."""
    async with httpx.AsyncClient() as upstream_client:
        transport = httpx.ASGITransport(app=_app(ctx, HttpRunner(upstream_client)))
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            url = f"/http://127.0.0.1:{upstream.port}/slow"
            t0 = time.perf_counter()
            responses = await asyncio.gather(client.get(url), client.get(url))
            elapsed = time.perf_counter() - t0

    assert [r.status_code for r in responses] == [200, 200]
    assert elapsed < _DELAY_S * 1.6, f"executions serialised: {elapsed:.2f}s"
    assert len(await _records(ctx)) == 2


async def test_buffered_execute_upstream_failure_still_records_failed(
    ctx: Context, upstream: _Upstream
) -> None:
    """A transport failure still persists a FAILED execution record."""
    upstream.fail = True
    async with httpx.AsyncClient() as upstream_client:
        app = _app(ctx, HttpRunner(upstream_client))
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            resp = await client.get(f"/http://127.0.0.1:{upstream.port}/slow")

    assert resp.status_code >= 500
    assert upstream.checkouts_on_arrival == [0]
    records = await _records(ctx)
    assert [(r.status, r.http_status) for r in records] == [(ExecutionStatus.FAILED, None)]
