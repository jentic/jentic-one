"""End-to-end integration tests for nested path matching (#701, #1422).

Exercises real SQLite and PostgreSQL databases across:
- OpenAPI ingestion (``Ingestor``) + already-ingested legacy URL-index rows
- SQL candidate lookup + single-statement resolution (``InProcessRegistryResolver``)
- Sibling route precedence, fail-closed ambiguity, and RFC 6570 ``{+path}`` coexistence
- Binding permission rules (``prefix``, ``regex``, ``exact``, ``operations``)
- Control ``permissions:test`` dry-run parity with broker enforcement
- Broker ``_handle`` execution + credential injection + unencoded slash forwarding
- Encoded-slash (``%2F`` / ``%2f``) and traversal (``..``, ``%2e%2e``, ``%2F..%2F``) safety.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, event, select, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.users import User
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.repos import AgentRuleEvaluator, CredentialBindingResolver
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.control.services.credentials.service import (
    CredentialService as ControlCredentialService,
)
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.core.url_index import URL_INDEX_FORMAT_MARKER
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.registry.web.app import create_app as create_registry_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS
from jentic_one.shared.context import Context
from jentic_one.shared.discovery.errors import AmbiguousMatchError, MethodNotAllowedError
from jentic_one.shared.models import ActorType, ApiRevisionSourceType, StoredCredentialType
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.wiring import InProcessRegistryResolver, install_broker_registry_resolver

pytestmark = pytest.mark.integration

_VENDOR = "github.com"
_API_NAME = "api.github.com"
_API_VERSION = "1.1.4"
_HOST = "api.github.com"
_GHE_HOST = "ghe.example.com"
_OWNER_ID = "usr_nested701_owner"
_AGENT_ID = "agnt_nested701_agent"
_CRED_ID = "cred_nested701_github"
_PAT_SECRET = "ghp_test_secret_701"  # pragma: allowlist secret


def _owner_identity() -> Identity:
    return Identity(
        sub=_OWNER_ID,
        email="owner701@test.local",
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


def _github_spec_content(*, ambiguous: bool = False) -> dict[str, Any]:
    paths: dict[str, Any] = {
        "/repos/{owner}/{repo}": {
            "get": {
                "operationId": "repos/get",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/repos/{owner}/{repo}/contents/{path}": {
            "get": {
                "operationId": "repos/get-content",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "path", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            },
            "put": {
                "operationId": "repos/create-or-update-file-contents",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "path", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            },
        },
        "/repos/{owner}/{repo}/contents/special": {
            "get": {
                "operationId": "repos/get-special-content",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/repos/{owner}/{repo}/items/{id}/meta": {
            "get": {
                "operationId": "repos/get-item-meta",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/repos/{owner}/{repo}/items/{path}": {
            "get": {
                "operationId": "repos/get-item-by-path",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "path", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/repos/{owner}/{repo}/actions/secrets": {
            "get": {
                "operationId": "actions/list-repo-secrets",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/files/{+path}": {
            "get": {
                "operationId": "files/get-reserved",
                "parameters": [
                    {"name": "path", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    if ambiguous:
        paths["/v1/docs/{topic}/{path}"] = {
            "get": {"operationId": "docs/by-topic", "responses": {"200": {"description": "OK"}}}
        }
        paths["/v1/{category}/files/{path}"] = {
            "get": {"operationId": "files/by-category", "responses": {"200": {"description": "OK"}}}
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": "GitHub v3 REST API", "version": _API_VERSION},
        "servers": [
            {"url": f"https://{_HOST}"},
            {"url": f"https://{_GHE_HOST}/api/v3"},
        ],
        "paths": paths,
    }


async def _wipe_all(ctx: Context) -> None:
    async with ctx.registry_db.session() as session:
        for reg_table in (
            OperationURLIndex,
            SecuritySchemeFlow,
            SecurityScheme,
            ServerVariable,
            Server,
            Operation,
            SpecFile,
        ):
            await session.execute(delete(reg_table))
        await session.execute(update(Api).values(current_revision_id=None))
        await session.execute(delete(ApiRevision))
        await session.execute(delete(Api))
        await session.commit()

    async with ctx.control_db.session() as session:
        await session.execute(
            delete(AgentPermissionRule).where(AgentPermissionRule.agent_id == _AGENT_ID)
        )
        await session.execute(
            delete(CustomerAPIKey).where(CustomerAPIKey.credential_id == _CRED_ID)
        )
        await session.execute(delete(Credential).where(Credential.id == _CRED_ID))
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


@pytest.fixture()
async def seeded_env(integration_context: Context) -> AsyncGenerator[dict[str, Any], None]:
    """Ingest + promote GitHub-shaped spec and seed bound agent + credential."""
    await _wipe_all(integration_context)

    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor=_VENDOR, name=_API_NAME, version=_API_VERSION),
        sha="sha-github-701",
        content=_github_spec_content(ambiguous=True),
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="github.json",
        submitted_by=_OWNER_ID,
    )
    ingest_res = await Ingestor(integration_context).ingest(spec, created_by=_OWNER_ID)
    await RevisionService(integration_context).promote(
        _VENDOR,
        _API_NAME,
        _API_VERSION,
        str(ingest_res.revision_id),
        identity=_owner_identity(),
    )

    async with integration_context.registry_db.session() as session:
        op_rows = (
            await session.execute(
                select(Operation.operation_id, Operation.id).where(
                    Operation.revision_id == ingest_res.revision_id
                )
            )
        ).all()
        ops_by_spec_id = {
            spec_op_id: op_id for spec_op_id, op_id in op_rows if spec_op_id is not None
        }

    async with integration_context.admin_db.session() as session:
        session.add(
            User(
                id=_OWNER_ID,
                email="owner701@test.local",
                first_name="Nested",
                last_name="Owner",
                active=True,
                created_by=_OWNER_ID,
            )
        )
        await session.flush()
        session.add(
            Agent(
                id=_AGENT_ID,
                name="nested-path-agent",
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
                credential_id=_CRED_ID,
                created_by=_OWNER_ID,
            )
        )
        await session.commit()

    async with integration_context.control_db.session() as session:
        session.add(
            Credential(
                id=_CRED_ID,
                type=StoredCredentialType.API_KEY,
                name="github-pat",
                api_vendor=_VENDOR,
                api_name=_API_NAME,
                api_version=_API_VERSION,
                active=True,
                state="connected",
                created_by=_OWNER_ID,
            )
        )
        await session.flush()
        session.add(
            CustomerAPIKey(
                id="key_nested701_github",
                credential_id=_CRED_ID,
                encrypted_key=integration_context.encryption.encrypt(f"Bearer {_PAT_SECRET}"),
                location="header",
                field_name="Authorization",
            )
        )
        await session.commit()

    yield {
        "ctx": integration_context,
        "revision_id": ingest_res.revision_id,
        "ops": ops_by_spec_id,
    }

    await _wipe_all(integration_context)


async def _set_rules(
    ctx: Context, evaluator: AgentRuleEvaluator, rules: list[dict[str, Any]]
) -> None:
    async with ctx.control_db.session() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session, _AGENT_ID, _CRED_ID, rules, created_by=_OWNER_ID
        )
        await session.commit()
    evaluator.clear()


@asynccontextmanager
async def _broker_client(
    ctx: Context,
) -> AsyncIterator[tuple[httpx.AsyncClient, AgentRuleEvaluator, list[httpx.Request]]]:
    """Mount real broker ``_handle`` with MockTransport recording forwarded upstream requests."""
    captured: list[httpx.Request] = []

    def _mock_upstream(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(
                {
                    "forwarded_url": str(req.url),
                    "raw_path": req.url.raw_path.decode("ascii"),
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

    public_ip = (ipaddress.ip_address("140.82.121.6"),)
    with patch(
        "jentic_one.shared.url_validation.resolve_host",
        new=AsyncMock(return_value=public_ip),
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_upstream)) as upstream:
            runner = HttpRunner(upstream)

            @app.api_route("/{upstream_url:path}", methods=["GET", "PUT", "POST", "DELETE"])
            async def proxy(upstream_url: str, request: Request) -> Response:
                return await _handle(
                    request, request.method, ctx, identity, deriver, evaluator, runner, None
                )

            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
                yield client, evaluator, captured


@pytest.mark.asyncio
async def test_resolver_matches_nested_github_paths_in_single_sql_statement(
    seeded_env: dict[str, Any],
) -> None:
    """Pins #701 / #1422: nested ``contents/{path}`` resolves in one SQL round-trip."""
    ctx: Context = seeded_env["ctx"]
    ops: dict[str, str] = seeded_env["ops"]
    resolver = InProcessRegistryResolver(ctx.registry_db, ctx=ctx)

    statements: list[str] = []

    def _capture(_conn: object, _cur: object, stmt: str, *_rest: object) -> None:
        statements.append(stmt)

    sync_engine = ctx.registry_db.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        resolved = await resolver.resolve_operation(
            method="GET",
            url="https://api.github.com/repos/octocat/Hello-World/contents/docs/guide/intro.md",
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    assert len(statements) == 1, statements
    assert resolved is not None
    assert resolved.operation.id == ops["repos/get-content"]
    assert resolved.operation.path == "/repos/{owner}/{repo}/contents/{path}"
    assert (
        resolved.operation.relative_path
        == "/repos/octocat/Hello-World/contents/docs/guide/intro.md"
    )
    assert resolved.path_params == {
        "owner": "octocat",
        "repo": "Hello-World",
        "path": "docs/guide/intro.md",
    }

    # Base-path server (GitHub Enterprise /api/v3) strips /api/v3 from relative_path.
    ghe_resolved = await resolver.resolve_operation(
        method="GET",
        url=f"https://{_GHE_HOST}/api/v3/repos/octocat/Hello-World/contents/docs/guide/intro.md",
    )
    assert ghe_resolved is not None
    assert ghe_resolved.operation.id == ops["repos/get-content"]
    assert (
        ghe_resolved.operation.relative_path
        == "/repos/octocat/Hello-World/contents/docs/guide/intro.md"
    )


@pytest.mark.asyncio
async def test_legacy_pre_marker_url_index_row_resolves_nested_paths(
    seeded_env: dict[str, Any],
) -> None:
    """Rows predating ``URL_INDEX_FORMAT_MARKER`` resolve nested paths without re-import."""
    ctx: Context = seeded_env["ctx"]
    ops: dict[str, str] = seeded_env["ops"]

    # Downgrade the stored ``path_regex`` on the live ``contents/{path}`` row to the
    # pre-marker format held by older deployments.
    async with ctx.registry_db.session() as session:
        rows = (
            (
                await session.execute(
                    select(OperationURLIndex).where(
                        OperationURLIndex.operation_id == ops["repos/get-content"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows
        for row in rows:
            row.path_regex = row.path_regex.removeprefix(URL_INDEX_FORMAT_MARKER)
        await session.commit()

    resolver = InProcessRegistryResolver(ctx.registry_db, ctx=ctx)
    resolved = await resolver.resolve_operation(
        method="GET",
        url="https://api.github.com/repos/octocat/Hello-World/contents/a/b/c.txt",
    )
    assert resolved is not None
    assert resolved.operation.id == ops["repos/get-content"]
    assert resolved.path_params == {
        "owner": "octocat",
        "repo": "Hello-World",
        "path": "a/b/c.txt",
    }
    assert resolved.operation.relative_path == "/repos/octocat/Hello-World/contents/a/b/c.txt"
    assert resolved.server_variables_unresolved is False


@pytest.mark.asyncio
async def test_precedence_rfc6570_and_ambiguity_on_real_db(seeded_env: dict[str, Any]) -> None:
    """Sibling precedence, RFC 6570 ``{+path}``, and fail-closed ambiguity work over SQL."""
    ctx: Context = seeded_env["ctx"]
    ops: dict[str, str] = seeded_env["ops"]
    resolver = InProcessRegistryResolver(ctx.registry_db, ctx=ctx)

    # Static sibling beats trailing {path} on exact 5-segment path.
    special = await resolver.resolve_operation(
        method="GET",
        url="https://api.github.com/repos/octocat/Hello-World/contents/special",
    )
    assert special is not None and special.operation.id == ops["repos/get-special-content"]

    # Single-segment sibling /items/{id}/meta beats greedy /items/{path}.
    item_meta = await resolver.resolve_operation(
        method="GET",
        url="https://api.github.com/repos/octocat/Hello-World/items/99/meta",
    )
    assert item_meta is not None and item_meta.operation.id == ops["repos/get-item-meta"]

    # Deeper path under /items/... falls through to greedy /items/{path}.
    item_nested = await resolver.resolve_operation(
        method="GET",
        url="https://api.github.com/repos/octocat/Hello-World/items/99/meta/sub",
    )
    assert item_nested is not None and item_nested.operation.id == ops["repos/get-item-by-path"]

    # RFC 6570 {+path} resolves both single-segment and multi-segment paths from SQL.
    for file_path in ("single.txt", "nested/dir/file.txt"):
        reserved = await resolver.resolve_operation(
            method="GET",
            url=f"https://api.github.com/files/{file_path}",
        )
        assert reserved is not None and reserved.operation.id == ops["files/get-reserved"]
        assert reserved.path_params == {"path": file_path}

    # Ambiguous greedy templates fail closed with AmbiguousMatchError.
    with pytest.raises(AmbiguousMatchError):
        await resolver.resolve_operation(
            method="GET",
            url="https://api.github.com/v1/docs/files/guide/intro.md",
        )

    # Pinned lookup with wrong HTTP method on a nested path raises MethodNotAllowedError.
    with pytest.raises(MethodNotAllowedError) as mna_exc:
        await resolver.resolve_operation(
            method="DELETE",
            url="https://api.github.com/repos/octocat/Hello-World/contents/docs/guide/intro.md",
            revision_id=seeded_env["revision_id"],
        )
    assert mna_exc.value.allowed_methods == ["GET", "PUT"]


@pytest.mark.asyncio
async def test_end_to_end_broker_execution_rules_and_permissions_test(
    seeded_env: dict[str, Any],
) -> None:
    """End-to-end nested URL through discovery, rules, broker, and ``permissions:test``."""
    ctx: Context = seeded_env["ctx"]
    ops: dict[str, str] = seeded_env["ops"]
    content_op_id = ops["repos/get-content"]
    nested_rel = "/repos/octocat/Hello-World/contents/docs/guide/intro.md"
    single_rel = "/repos/octocat/Hello-World/contents/README.md"
    control_svc = ControlCredentialService(ctx)

    # Verify Registry ``GET /inspect`` resolves the nested GitHub URL over HTTP.
    reg_app = create_registry_app(ctx)
    reg_app.dependency_overrides[resolve_identity] = _owner_identity
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=reg_app, raise_app_exceptions=False),
            base_url="http://registry",
        ) as reg_client:
            inspect_resp = await reg_client.get(
                "/inspect",
                params={"id": f"GET https://{_HOST}{nested_rel}", "detail": "summary"},
            )
        assert inspect_resp.status_code == 200, inspect_resp.text
        assert inspect_resp.json()["operation_id"] == content_op_id
    finally:
        reg_app.dependency_overrides.clear()

    # Mount Control HTTP app to exercise ``POST .../permissions:test`` over HTTP too.
    @asynccontextmanager
    async def _noop_lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield

    ctrl_app = create_control_app(ctx)
    ctrl_app.router.lifespan_context = _noop_lifespan
    ctrl_app.dependency_overrides[resolve_identity] = _owner_identity

    try:
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=ctrl_app, raise_app_exceptions=False),
                base_url="http://control",
            ) as ctrl_client,
            _broker_client(ctx) as (broker, evaluator, captured),
        ):
            # 1. Prefix rule with template placeholders allows nested path.
            await _set_rules(
                ctx,
                evaluator,
                [
                    {
                        "effect": "allow",
                        "methods": ["GET"],
                        "path": "/repos/{owner}/{repo}/contents/{path}",
                        "match_mode": "prefix",
                    }
                ],
            )
            dry_http = await ctrl_client.post(
                f"/credentials/{_CRED_ID}/agents/{_AGENT_ID}/permissions:test",
                json={"method": "GET", "path": nested_rel, "operation_id": content_op_id},
            )
            assert dry_http.status_code == 200, dry_http.text
            assert dry_http.json()["allowed"] is True
            assert dry_http.json()["matched"] is True

            resp = await broker.get(
                f"/https://{_HOST}{nested_rel}",
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["raw_path"] == nested_rel
            assert payload["auth"] == f"Bearer {_PAT_SECRET}"
            assert len(captured) == 1

            # Base-path server (/api/v3) also evaluates rules on the spec-relative nested path.
            ghe_resp = await broker.get(f"/https://{_GHE_HOST}/api/v3{nested_rel}")
            assert ghe_resp.status_code == 200, ghe_resp.text
            assert ghe_resp.json()["raw_path"] == f"/api/v3{nested_rel}"

            # 2. Exact rule with {path} placeholder stays single-segment:
            # allows README.md, denies nested path.
            await _set_rules(
                ctx,
                evaluator,
                [
                    {
                        "effect": "allow",
                        "methods": ["GET"],
                        "path": "/repos/{owner}/{repo}/contents/{path}",
                        "match_mode": "exact",
                    }
                ],
            )
            dry_single = await control_svc.test_agent_permissions(
                _CRED_ID,
                _AGENT_ID,
                method="GET",
                path=single_rel,
                operation_id=content_op_id,
                identity=_owner_identity(),
            )
            dry_nested = await ctrl_client.post(
                f"/credentials/{_CRED_ID}/agents/{_AGENT_ID}/permissions:test",
                json={"method": "GET", "path": nested_rel, "operation_id": content_op_id},
            )
            assert dry_single.allowed is True
            assert dry_nested.status_code == 200 and dry_nested.json()["allowed"] is False
            assert (await broker.get(f"/https://{_HOST}{single_rel}")).status_code == 200
            denied_nested = await broker.get(f"/https://{_HOST}{nested_rel}")
            assert denied_nested.status_code == 403
            assert denied_nested.json()["type"] == "action_denied"

            # 3. Regex rule ``.../contents/.*`` allows nested path, denies other repos.
            await _set_rules(
                ctx,
                evaluator,
                [
                    {
                        "effect": "allow",
                        "methods": ["GET"],
                        "path": r"/repos/octocat/Hello-World/contents/.*",
                        "match_mode": "regex",
                    }
                ],
            )
            assert (await broker.get(f"/https://{_HOST}{nested_rel}")).status_code == 200
            other_repo = await broker.get(
                f"/https://{_HOST}/repos/octocat/other-repo/contents/docs/guide/intro.md"
            )
            assert other_repo.status_code == 403

            # 4. Operation-scoped rule (operations=[op_id], path=None) allows nested path.
            await _set_rules(
                ctx,
                evaluator,
                [
                    {
                        "effect": "allow",
                        "methods": ["GET"],
                        "path": None,
                        "operations": [content_op_id],
                    }
                ],
            )
            assert (await broker.get(f"/https://{_HOST}{nested_rel}")).status_code == 200

            # 5. Ambiguous greedy match surfaces 409 ambiguous_match over broker HTTP.
            ambig_resp = await broker.get(f"/https://{_HOST}/v1/docs/files/guide/intro.md")
            assert ambig_resp.status_code == 409
            assert ambig_resp.json()["type"] == "ambiguous_match"
    finally:
        ctrl_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_encoded_slashes_and_traversal_safety_end_to_end(
    seeded_env: dict[str, Any],
) -> None:
    """Encoded slashes stay encoded and traversal inputs cannot bypass path rules."""
    ctx: Context = seeded_env["ctx"]
    control_svc = ControlCredentialService(ctx)

    async with _broker_client(ctx) as (broker, evaluator, captured):
        # Allow files under ``.../contents/docs/`` (trailing slash), deny ``.../docs/secret``.
        await _set_rules(
            ctx,
            evaluator,
            [
                {
                    "effect": "deny",
                    "methods": ["GET"],
                    "path": "/repos/octocat/Hello-World/contents/docs/secret",
                    "match_mode": "prefix",
                },
                {
                    "effect": "allow",
                    "methods": ["GET"],
                    "path": "/repos/octocat/Hello-World/contents/docs/",
                    "match_mode": "prefix",
                },
            ],
        )

        # 1. Encoded slash ``docs%2Fguide.md`` / ``docs%2fguide.md`` is NOT decoded
        # to ``docs/guide.md``: it does not match prefix ``.../contents/docs/``.
        for encoded in ("docs%2Fguide.md", "docs%2fguide.md"):
            enc_path = f"/repos/octocat/Hello-World/contents/{encoded}"
            dry_enc = await control_svc.test_agent_permissions(
                _CRED_ID,
                _AGENT_ID,
                method="GET",
                path=enc_path,
                operation_id=None,
                identity=_owner_identity(),
            )
            assert dry_enc.allowed is False
            enc_resp = await broker.get(f"/https://{_HOST}{enc_path}")
            assert enc_resp.status_code == 403

        # Under a broader prefix ``.../contents/``, ``%2f`` is forwarded raw without decoding.
        await _set_rules(
            ctx,
            evaluator,
            [
                {
                    "effect": "deny",
                    "methods": ["GET"],
                    "path": "/repos/octocat/Hello-World/contents/secret",
                    "match_mode": "prefix",
                },
                {
                    "effect": "allow",
                    "methods": ["GET"],
                    "path": "/repos/octocat/Hello-World/contents/",
                    "match_mode": "prefix",
                },
            ],
        )
        captured.clear()
        raw_enc_resp = await broker.get(
            f"/https://{_HOST}/repos/octocat/Hello-World/contents/docs%2fintro.md"
        )
        assert raw_enc_resp.status_code == 200, raw_enc_resp.text
        assert (
            raw_enc_resp.json()["raw_path"] == "/repos/octocat/Hello-World/contents/docs%2fintro.md"
        )
        assert len(captured) == 1

        # 2. Plain dot-segment traversal out of ``/contents/`` or into ``/secret`` is denied.
        captured.clear()
        for traversal in (
            "/repos/octocat/Hello-World/contents/docs/../../actions/secrets",
            "/repos/octocat/Hello-World/contents/public/../secret/credentials.json",
        ):
            dry_trav = await control_svc.test_agent_permissions(
                _CRED_ID,
                _AGENT_ID,
                method="GET",
                path=traversal,
                operation_id=None,
                identity=_owner_identity(),
            )
            assert dry_trav.allowed is False
            trav_resp = await broker.get(f"/https://{_HOST}{traversal}")
            assert trav_resp.status_code == 403
        assert captured == []

        # 3. Encoded dot-segment or encoded-separator traversal is refused even under ``.*``.
        await _set_rules(
            ctx,
            evaluator,
            [{"effect": "allow", "methods": ["GET"], "path": ".*", "match_mode": "regex"}],
        )
        captured.clear()
        for ambig_traversal in (
            "/repos/octocat/Hello-World/contents/public/%2e%2e/secret/key.txt",
            "/repos/octocat/Hello-World/contents/public%2F..%2Fsecret/key.txt",
            "/repos/octocat/Hello-World/contents/public%2f..%2fsecret/key.txt",
        ):
            dry_ambig = await control_svc.test_agent_permissions(
                _CRED_ID,
                _AGENT_ID,
                method="GET",
                path=ambig_traversal,
                operation_id=None,
                identity=_owner_identity(),
            )
            assert dry_ambig.allowed is False
            ambig_resp = await broker.get(f"/https://{_HOST}{ambig_traversal}")
            assert ambig_resp.status_code == 403
            assert ambig_resp.json()["type"] == "action_denied"
        assert captured == []
