"""Integration tests for upstream scheme enforcement against operation server declarations."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, select, text, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.users import User
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.repos import AgentRuleEvaluator, CredentialBindingResolver
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, ApiRevisionSourceType, StoredCredentialType
from jentic_one.shared.models.events import EventType
from jentic_one.wiring import InProcessRegistryResolver, install_broker_registry_resolver
from tests.harness.db_round_trips import trace_round_trips

pytestmark = pytest.mark.integration

_VENDOR = "scheme-guard-vendor"
_HTTPS_API_NAME = "https-only-api"
_HTTP_API_NAME = "http-enabled-api"
_API_VERSION = "1.0.0"

_OWNER_ID = "usr_scheme_guard_owner"
_AGENT_ID = "agnt_scheme_guard_agent"
_HTTPS_CRED_ID = "cred_scheme_guard_https"
_HTTP_CRED_ID = "cred_scheme_guard_http"
_BEARER_VALUE = "test-bearer-value-scheme-guard"


def _owner_identity() -> Identity:
    return Identity(
        sub=_OWNER_ID,
        email="scheme-guard@test.local",
        permissions=[
            "org:admin",
            "apis:read",
            "apis:write",
            "credentials:read",
            "credentials:write",
        ],
    )


def _agent_identity() -> Identity:
    return Identity(
        sub=_AGENT_ID,
        actor_type=ActorType.AGENT,
        parent_actor_id=_OWNER_ID,
        permissions=list(DEFAULT_AGENT_PERMISSIONS),
    )


async def _wipe_all(ctx: Context) -> None:
    async with ctx.registry_db.session() as session:
        rev_ids = (
            select(ApiRevision.id)
            .join(Api, Api.id == ApiRevision.api_id)
            .where(Api.vendor == _VENDOR)
        )
        srv_ids = select(Server.id).where(Server.revision_id.in_(rev_ids))
        await session.execute(
            delete(OperationURLIndex).where(OperationURLIndex.revision_id.in_(rev_ids))
        )
        await session.execute(delete(ServerVariable).where(ServerVariable.server_id.in_(srv_ids)))
        await session.execute(delete(Server).where(Server.revision_id.in_(rev_ids)))
        await session.execute(delete(Operation).where(Operation.revision_id.in_(rev_ids)))
        await session.execute(delete(SpecFile).where(SpecFile.revision_id.in_(rev_ids)))
        await session.execute(
            update(Api).where(Api.vendor == _VENDOR).values(current_revision_id=None)
        )
        await session.execute(
            delete(ApiRevision).where(
                ApiRevision.api_id.in_(select(Api.id).where(Api.vendor == _VENDOR))
            )
        )
        await session.execute(delete(Api).where(Api.vendor == _VENDOR))
        await session.commit()

    async with ctx.control_db.session() as session:
        await session.execute(
            delete(AgentPermissionRule).where(AgentPermissionRule.agent_id == _AGENT_ID)
        )
        await session.execute(
            delete(TokenValueCredential).where(
                TokenValueCredential.credential_id.in_((_HTTPS_CRED_ID, _HTTP_CRED_ID))
            )
        )
        await session.execute(
            delete(Credential).where(Credential.id.in_((_HTTPS_CRED_ID, _HTTP_CRED_ID)))
        )
        await session.commit()

    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == _AGENT_ID)
        )
        await session.execute(delete(ExecutionRecord).where(ExecutionRecord.actor_id == _AGENT_ID))
        await session.execute(delete(Event).where(Event.actor_id == _AGENT_ID))
        await session.execute(delete(Agent).where(Agent.id == _AGENT_ID))
        await session.execute(delete(User).where(User.id == _OWNER_ID))
        await session.commit()


async def _ingest_and_promote(
    ctx: Context,
    *,
    api_name: str,
    server_url: str,
    sha: str,
) -> None:
    content = {
        "openapi": "3.1.0",
        "info": {"title": api_name, "version": _API_VERSION},
        "servers": [{"url": server_url}],
        "paths": {
            "/x": {
                "get": {
                    "operationId": "getX",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    specification = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor=_VENDOR, name=api_name, version=_API_VERSION),
        sha=sha,
        content=content,
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="openapi.json",
        submitted_by=_OWNER_ID,
    )
    ingested = await Ingestor(ctx).ingest(specification, created_by=_OWNER_ID)
    await RevisionService(ctx).promote(
        _VENDOR,
        api_name,
        _API_VERSION,
        str(ingested.revision_id),
        identity=_owner_identity(),
    )


async def _seed_bound_bearer_credential(
    ctx: Context,
    *,
    cred_id: str,
    api_name: str,
) -> None:
    async with ctx.admin_db.session() as session:
        existing_user = await session.get(User, _OWNER_ID)
        if existing_user is None:
            session.add(
                User(
                    id=_OWNER_ID,
                    email="scheme-guard@test.local",
                    first_name="Scheme",
                    last_name="Guard",
                    active=True,
                    created_by=_OWNER_ID,
                )
            )
            await session.flush()
        existing_agent = await session.get(Agent, _AGENT_ID)
        if existing_agent is None:
            session.add(
                Agent(
                    id=_AGENT_ID,
                    name="scheme-guard-agent",
                    owner_id=_OWNER_ID,
                    registered_by=_OWNER_ID,
                    created_by=_OWNER_ID,
                    status="active",
                )
            )
            await session.flush()
        session.add(
            AgentCredentialBinding(
                agent_id=_AGENT_ID,
                credential_id=cred_id,
                created_by=_OWNER_ID,
            )
        )
        await session.commit()

    async with ctx.control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.STATIC_BEARER_TOKEN,
                name=f"bearer-{cred_id}",
                api_vendor=_VENDOR,
                api_name=api_name,
                api_version=_API_VERSION,
                active=True,
                state="connected",
                created_by=_OWNER_ID,
            )
        )
        await session.flush()
        session.add(
            TokenValueCredential(
                credential_id=cred_id,
                encrypted_token_value=ctx.encryption.encrypt(_BEARER_VALUE),
            )
        )
        await AgentPermissionRuleRepository.replace_user_rules(
            session,
            _AGENT_ID,
            cred_id,
            [{"effect": "allow", "methods": ["GET"], "path": "/x", "match_mode": "exact"}],
            created_by=_OWNER_ID,
        )
        await session.commit()


@pytest.fixture()
async def clean_scheme_guard_env(
    integration_context: Context,
) -> AsyncGenerator[Context, None]:
    await _wipe_all(integration_context)
    yield integration_context
    await _wipe_all(integration_context)


@asynccontextmanager
async def _broker_client(
    ctx: Context,
) -> AsyncIterator[tuple[httpx.AsyncClient, list[httpx.Request]]]:
    captured: list[httpx.Request] = []

    def _mock_upstream(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(
                {
                    "forwarded_url": str(req.url),
                    "auth": req.headers.get("authorization"),
                }
            ).encode(),
        )

    app = FastAPI()
    install_broker_error_handlers(app)
    install_broker_registry_resolver(app, ctx)
    deriver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    evaluator = AgentRuleEvaluator(ctx.control_db, cache_ttl_seconds=0)
    identity = _agent_identity()

    public_ip = (ipaddress.ip_address("93.184.216.34"),)
    with patch(
        "jentic_one.shared.url_validation.resolve_host",
        new=AsyncMock(return_value=public_ip),
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_upstream)) as upstream:
            runner = HttpRunner(upstream)

            @app.api_route("/{upstream_url:path}", methods=["GET", "POST"])
            async def proxy(upstream_url: str, request: Request) -> Response:
                return await _handle(
                    request, request.method, ctx, identity, deriver, evaluator, runner, None
                )

            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
                yield client, captured


async def test_http_request_to_https_only_api_rejected_before_injection(
    clean_scheme_guard_env: Context,
) -> None:
    ctx = clean_scheme_guard_env
    await _ingest_and_promote(
        ctx,
        api_name=_HTTPS_API_NAME,
        server_url="https://api.example.com",
        sha="sha-scheme-guard-https-only",
    )
    await _seed_bound_bearer_credential(
        ctx,
        cred_id=_HTTPS_CRED_ID,
        api_name=_HTTPS_API_NAME,
    )

    async with _broker_client(ctx) as (client, captured):
        resp = await client.get("/http://api.example.com/x")

    assert resp.status_code == 400, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers[JenticHeader.ERROR_ORIGIN.value] == "broker"
    body = resp.json()
    assert body["type"] == "insecure_upstream_scheme"
    assert body["detail"] == "This API is only served over HTTPS; call it with https://."
    assert body["agent_directive"]["strategy"] == "retry"
    assert captured == []

    async with ctx.admin_db.session() as session:
        audit_rows = (
            (
                await session.execute(
                    select(Event).where(
                        Event.type == EventType.CREDENTIAL_ACCESSED,
                        Event.actor_id == _AGENT_ID,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert audit_rows == []


async def test_http_allowed_when_spec_declares_http_server(
    clean_scheme_guard_env: Context,
) -> None:
    ctx = clean_scheme_guard_env
    await _ingest_and_promote(
        ctx,
        api_name=_HTTP_API_NAME,
        server_url="http://plain.example.com",
        sha="sha-scheme-guard-http-allowed",
    )
    await _seed_bound_bearer_credential(
        ctx,
        cred_id=_HTTP_CRED_ID,
        api_name=_HTTP_API_NAME,
    )

    async with _broker_client(ctx) as (client, captured):
        resp = await client.get("/http://plain.example.com/x")

    assert resp.status_code == 200, resp.text
    assert len(captured) == 1
    assert str(captured[0].url) == "http://plain.example.com/x"
    assert captured[0].headers["authorization"] == f"Bearer {_BEARER_VALUE}"


async def test_https_request_does_not_load_servers(
    clean_scheme_guard_env: Context,
) -> None:
    ctx = clean_scheme_guard_env
    await _ingest_and_promote(
        ctx,
        api_name=_HTTPS_API_NAME,
        server_url="https://api.example.com",
        sha="sha-scheme-guard-query-count",
    )
    resolver = InProcessRegistryResolver(ctx.registry_db, ctx=ctx)

    async with ctx.registry_db.read_session() as session:
        await session.execute(text("SELECT 1"))

    with trace_round_trips(ctx.registry_db) as https_trace:
        https_resolved = await resolver.resolve_operation(
            method="GET", url="https://api.example.com/x"
        )

    assert https_resolved is not None
    assert https_resolved.plaintext_http_allowed is True
    assert len(https_trace.statements) == 1, https_trace.statements
    assert "servers" not in https_trace.statements[0].lower()
    https_trace.assert_no_begin_or_rollback()

    with trace_round_trips(ctx.registry_db) as http_trace:
        http_resolved = await resolver.resolve_operation(
            method="GET", url="http://api.example.com/x"
        )

    assert http_resolved is not None
    assert http_resolved.plaintext_http_allowed is False
    assert len(http_trace.statements) == 2, http_trace.statements
    http_trace.assert_no_begin_or_rollback()
