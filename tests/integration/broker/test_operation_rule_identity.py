"""Integration regression tests for permission-rule operation identity (#1081).

Pins one canonical operation-identity contract across live broker execution,
queued re-authorization, and ``POST .../permissions:test`` dry-run:

- Rules authored with OpenAPI ``operationId`` match live execution, queued
  re-authorization, and ``permissions:test`` (whether ``operation_id`` is
  supplied or resolved from ``(method, path)``), and continue to match cleanly
  after promoting a new API revision that mints new ``op_…`` primary keys.
- Rules authored with a revision-scoped ``op_…`` primary key remain compatible
  on that revision and fail closed on both live execution and ``permissions:test``
  once a promoted revision supersedes it.
- Ambiguous ``operationId``s across multiple live APIs or dry-run candidates
  covered by a wildcard credential fail closed (never grant ``allow`` and never
  fall through a ``deny`` or ``require-approval`` rule to a broader ``allow``),
  while unrelated vendors do not pollute a credential's scope.
- Default-deny invariants, exact-equality matching (never substring), and
  server-relative ``OperationInfo.relative_path`` method/path parity hold.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.setup import build_queued_execution_authorizer
from jentic_one.broker.repos import (
    AgentRuleEvaluator,
    CredentialBindingResolver,
)
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    BROKER_EXECUTE_PERMISSION,
    DEFAULT_AGENT_PERMISSIONS,
    ORG_ADMIN,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.protocols import (
    InjectedAuth,
    UpstreamExecRequest,
    UpstreamExecResult,
)
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.web.background_tasks import install_background_tasks
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.wiring import InProcessRegistryResolver, install_broker_registry_resolver

pytestmark = pytest.mark.integration

VENDOR = "op-rule-1081"
OTHER_VENDOR = "other-vendor-1081"
API_NAME = "shop"
BILLING_API = "billing"
ADMIN_API = "admin-api"
VERSION = "1.0.0"
# Public IP literals pass the broker SSRF pre-check without DNS resolution;
# the httpx MockTransport intercepts every outbound upstream call in-memory.
HOST_A = "93.184.215.14"
HOST_B = "93.184.215.15"
HOST_OTHER = "93.184.215.16"
OPERATOR_ID = "usr_op_rule_1081_operator"
AGENT_ID = "agnt_op_rule_1081_agent"
TOKEN_SHA = hashlib.sha256(b"jak_op_rule_1081_key").hexdigest()


def _operator_identity() -> Identity:
    return Identity(
        sub=OPERATOR_ID,
        actor_type=ActorType.USER,
        permissions=[ORG_ADMIN, "apis:write", "credentials:read", "credentials:write"],
    )


def _agent_identity() -> Identity:
    return Identity(
        sub=AGENT_ID,
        actor_type=ActorType.AGENT,
        permissions=list(DEFAULT_AGENT_PERMISSIONS),
        auth_token_sha256=TOKEN_SHA,
        auth_method="api_key",
    )


async def _wipe(ctx: Context) -> None:
    async with ctx.control_db.session() as session:
        await session.execute(
            delete(AgentPermissionRule).where(AgentPermissionRule.agent_id == AGENT_ID)
        )
        await session.execute(delete(CustomerAPIKey))
        await session.execute(
            delete(Credential).where(Credential.api_vendor.in_([VENDOR, OTHER_VENDOR]))
        )
        await session.commit()

    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == AGENT_ID)
        )
        await session.execute(
            delete(ActorPermissionGrant).where(ActorPermissionGrant.actor_id == AGENT_ID)
        )
        await session.execute(delete(Agent).where(Agent.id == AGENT_ID))
        await session.execute(delete(Event))
        await session.commit()

    async with ctx.registry_db.session() as session:
        for table in (
            OperationURLIndex,
            SecuritySchemeFlow,
            SecurityScheme,
            ServerVariable,
            Server,
            Operation,
            SpecFile,
        ):
            await session.execute(delete(table))
        await session.execute(update(Api).values(current_revision_id=None))
        await session.execute(delete(ApiRevision))
        await session.execute(delete(Api))
        await session.commit()


@pytest.fixture()
async def env(integration_context: Context) -> AsyncGenerator[Context, None]:
    await _wipe(integration_context)
    async with integration_context.admin_db.session() as session:
        session.add(
            Agent(
                id=AGENT_ID,
                name="Agent 1081",
                registered_by=OPERATOR_ID,
                status="active",
            )
        )
        session.add(
            ActorPermissionGrant(
                actor_id=AGENT_ID,
                actor_type="agent",
                permission=BROKER_EXECUTE_PERMISSION,
            )
        )
        await session.commit()
    yield integration_context
    await _wipe(integration_context)


async def _import_and_promote(
    ctx: Context,
    *,
    vendor: str = VENDOR,
    name: str = API_NAME,
    version: str = VERSION,
    server_url: str = f"https://{HOST_A}/api",
    paths: dict[str, dict[str, dict[str, Any]]],
) -> str:
    """Import an OpenAPI spec and promote it as the live revision; return revision_id."""
    spec = {
        "openapi": "3.1.0",
        "info": {"title": name, "version": version, "description": uuid.uuid4().hex},
        "servers": [{"url": server_url}],
        "paths": paths,
    }
    source = {
        "type": "inline",
        "content": json.dumps(spec),
        "filename": "openapi.json",
        "vendor": vendor,
        "api_name": name,
        "version": version,
        "submitted_by": OPERATOR_ID,
    }
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        db=None,
        payload={"sources": [source]},
        created_by=OPERATOR_ID,
    )
    revision_id = str(result.body["revisions"][0]["revision_id"])
    await RevisionService(ctx).promote(
        vendor, name, version, revision_id, identity=_operator_identity()
    )
    return revision_id


async def _seed_bound_credential(
    ctx: Context,
    *,
    cred_id: str,
    vendor: str = VENDOR,
    name: str | None = API_NAME,
    version: str | None = VERSION,
) -> str:
    """Create an active API-key credential and bind AGENT_ID to it."""
    encrypted = ctx.encryption.encrypt("secret-token-1081")  # pragma: allowlist secret
    async with ctx.control_db.session() as session:
        cred = Credential(
            id=cred_id,
            type=StoredCredentialType.API_KEY,
            name=f"cred-{cred_id}",
            api_vendor=vendor,
            api_name=name,
            api_version=version,
            active=True,
            state="connected",
            created_by=OPERATOR_ID,
        )
        session.add(cred)
        await session.flush()
        session.add(
            CustomerAPIKey(
                id=generate_ksuid("key"),
                credential_id=cred_id,
                encrypted_key=encrypted,
                location="header",
                field_name="X-Api-Key",
                created_by=OPERATOR_ID,
            )
        )
        await session.commit()

    async with ctx.admin_db.session() as session:
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"),
                agent_id=AGENT_ID,
                credential_id=cred_id,
            )
        )
        await session.commit()
    return cred_id


async def _get_operation_pk(ctx: Context, revision_id: str, method: str, path: str) -> str:
    async with ctx.registry_db.session() as session:
        row = (
            await session.execute(
                select(Operation.id).where(
                    Operation.revision_id == uuid.UUID(revision_id),
                    Operation.method == method.upper(),
                    Operation.path == path,
                )
            )
        ).scalar_one()
        return str(row)


@asynccontextmanager
async def _control_client(ctx: Context) -> AsyncIterator[httpx.AsyncClient]:
    app = create_control_app(ctx)
    app.dependency_overrides[resolve_identity] = _operator_identity
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://control") as client:
        yield client
    app.dependency_overrides.clear()


@asynccontextmanager
async def _broker_client(ctx: Context) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.ctx = ctx
    install_background_tasks(app)
    install_broker_error_handlers(app)
    install_broker_registry_resolver(app, ctx)
    deriver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    evaluator = AgentRuleEvaluator(ctx.control_db, cache_ttl_seconds=0)
    identity = _agent_identity()

    def _mock_upstream(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "method": req.method, "url": str(req.url)},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_upstream)) as upstream:
        runner = HttpRunner(upstream)

        @app.api_route("/{upstream_url:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        async def proxy(upstream_url: str, request: Request) -> Response:
            return await _handle(
                request, request.method, ctx, identity, deriver, evaluator, runner, None
            )

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            yield client


@pytest.mark.asyncio
async def test_openapi_operation_id_rule_matches_live_dry_run_queued_and_survives_rollover(
    env: Context,
) -> None:
    """Pins #1081: OpenAPI ``operationId`` rules work identically across live broker,
    queued worker re-authorization, and ``permissions:test``, and survive revision promotion."""
    rev1_id = await _import_and_promote(
        env,
        paths={
            "/items": {
                "get": {"operationId": "get_items", "responses": {"200": {"description": "OK"}}},
                "post": {
                    "operationId": "create_item",
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/items/{item_id}": {
                "get": {"operationId": "get_item", "responses": {"200": {"description": "OK"}}},
            },
        },
    )
    op_r1 = await _get_operation_pk(env, rev1_id, "GET", "/items")
    cred_id = await _seed_bound_credential(env, cred_id="cred_1081_spec_op")

    async with _control_client(env) as control, _broker_client(env) as broker:
        base = f"/credentials/{cred_id}/agents/{AGENT_ID}/permissions"
        put_resp = await control.put(
            base,
            json=[{"effect": "allow", "operations": ["get_items"]}],
        )
        assert put_resp.status_code == 200, put_resp.text

        # Live broker allows the matching OpenAPI operationId and denies unlisted ones.
        live_ok = await broker.get(f"/{HOST_A}/api/items")
        assert live_ok.status_code == 200, live_ok.text

        live_denied = await broker.post(f"/{HOST_A}/api/items")
        assert live_denied.status_code == 403, live_denied.text
        assert live_denied.json()["type"] == "action_denied"

        live_sub_denied = await broker.get(f"/{HOST_A}/api/items/42")
        assert live_sub_denied.status_code == 403, live_sub_denied.text

        # Dry-run agrees whether operation_id is explicit or resolved from (method, path).
        dry_explicit = (
            await control.post(
                f"{base}:test",
                json={"method": "GET", "path": "/items", "operation_id": "get_items"},
            )
        ).json()
        assert dry_explicit["allowed"] is True
        assert dry_explicit["matched"] is True

        dry_inferred = (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/items"})
        ).json()
        assert dry_inferred["allowed"] is True
        assert dry_inferred["matched"] is True

        # Forged/mismatched operation_id on a different method or unregistered path fails closed.
        dry_mismatch = (
            await control.post(
                f"{base}:test",
                json={"method": "POST", "path": "/items", "operation_id": "get_items"},
            )
        ).json()
        assert dry_mismatch["allowed"] is False

        dry_unregistered = (
            await control.post(
                f"{base}:test",
                json={"method": "GET", "path": "/unregistered", "operation_id": "get_items"},
            )
        ).json()
        assert dry_unregistered["allowed"] is False

        # Queued worker re-authorization agrees with the sync path (rebuilt from job payload).
        class _StubExecutor:
            async def execute(self, request: UpstreamExecRequest, *, db: Any) -> UpstreamExecResult:
                return UpstreamExecResult(
                    status_code=200, body=b"{}", content_type="application/json", duration_ms=1
                )

        class _StubInjector:
            async def inject(self, **kwargs: Any) -> InjectedAuth:
                return InjectedAuth(headers={}, query_params={}, cookies={})

        handler = ExecutionHandler(
            executor=_StubExecutor(),
            credential_injector=_StubInjector(),
            execution_authorizer=build_queued_execution_authorizer(env),
            actor_status=ActorStatusResolver(env.admin_db),
        )
        resolved_hit = await InProcessRegistryResolver(env.registry_db, ctx=env).resolve_operation(
            method="GET", url=f"https://{HOST_A}/api/items"
        )
        assert resolved_hit is not None
        job_result = await handler.execute(
            job_id="job_1081_queued",
            db=env.admin_db,
            created_by=AGENT_ID,
            actor_type="agent",
            payload={
                "execution_id": "exec_1081_queued",
                "upstream_url": f"https://{HOST_A}/api/items",
                "method": "GET",
                "api_vendor": VENDOR,
                "api_name": API_NAME,
                "api_version": VERSION,
                "credential_id": cred_id,
                "operation": resolved_hit.operation.model_dump(),
                "auth_token_sha256": TOKEN_SHA,
                "auth_method": "jwt",
                "auth_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert job_result.body["http_status"] == 200, job_result.body

        # A historical in-flight job payload carrying only OperationInfo.id (op_...)
        # resolves Operation.operation_id at re-authorization time too.
        legacy_job_result = await handler.execute(
            job_id="job_1081_queued_legacy",
            db=env.admin_db,
            created_by=AGENT_ID,
            actor_type="agent",
            payload={
                "execution_id": "exec_1081_queued_legacy",
                "upstream_url": f"https://{HOST_A}/api/items",
                "method": "GET",
                "api_vendor": VENDOR,
                "api_name": API_NAME,
                "api_version": VERSION,
                "credential_id": cred_id,
                "operation": {
                    "id": op_r1,
                    "path": "/items",
                    "method": "GET",
                    "relative_path": "/items",
                },
                "auth_token_sha256": TOKEN_SHA,
                "auth_method": "jwt",
                "auth_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert legacy_job_result.body["http_status"] == 200, legacy_job_result.body

        # Promote a new revision R2: mints a brand-new op_... PK for GET /items.
        rev2_id = await _import_and_promote(
            env,
            paths={
                "/items": {
                    "get": {
                        "operationId": "get_items",
                        "responses": {"200": {"description": "OK v2"}},
                    },
                    "post": {
                        "operationId": "create_item",
                        "responses": {"201": {"description": "Created"}},
                    },
                },
                "/items/{item_id}": {
                    "get": {
                        "operationId": "get_item",
                        "responses": {"200": {"description": "OK"}},
                    },
                    "delete": {
                        "operationId": "delete_item",
                        "responses": {"204": {"description": "Deleted"}},
                    },
                },
            },
        )
        op_r2 = await _get_operation_pk(env, rev2_id, "GET", "/items")
        assert op_r2 != op_r1

        # OpenAPI operationId rule continues to match R2 cleanly on both surfaces.
        live_after = await broker.get(f"/{HOST_A}/api/items")
        assert live_after.status_code == 200, live_after.text

        dry_after = (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/items"})
        ).json()
        assert dry_after["allowed"] is True

        delete_denied = await broker.delete(f"/{HOST_A}/api/items/42")
        assert delete_denied.status_code == 403, delete_denied.text


@pytest.mark.asyncio
async def test_existing_registry_op_id_rule_compatibility_and_revision_rollover(
    env: Context,
) -> None:
    """Pins #1081: existing ``op_…`` rules match that revision's operation on both
    live broker and ``permissions:test``, and fail closed on both once superseded."""
    rev1_id = await _import_and_promote(
        env,
        paths={
            "/items": {
                "get": {"operationId": "get_items", "responses": {"200": {"description": "OK"}}},
            },
        },
    )
    op_r1 = await _get_operation_pk(env, rev1_id, "GET", "/items")
    cred_id = await _seed_bound_credential(env, cred_id="cred_1081_reg_op")

    async with _control_client(env) as control, _broker_client(env) as broker:
        base = f"/credentials/{cred_id}/agents/{AGENT_ID}/permissions"
        put_resp = await control.put(
            base,
            json=[{"effect": "allow", "operations": [op_r1]}],
        )
        assert put_resp.status_code == 200, put_resp.text

        assert (await broker.get(f"/{HOST_A}/api/items")).status_code == 200
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/items"})
        ).json()["allowed"] is True
        assert (
            await control.post(
                f"{base}:test",
                json={"method": "GET", "path": "/items", "operation_id": "get_items"},
            )
        ).json()["allowed"] is True
        assert (
            await control.post(
                f"{base}:test",
                json={"method": "GET", "path": "/items", "operation_id": op_r1},
            )
        ).json()["allowed"] is True

        # Promote R2: op_r1 is superseded by op_r2, so both broker and dry-run fail closed.
        rev2_id = await _import_and_promote(
            env,
            paths={
                "/items": {
                    "get": {
                        "operationId": "get_items",
                        "summary": "v2",
                        "responses": {"200": {"description": "OK v2"}},
                    },
                },
            },
        )
        op_r2 = await _get_operation_pk(env, rev2_id, "GET", "/items")
        assert op_r2 != op_r1

        assert (await broker.get(f"/{HOST_A}/api/items")).status_code == 403
        assert (
            await control.post(
                f"{base}:test",
                json={"method": "GET", "path": "/items", "operation_id": op_r1},
            )
        ).json()["allowed"] is False
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/items"})
        ).json()["allowed"] is False


@pytest.mark.asyncio
async def test_ambiguous_operation_ids_across_covered_apis_fail_closed(
    env: Context,
) -> None:
    """Pins #1081: under a wildcard credential covering multiple live APIs, a colliding
    ``operationId`` fails closed on both live broker and ``permissions:test``, while
    unambiguous ``operationId``s and unrelated-vendor collisions behave accurately."""
    await _import_and_promote(
        env,
        vendor=VENDOR,
        name=BILLING_API,
        version=VERSION,
        server_url=f"https://{HOST_A}",
        paths={
            "/billing/records": {
                "get": {"operationId": "list_records", "responses": {"200": {"description": "OK"}}}
            },
            "/billing/invoices": {
                "get": {"operationId": "get_invoices", "responses": {"200": {"description": "OK"}}}
            },
            "/shared/{id}": {
                "get": {
                    "operationId": "get_billing_shared",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    )
    await _import_and_promote(
        env,
        vendor=VENDOR,
        name=ADMIN_API,
        version=VERSION,
        server_url=f"https://{HOST_B}",
        paths={
            "/admin/records": {
                "get": {"operationId": "list_records", "responses": {"200": {"description": "OK"}}}
            },
            "/shared/{id}": {
                "get": {
                    "operationId": "get_admin_shared",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    )
    # An unrelated vendor reusing get_invoices must NOT taint VENDOR's scope.
    await _import_and_promote(
        env,
        vendor=OTHER_VENDOR,
        name="other",
        version=VERSION,
        server_url=f"https://{HOST_OTHER}",
        paths={
            "/other/invoices": {
                "get": {"operationId": "get_invoices", "responses": {"200": {"description": "OK"}}}
            },
        },
    )

    # Vendor-wide credential covers both BILLING_API and ADMIN_API under VENDOR.
    cred_id = await _seed_bound_credential(
        env, cred_id="cred_1081_wildcard", vendor=VENDOR, name=None, version=None
    )

    async with _control_client(env) as control, _broker_client(env) as broker:
        base = f"/credentials/{cred_id}/agents/{AGENT_ID}/permissions"

        # 1. Colliding operationId "list_records" fails closed everywhere.
        await control.put(base, json=[{"effect": "allow", "operations": ["list_records"]}])
        assert (await broker.get(f"/{HOST_A}/billing/records")).status_code == 403
        assert (await broker.get(f"/{HOST_B}/admin/records")).status_code == 403
        assert (
            await control.post(
                f"{base}:test",
                json={
                    "method": "GET",
                    "path": "/billing/records",
                    "operation_id": "list_records",
                },
            )
        ).json()["allowed"] is False
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/billing/records"})
        ).json()["allowed"] is False

        # 2. Ambiguous require-approval or deny never falls through to a later allow.
        await control.put(
            base,
            json=[
                {"effect": "require-approval", "operations": ["list_records"]},
                {"effect": "allow", "methods": ["GET"], "path": "/.*", "match_mode": "regex"},
            ],
        )
        assert (await broker.get(f"/{HOST_A}/billing/records")).status_code == 403
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/billing/records"})
        ).json()["allowed"] is False

        await control.put(
            base,
            json=[
                {"effect": "deny", "operations": ["list_records"]},
                {"effect": "allow", "methods": ["GET"], "path": "/.*", "match_mode": "regex"},
            ],
        )
        assert (await broker.get(f"/{HOST_A}/billing/records")).status_code == 403
        dry_deny = (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/billing/records"})
        ).json()
        assert dry_deny["allowed"] is False
        assert dry_deny["effect"] == "deny"

        # 3. Unambiguous "get_invoices" within VENDOR works despite OTHER_VENDOR sharing the name.
        await control.put(base, json=[{"effect": "allow", "operations": ["get_invoices"]}])
        assert (await broker.get(f"/{HOST_A}/billing/invoices")).status_code == 200
        assert (await broker.get(f"/{HOST_OTHER}/other/invoices")).status_code == 403
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/billing/invoices"})
        ).json()["allowed"] is True

        # 4. Dry-run candidate ambiguity on /shared/42 (matches two covered APIs) fails closed
        #    when operation_id is omitted, and resolves when operation_id disambiguates.
        await control.put(base, json=[{"effect": "allow", "operations": ["get_billing_shared"]}])
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/shared/42"})
        ).json()["allowed"] is False
        assert (
            await control.post(
                f"{base}:test",
                json={
                    "method": "GET",
                    "path": "/shared/42",
                    "operation_id": "get_billing_shared",
                },
            )
        ).json()["allowed"] is True


@pytest.mark.asyncio
async def test_default_deny_no_substring_and_relative_path_parity_preserved(
    env: Context,
) -> None:
    """Pins #1081: empty rules default-deny, operation matching is exact (never
    substring), operationId-less operations fail closed against ``operations`` rules,
    and method+path rules preserve ``OperationInfo.relative_path`` parity."""
    await _import_and_promote(
        env,
        server_url=f"https://{HOST_A}/v2",
        paths={
            "/items/{item_id}": {
                "get": {"operationId": "get_item", "responses": {"200": {"description": "OK"}}},
            },
            "/anonymous": {
                "get": {"responses": {"200": {"description": "No operationId"}}},
            },
        },
    )
    cred_id = await _seed_bound_credential(env, cred_id="cred_1081_invariants")

    async with _control_client(env) as control, _broker_client(env) as broker:
        base = f"/credentials/{cred_id}/agents/{AGENT_ID}/permissions"

        # Default-deny with zero rules.
        assert (await broker.get(f"/{HOST_A}/v2/items/42")).status_code == 403
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/items/42"})
        ).json() == {
            "allowed": False,
            "matched": False,
            "effect": None,
            "rule_index": None,
            "credential_id": None,
            "is_system": None,
        }

        # Substring / prefix operationId does not match, and operationId-less op does not match.
        await control.put(base, json=[{"effect": "allow", "operations": ["item", "get"]}])
        assert (await broker.get(f"/{HOST_A}/v2/items/42")).status_code == 403
        assert (await broker.get(f"/{HOST_A}/v2/anonymous")).status_code == 403
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/items/42"})
        ).json()["allowed"] is False

        # Method + server-relative path rule parity (#1424) is preserved.
        await control.put(
            base,
            json=[
                {
                    "effect": "allow",
                    "methods": ["GET"],
                    "path": "/items/{item_id}",
                    "match_mode": "exact",
                }
            ],
        )
        assert (await broker.get(f"/{HOST_A}/v2/items/42")).status_code == 200
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/items/42"})
        ).json()["allowed"] is True
        assert (
            await control.post(f"{base}:test", json={"method": "GET", "path": "/v2/items/42"})
        ).json()["allowed"] is False
