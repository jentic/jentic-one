"""Integration tests: a keyed execution that fails releases its Idempotency-Key claim (#1710).

Drives the real broker catch-all route (``execute.router``) end to end against
real databases: the registry resolves the operation from seeded rows, the agent's
direct credential binding and allow rule come from the admin and control DBs,
the credential is decrypted and injected, and the execution record is persisted.
The runner stack is the production one (``HttpRunner`` behind a
``CircuitBreakerRunner``); only the outbound HTTP leg is an
``httpx.MockTransport`` so a test can make the upstream answer, hang, or fail at
the transport level. The idempotency store is the real
``SharedStateIdempotencyStore`` over the in-process memory backend.

Pins:

- a failed attempt (circuit open, transport error, timeout, an unexpected
  exception, a cancelled request) releases the claim, so the immediate same-key
  retry runs again instead of answering ``409 idempotency_in_progress``;
- a success is still stored and replayed without a second upstream call;
- a duplicate that arrives while the original is in flight still gets ``409``;
- a store failure during the release never masks the original error, and the
  claim then falls back to its pending TTL.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.circuit import CircuitBreakerRunner
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.broker.services.idempotency import SharedStateIdempotencyStore
from jentic_one.broker.web.deps import require_execute_within_rate_limit
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers import execute
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import build_index_entry
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.broker.execution import RunnerRequest, RunnerResult, StreamingResult
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorType, ApiRevisionState, StoredCredentialType
from jentic_one.shared.resilience import CircuitBreaker
from jentic_one.shared.state.backend import MemoryStateBackend
from jentic_one.wiring import InProcessRegistryResolver

pytestmark = pytest.mark.integration

_VENDOR = "idem-release.example"
_API_NAME = "charges-api"
_API_VERSION = "v1"
# An IP-literal upstream (no DNS lookup), let through by an egress allowlist the
# tests install; the MockTransport answers it, so nothing listens on it.
_HOST = "127.0.0.1"
_PATH = "/v1/charges"
_ROUTE = f"/{_HOST}{_PATH}"
_BODY = b'{"amount": 500}'
_PENDING_TTL_S = 35.0


class _Clock:
    """Monotonic stand-in so the pending-TTL fallback is checked without sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class _Upstream:
    """The outbound HTTP leg: each call takes the next scripted behaviour."""

    script: list[Callable[[httpx.Request], httpx.Response]] = field(default_factory=list)
    calls: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.entered.set()
        behaviour = self.script.pop(0) if self.script else _ok
        if behaviour is _hang:
            await self.release.wait()
            return _ok(request)
        return behaviour(request)


def _json(status: int, body: bytes) -> httpx.Response:
    # An unread byte stream, as a socket transport would hand the runner.
    return httpx.Response(
        status, headers={"content-type": "application/json"}, stream=httpx.ByteStream(body)
    )


def _ok(_request: httpx.Request) -> httpx.Response:
    return _json(201, b'{"charge": "ch_1"}')


def _upstream_503(_request: httpx.Request) -> httpx.Response:
    return _json(503, b'{"error": "unavailable"}')


def _connection_dropped(request: httpx.Request) -> httpx.Response:
    raise httpx.RemoteProtocolError("peer closed connection", request=request)


def _read_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _hang(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - sentinel
    raise AssertionError("handled in _Upstream.handle")


class _ExplodingRunner:
    """Raises a non-``BrokerError`` once, then delegates — an unexpected bug."""

    def __init__(self, inner: CircuitBreakerRunner) -> None:
        self._inner = inner
        self.armed = False

    async def run(self, request: RunnerRequest) -> RunnerResult:
        if self.armed:
            self.armed = False
            raise RuntimeError("unexpected runner bug")
        return await self._inner.run(request)

    def stream(self, request: RunnerRequest) -> AbstractAsyncContextManager[StreamingResult]:
        return self._inner.stream(request)


class _ReleaseFailsBackend(MemoryStateBackend):
    """A real memory backend whose compare-and-delete fails like a lost store."""

    async def delete_if_equals(self, key: str, expected: bytes) -> bool:
        raise ConnectionError("state store unreachable")


@dataclass
class _Harness:
    client: httpx.AsyncClient
    upstream: _Upstream
    breaker: CircuitBreaker
    runner: _ExplodingRunner
    clock: _Clock

    async def post(self, key: str | None) -> httpx.Response:
        headers = {"content-type": "application/json"}
        if key is not None:
            headers["idempotency-key"] = key
        return await self.client.post(_ROUTE, content=_BODY, headers=headers)


@pytest.fixture()
async def agent_id(integration_context: Context) -> AsyncGenerator[str, None]:
    """Seed the registry operation, a bound agent, its credential and an allow rule."""
    ctx = integration_context
    async with ctx.registry_db.session() as session:
        api = Api(vendor=_VENDOR, name=_API_NAME, version=_API_VERSION)
        session.add(api)
        await session.flush()
        revision = ApiRevision(
            api_id=api.id, state=ApiRevisionState.PUBLISHED.value, source_type="url"
        )
        session.add(revision)
        await session.flush()
        api_id, revision_id = api.id, revision.id
        await ApiRepository.set_current_revision(session, api_id, revision_id)
        await session.commit()
    async with ctx.registry_db.session() as session:
        (op_id,) = await OperationRepository.bulk_create(
            session,
            revision_id,
            [OperationInput(path=_PATH, method="POST")],
            created_by="usr_test",
        )
        await UrlIndexRepository.upsert_entry(
            session,
            revision_id=revision_id,
            operation_id=op_id,
            method="POST",
            entry=build_index_entry(_HOST, _PATH, "https"),
            created_by="usr_test",
        )
        await session.commit()

    credential = Credential(
        type=StoredCredentialType.API_KEY,
        name="idem-release-key",
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
    )
    async with ctx.control_db.session() as session:
        session.add(credential)
        await session.flush()
        credential_id = credential.id
        session.add(
            CustomerAPIKey(
                id=generate_ksuid("key"),
                credential_id=credential_id,
                encrypted_key=ctx.encryption.encrypt("SECRET"),  # pragma: allowlist secret
                location="header",
                field_name="X-Api-Key",
            )
        )
        await session.commit()
    agent = Agent(
        name=f"idem-release-{uuid.uuid4().hex[:8]}", registered_by="usr_owner", status="active"
    )
    async with ctx.admin_db.session() as session:
        session.add(agent)
        await session.flush()
        new_agent_id = agent.id
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"), agent_id=new_agent_id, credential_id=credential_id
            )
        )
        session.add(
            ActorPermissionGrant(
                actor_id=new_agent_id, actor_type="agent", permission=BROKER_EXECUTE_PERMISSION
            )
        )
        await session.commit()
    async with ctx.control_db.session() as session:
        session.add(
            AgentPermissionRule(
                agent_id=new_agent_id,
                credential_id=credential_id,
                effect="allow",
                methods=["POST"],
                path=".*",
                sequence=1,
            )
        )
        await session.commit()

    yield new_agent_id

    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(ExecutionRecord).where(ExecutionRecord.actor_id == new_agent_id)
        )
        await session.execute(delete(Event).where(Event.actor_id == new_agent_id))
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == new_agent_id)
        )
        await session.execute(
            delete(ActorPermissionGrant).where(ActorPermissionGrant.actor_id == new_agent_id)
        )
        await session.execute(delete(Agent).where(Agent.id == new_agent_id))
        await session.commit()
    async with ctx.control_db.session() as session:
        await session.execute(
            delete(AgentPermissionRule).where(AgentPermissionRule.agent_id == new_agent_id)
        )
        await session.execute(
            delete(CustomerAPIKey).where(CustomerAPIKey.credential_id == credential_id)
        )
        await session.execute(delete(Credential).where(Credential.id == credential_id))
        await session.commit()
    async with ctx.registry_db.session() as session:
        await session.execute(
            delete(OperationURLIndex).where(OperationURLIndex.revision_id == revision_id)
        )
        await session.execute(delete(Operation).where(Operation.revision_id == revision_id))
        await session.execute(update(Api).where(Api.id == api_id).values(current_revision_id=None))
        await session.execute(delete(ApiRevision).where(ApiRevision.id == revision_id))
        await session.execute(delete(Api).where(Api.id == api_id))
        await session.commit()


async def _harness(
    ctx: Context,
    agent_id: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    idempotency_backend: type[MemoryStateBackend] = MemoryStateBackend,
) -> AsyncGenerator[_Harness, None]:
    monkeypatch.setattr(ctx.config.broker.egress, "allowed_private_subnets", ["127.0.0.0/8"])
    monkeypatch.setattr(ctx.config.broker, "upstream_timeout_s", 5.0)

    upstream = _Upstream()
    breaker = CircuitBreaker(
        MemoryStateBackend(), failure_ratio=0.5, min_calls=2, window_s=60, cooldown_s=60
    )
    clock = _Clock()
    identity = Identity(
        sub=agent_id,
        actor_type=ActorType.AGENT,
        permissions=[BROKER_EXECUTE_PERMISSION],
        active=True,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle)) as outbound:
        runner = _ExplodingRunner(CircuitBreakerRunner(HttpRunner(outbound), breaker))
        registry = RunnerRegistry()
        registry.register(["http", "https"], runner, required=True)

        app = FastAPI()
        app.state.ctx = ctx
        app.state.broker_registry_resolver = InProcessRegistryResolver(ctx.registry_db, ctx=ctx)
        app.state.broker_runner_registry = registry
        app.state.broker_idempotency_store = SharedStateIdempotencyStore(
            idempotency_backend(clock=clock),
            pending_ttl_s=_PENDING_TTL_S,
            done_ttl_s=86_400.0,
            max_response_bytes=64 * 1024,
        )
        install_broker_auth(app, ctx)
        install_broker_error_handlers(app)
        app.include_router(execute.router)
        app.dependency_overrides[require_execute_within_rate_limit] = lambda: identity

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            yield _Harness(
                client=client, upstream=upstream, breaker=breaker, runner=runner, clock=clock
            )


@pytest.fixture()
async def harness(
    integration_context: Context, agent_id: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[_Harness, None]:
    async for h in _harness(integration_context, agent_id, monkeypatch):
        yield h


@pytest.fixture()
async def release_fails_harness(
    integration_context: Context, agent_id: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[_Harness, None]:
    async for h in _harness(
        integration_context, agent_id, monkeypatch, idempotency_backend=_ReleaseFailsBackend
    ):
        yield h


@pytest.mark.parametrize(
    ("failure", "status", "problem_type"),
    [
        (_connection_dropped, None, "upstream_transport_error"),
        (_read_timeout, 504, "upstream_timeout"),
    ],
    ids=["transport_error", "timeout"],
)
async def test_failed_attempt_releases_claim_so_retry_executes(
    harness: _Harness,
    failure: Callable[[httpx.Request], httpx.Response],
    status: int | None,
    problem_type: str,
) -> None:
    """A transport error / timeout frees the key: the retry runs and its result replays."""
    harness.upstream.script = [failure]

    first = await harness.post("key-failed-once")
    assert first.json()["type"] == problem_type, first.text
    if status is not None:
        assert first.status_code == status
    else:
        # The transport-error status is the runner's to choose; the class is the contract.
        assert first.status_code >= 500

    retry = await harness.post("key-failed-once")
    assert retry.status_code == 201, retry.text
    assert harness.upstream.calls == 2

    replay = await harness.post("key-failed-once")
    assert replay.status_code == 201
    assert replay.json() == {"charge": "ch_1"}
    assert harness.upstream.calls == 2


async def test_circuit_open_releases_claim(harness: _Harness) -> None:
    """A ``circuit_open`` fast-fail frees the key: the retry is re-evaluated, not 409."""
    harness.upstream.script = [_upstream_503, _upstream_503]
    for n in range(2):
        tripped = await harness.post(f"trip-{n}")
        assert tripped.status_code == 503
    calls_before = harness.upstream.calls

    first = await harness.post("key-circuit")
    assert first.status_code == 503
    assert first.json()["type"] == "circuit_open"

    retry = await harness.post("key-circuit")
    assert retry.status_code == 503, retry.text
    assert retry.json()["type"] == "circuit_open"
    assert harness.upstream.calls == calls_before


async def test_unexpected_exception_releases_claim(harness: _Harness) -> None:
    """A non-``BrokerError`` failure (a bug) frees the key too."""
    harness.runner.armed = True

    first = await harness.post("key-bug")
    assert first.status_code == 500

    retry = await harness.post("key-bug")
    assert retry.status_code == 201, retry.text
    assert harness.upstream.calls == 1


async def test_cancelled_request_releases_claim(harness: _Harness) -> None:
    """A request cancelled mid-flight (client gone) frees the key."""
    harness.upstream.script = [_hang]
    in_flight = asyncio.create_task(harness.post("key-cancelled"))
    await asyncio.wait_for(harness.upstream.entered.wait(), timeout=10)
    in_flight.cancel()
    with pytest.raises(asyncio.CancelledError):
        await in_flight

    retry = await harness.post("key-cancelled")
    assert retry.status_code == 201, retry.text
    assert harness.upstream.calls == 2


async def test_in_flight_duplicate_still_conflicts(harness: _Harness) -> None:
    """While the original holds the claim, a same-key duplicate is ``409``."""
    harness.upstream.script = [_hang]
    original = asyncio.create_task(harness.post("key-concurrent"))
    await asyncio.wait_for(harness.upstream.entered.wait(), timeout=10)

    duplicate = await harness.post("key-concurrent")
    assert duplicate.status_code == 409
    assert duplicate.json()["type"] == "idempotency_in_progress"
    assert duplicate.headers["retry-after"] == str(int(_PENDING_TTL_S))

    harness.upstream.release.set()
    done = await asyncio.wait_for(original, timeout=10)
    assert done.status_code == 201

    replay = await harness.post("key-concurrent")
    assert replay.status_code == 201
    assert harness.upstream.calls == 1


async def test_release_failure_keeps_original_error_and_falls_back_to_ttl(
    release_fails_harness: _Harness,
) -> None:
    """A store that fails the release never masks the failure; the TTL frees the key."""
    harness = release_fails_harness
    harness.upstream.script = [_read_timeout]

    first = await harness.post("key-store-down")
    assert first.status_code == 504
    assert first.json()["type"] == "upstream_timeout"

    # The claim could not be released, so it holds until its pending TTL.
    held = await harness.post("key-store-down")
    assert held.status_code == 409
    assert held.json()["type"] == "idempotency_in_progress"

    harness.clock.now += _PENDING_TTL_S + 1
    retry = await harness.post("key-store-down")
    assert retry.status_code == 201, retry.text
    assert harness.upstream.calls == 2
