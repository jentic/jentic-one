"""Integration test: a container passed to ``composition.build_app`` reaches the broker's proxy.

Builds the standalone broker through ``jentic_one.composition.build_app`` (the
path ``jentic-one serve`` uses) with an ``AppContainer`` that carries an extra
router **and** an injected :class:`~jentic_one.shared.broker.broker.Broker`,
runs the app's real lifespan (databases, runner registry, credential injector,
worker), and sends an **authenticated** request to the root
``/{upstream_url:path}`` catch-all. The request travels the real pipeline:
authentication, execute-permission check, egress check, discovery, rule
evaluation, credential injection (real decryption of a seeded credential) and
the admin-DB execution record, then reaches the injected broker, whose response
the caller receives. The extra router answers on its own path, untouched by the
catch-all.

Only the app-state protocols the broker is wired with at build time are
stand-ins, as in ``test_server_timing.py``: the opaque-token resolver, the
registry resolver, the binding deriver and the rule evaluator.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Response
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import DualTokenValidator
from jentic_one.composition import build_app, build_context
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.broker.execution import (
    ExecutionContext,
    ExecutionOutcome,
    RunnerRequest,
    RunnerResult,
    StreamingOutcome,
    StreamingUpstreamRunner,
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
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.schemas import APIReference, OperationInfo
from jentic_one.shared.web.container import AppContainer

pytestmark = pytest.mark.integration

_CRED_ID = "cred_composition_container_integ"
_AGENT = "agnt_composition_container_integ"
_VENDOR = "container-example-com"
_INJECTED_KEY = "container-integ-key"  # pragma: allowlist secret
_SPY_BODY = b'{"source":"injected-broker"}'


class _SpyBroker:
    """An injected broker that owns transport: records the call and answers fixed."""

    def __init__(self) -> None:
        self.calls: list[RunnerRequest] = []

    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        self.calls.append(request)
        return ExecutionOutcome(
            result=RunnerResult(
                status_code=201,
                body=_SPY_BODY,
                headers={"x-spy": "1"},
                content_type="application/json",
                duration_ms=1,
            ),
            context=context,
        )

    async def execute_streaming(
        self,
        runner: StreamingUpstreamRunner,
        request: RunnerRequest,
        ctx_req: ExecuteRequestContext,
        execution_id: str,
        *,
        transfer_deadline_s: float,
        background_callback: Callable[[StreamingOutcome], Awaitable[None]] | None = None,
    ) -> Response:  # pragma: no cover - the test disables stream passthrough
        raise AssertionError("buffered path expected")


class _Resolver:
    async def resolve_operation(
        self, *, method: str, url: str, revision_id: object = None
    ) -> ResolveResult:
        return ResolveResult(
            operation=OperationInfo(id="getThings", path="/v1/things", method="GET"),
            api=APIReference(vendor=_VENDOR, name="things", version="1.0.0"),
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


_extra_router = APIRouter()


@_extra_router.get("/status")
async def _extra_status() -> dict[str, str]:
    return {"source": "extra-router"}


def _token_validator() -> DualTokenValidator:
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
    return DualTokenValidator(
        opaque=CachedTokenValidator(resolver=resolver, cache_ttl_seconds=60.0),
    )


async def _seed(ctx: Context) -> None:
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=_CRED_ID,
                type=StoredCredentialType.API_KEY,
                name="container-key",
                api_vendor=_VENDOR,
                api_name="things",
                api_version="1.0.0",
            )
        )
        session.add(
            CustomerAPIKey(
                id=f"key-{_CRED_ID}",
                credential_id=_CRED_ID,
                encrypted_key=ctx.encryption.encrypt(_INJECTED_KEY),
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
        await session.execute(delete(Event).where(Event.actor_id == _AGENT))
        await session.execute(delete(ExecutionRecord).where(ExecutionRecord.actor_id == _AGENT))


@pytest.fixture()
async def composed_broker(
    integration_config: AppConfig,
) -> AsyncGenerator[tuple[FastAPI, Context, _SpyBroker], None]:
    """The standalone broker built by ``build_app`` with a caller container, lifespan running."""
    config = integration_config.model_copy(deep=True)
    config.apps = ["broker"]
    # The injected broker never dials out, but the egress pre-flight still
    # resolves the target host: admit the loopback literal used below.
    config.broker.egress.allowed_private_subnets = ["127.0.0.0/8"]
    # Keep the sync call on the buffered path, where the injected broker's
    # ``execute`` produces the response.
    config.broker.resilience.upstream.stream_passthrough_enabled = False
    ctx = build_context(config, ["broker"])
    spy = _SpyBroker()
    container = AppContainer(
        ctx=ctx,
        broker=spy,
        extra_routers=[(_extra_router, "/overlay", ["overlay"])],
    )
    app = build_app(ctx, ["broker"], container=container)
    # The composition root ran its post-build wiring on the container-built app.
    assert app.state.broker_registry_resolver is not None
    async with app.router.lifespan_context(app):
        app.state.broker_token_validator = _token_validator()
        app.state.broker_registry_resolver = _Resolver()
        app.state.broker_credential_deriver = _Deriver()
        app.state.broker_agent_rule_evaluator = _AllowAll()
        await _cleanup(ctx)
        await _seed(ctx)
        try:
            yield app, ctx, spy
        finally:
            await _cleanup(ctx)


async def test_authenticated_catch_all_reaches_injected_broker(
    composed_broker: tuple[FastAPI, Context, _SpyBroker],
) -> None:
    app, ctx, spy = composed_broker
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        extra = await client.get("/overlay/status")
        proxied = await client.get(
            "/http://127.0.0.1:9/v1/things",
            headers={"Authorization": "Bearer at_container_integ"},
        )

    assert extra.status_code == 200
    assert extra.json() == {"source": "extra-router"}

    # The catch-all ran the pipeline and answered with the injected broker's response.
    assert proxied.status_code == 201, proxied.text
    assert proxied.content == _SPY_BODY
    assert proxied.headers["x-spy"] == "1"
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call.url == "http://127.0.0.1:9/v1/things"
    # The seeded credential was decrypted and injected before the broker ran.
    assert call.headers["X-Api-Key"] == _INJECTED_KEY

    async with ctx.admin_db.session() as session:
        records = (
            (
                await session.execute(
                    select(ExecutionRecord).where(ExecutionRecord.actor_id == _AGENT)
                )
            )
            .scalars()
            .all()
        )
    assert len(records) == 1
    assert records[0].http_status == 201
