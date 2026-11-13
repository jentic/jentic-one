"""End-to-end integration tests for trusted-issuer async job and result links (#1807).

Pins that an external-JWKS (``broker.jwt_verification.trusted_issuers``) agent
accepted for async execution (``202 Accepted``) receives ``_links.self`` and
``_links.result`` URLs on the broker data plane that it can follow with its
external JWT across ``completed`` and ``failed`` jobs (#2030), while foreign
actors receive ``404`` anti-enumeration and platform credentials (``jak_`` /
``at_``) keep working across both surfaces.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos.access_token_repo import AccessTokenRepository
from jentic_one.admin.repos.actor_permission_grant_repo import ActorPermissionGrantRepository
from jentic_one.admin.repos.agent_credential_repo import AgentCredentialRepository
from jentic_one.admin.web.app import create_app as create_admin_app
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.setup import (
    build_queued_execution_authorizer,
    build_worker_actor_status,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.execution.executor import PipelineExecutor
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.broker.web.deps import get_http_runner
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    BROKER_EXECUTE_PERMISSION,
    DEFAULT_AGENT_PERMISSIONS,
)
from jentic_one.shared.config import AppConfig, BrokerConfig, EgressConfig, WorkerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.hold import PROBLEM_CONTENT_TYPE
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import (
    ActorType,
    ApiRevisionSourceType,
    JobKind,
    JobStatus,
    StoredCredentialType,
)
from jentic_one.wiring import install_broker_registry_resolver

pytestmark = pytest.mark.integration

_ISS = "https://idp.trusted-1807.local"
_AUD = "jentic-broker-1807"
_KID = "kid-1807"
_VENDOR = "acme1807.example.com"
_API_NAME = "widgets"
_API_VERSION = "1.0.0"
_UPSTREAM_HOST = "api.acme1807.example.com"
_OWNER_ID = "usr_1807_owner"
_AGENT_ID = "agnt_1807_jwks"
_OTHER_AGENT_ID = "agnt_1807_other"
_CRED_ID = "cred_1807_widgets"
_JAK_PLAINTEXT = "jak_live_1807_test_agent_key_000001"  # pragma: allowlist secret
_AT_PLAINTEXT = "at_1807_test_opaque_access_token_01"  # pragma: allowlist secret


class _JwksServer:
    """Serves a single JWKS document over loopback HTTP."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        body = json.dumps(jwks).encode()

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/jwks.json"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _JwksServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture()
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture()
def jwks_server(signing_key: ec.EllipticCurvePrivateKey) -> Iterator[_JwksServer]:
    public_jwk = json.loads(ECAlgorithm.to_jwk(signing_key.public_key()))
    public_jwk |= {"kid": _KID, "alg": "ES256", "use": "sig"}
    with _JwksServer({"keys": [public_jwk]}) as server:
        yield server


def _mint_jwks_token(
    key: ec.EllipticCurvePrivateKey,
    *,
    sub: str = _AGENT_ID,
    iss: str = _ISS,
    scopes: list[str] | None = None,
) -> str:
    claims = {
        "iss": iss,
        "aud": _AUD,
        "sub": sub,
        "exp": int(time.time()) + 300,
        "actor_type": ActorType.AGENT.value,
        "scopes": [BROKER_EXECUTE_PERMISSION] if scopes is None else scopes,
    }
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": _KID})


def _build_broker_config(jwks_url: str, *, jobs_api_base_url: str | None) -> BrokerConfig:
    raw: dict[str, Any] = {
        "resolve_cache_ttl_seconds": 0,
        "rule_cache_ttl_s": 0,
        "toolkit_cache_ttl_s": 0,
        "egress": {"allowed_private_subnets": ["127.0.0.0/8"]},
        "jwt_verification": {
            "audience": _AUD,
            "trusted_issuers": [{"issuer": _ISS, "jwks_url": jwks_url}],
        },
    }
    if jobs_api_base_url is not None:
        raw["jobs_api_base_url"] = jobs_api_base_url
    with patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False):
        return BrokerConfig.model_validate(raw)


def _openapi_spec() -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Acme Widgets", "version": _API_VERSION},
        "servers": [{"url": f"https://{_UPSTREAM_HOST}"}],
        "components": {
            "securitySchemes": {
                "BearerToken": {"type": "http", "scheme": "bearer"},
            }
        },
        "security": [{"BearerToken": []}],
        "paths": {
            "/v1/widgets": {
                "post": {
                    "operationId": "createWidget",
                    "responses": {"201": {"description": "Created"}},
                },
                "get": {
                    "operationId": "listWidgets",
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
    }


async def _cleanup_seeded(ctx: Context) -> None:
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(JobResult))
        await session.execute(delete(Job).where(Job.created_by.in_([_AGENT_ID, _OTHER_AGENT_ID])))
        await session.execute(
            delete(ExecutionRecord).where(
                ExecutionRecord.actor_id.in_([_AGENT_ID, _OTHER_AGENT_ID])
            )
        )
        await session.execute(
            delete(Event).where(Event.actor_id.in_([_AGENT_ID, _OTHER_AGENT_ID, _OWNER_ID]))
        )
        await session.execute(
            delete(AgentCredentialBinding).where(
                AgentCredentialBinding.agent_id.in_([_AGENT_ID, _OTHER_AGENT_ID])
            )
        )
        await session.execute(
            delete(AgentCredential).where(
                AgentCredential.agent_id.in_([_AGENT_ID, _OTHER_AGENT_ID])
            )
        )
        await session.execute(
            delete(AccessToken).where(AccessToken.actor_id.in_([_AGENT_ID, _OTHER_AGENT_ID]))
        )
        await session.execute(
            delete(ActorPermissionGrant).where(
                ActorPermissionGrant.actor_id.in_([_AGENT_ID, _OTHER_AGENT_ID])
            )
        )
        await session.execute(delete(Agent).where(Agent.id.in_([_AGENT_ID, _OTHER_AGENT_ID])))
        await session.execute(delete(User).where(User.id == _OWNER_ID))

    async with ctx.control_db.transaction() as session:
        await session.execute(
            delete(AgentPermissionRule).where(
                AgentPermissionRule.agent_id.in_([_AGENT_ID, _OTHER_AGENT_ID])
            )
        )
        await session.execute(
            delete(CustomerAPIKey).where(CustomerAPIKey.credential_id == _CRED_ID)
        )
        await session.execute(delete(Credential).where(Credential.id == _CRED_ID))

    async with ctx.registry_db.transaction() as session:
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


async def _seed_world(ctx: Context) -> None:
    owner_identity = Identity(
        sub=_OWNER_ID,
        email="owner1807@test.local",
        permissions=["org:admin", "apis:write", "credentials:write"],
    )
    # 1) Owner + two active agents + grants + jak_ key + at_ token.
    async with ctx.admin_db.transaction() as session:
        session.add(
            User(
                id=_OWNER_ID,
                email="owner1807@test.local",
                first_name="Owner",
                last_name="Test",
                active=True,
                created_by=_OWNER_ID,
            )
        )
        await session.flush()
        for aid in (_AGENT_ID, _OTHER_AGENT_ID):
            session.add(
                Agent(
                    id=aid,
                    name=f"agent-{aid}",
                    owner_id=_OWNER_ID,
                    registered_by=_OWNER_ID,
                    created_by=_OWNER_ID,
                    status="active",
                )
            )
            await session.flush()
            for perm in DEFAULT_AGENT_PERMISSIONS:
                await ActorPermissionGrantRepository.grant(
                    session,
                    actor_id=aid,
                    actor_type=ActorType.AGENT.value,
                    permission=perm,
                    created_by=_OWNER_ID,
                )
        await AgentCredentialRepository.create(
            session,
            agent_id=_AGENT_ID,
            api_key_hash=hashlib.sha256(_JAK_PLAINTEXT.encode()).hexdigest(),
            created_by=_OWNER_ID,
        )
        await AccessTokenRepository.create(
            session,
            token_hash=hashlib.sha256(_AT_PLAINTEXT.encode()).hexdigest(),
            actor_id=_AGENT_ID,
            actor_type=ActorType.AGENT.value,
            scopes=list(DEFAULT_AGENT_PERMISSIONS),
            token_family_id="fam_1807",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_by=_AGENT_ID,
            is_ephemeral=False,
        )
        session.add(
            AgentCredentialBinding(
                agent_id=_AGENT_ID,
                credential_id=_CRED_ID,
                created_by=_OWNER_ID,
            )
        )

    # 2) Ingest and promote the upstream API spec in the registry DB.
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor=_VENDOR, name=_API_NAME, version=_API_VERSION),
        sha="sha-widgets-1807",
        content=_openapi_spec(),
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="openapi.json",
        submitted_by=_OWNER_ID,
    )
    ingest_res = await Ingestor(ctx).ingest(spec, created_by=_OWNER_ID)
    await RevisionService(ctx).promote(
        _VENDOR,
        _API_NAME,
        _API_VERSION,
        str(ingest_res.revision_id),
        identity=owner_identity,
    )

    # 3) Store an encrypted bearer credential and bind _AGENT_ID with an ALLOW rule.
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=_CRED_ID,
                type=StoredCredentialType.API_KEY,
                name="acme-prod-token",
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
                id="key_1807_widgets",
                credential_id=_CRED_ID,
                encrypted_key=ctx.encryption.encrypt("Bearer upstream-secret-1807"),
                location="header",
                field_name="Authorization",
            )
        )
        await AgentPermissionRuleRepository.replace_user_rules(
            session,
            _AGENT_ID,
            _CRED_ID,
            [
                {
                    "effect": "allow",
                    "methods": ["GET", "POST"],
                    "path_pattern": "/v1/**",
                }
            ],
            created_by=_OWNER_ID,
        )


class _TwoPlaneRouter:
    """Routes full URLs to the standalone broker or standalone admin ASGI app by host."""

    def __init__(self, broker_client: httpx.AsyncClient, admin_client: httpx.AsyncClient) -> None:
        self.broker = broker_client
        self.admin = admin_client

    async def get(self, url: str, *, token: str | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        parsed = urlsplit(url)
        path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if parsed.netloc == "admin.local":
            return await self.admin.get(path_and_query, headers=headers)
        if parsed.netloc in ("broker.local", ""):
            return await self.broker.get(path_and_query, headers=headers)
        raise AssertionError(f"Unexpected link host in {url!r}")


@asynccontextmanager
async def _running_planes(
    integration_config: AppConfig,
    jwks_url: str,
    *,
    jobs_api_base_url: str | None,
    upstream_status: int = 201,
    upstream_body: bytes = b'{"id":"w_1","created":true}',
    upstream_content_type: str = "application/json",
) -> AsyncGenerator[tuple[Context, _TwoPlaneRouter, WorkerLoop], None]:
    broker_cfg = _build_broker_config(jwks_url, jobs_api_base_url=jobs_api_base_url)
    config = integration_config.model_copy(update={"broker": broker_cfg})

    def _mock_upstream(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("authorization") == "Bearer upstream-secret-1807"
        return httpx.Response(
            upstream_status,
            content=upstream_body,
            headers={"Content-Type": upstream_content_type},
        )

    async with (
        Context(config) as ctx,
        httpx.AsyncClient(transport=httpx.MockTransport(_mock_upstream)) as outbound_http,
    ):
        await _cleanup_seeded(ctx)
        await _seed_world(ctx)
        try:
            http_runner = HttpRunner(outbound_http)
            runner_registry = RunnerRegistry()
            runner_registry.register(["http", "https"], http_runner, required=True)

            broker_app = create_broker_app(ctx)
            install_broker_registry_resolver(broker_app, ctx)
            broker_app.state.broker_runner_registry = runner_registry
            broker_app.dependency_overrides[get_http_runner] = lambda: http_runner

            admin_app = create_admin_app(ctx)

            exec_handler = ExecutionHandler(
                executor=PipelineExecutor(runner_registry),
                upstream_timeout_s=ctx.config.broker.upstream_timeout_s,
                credential_injector=CredentialService(ctx),
                execution_authorizer=build_queued_execution_authorizer(ctx),
                actor_status=build_worker_actor_status(ctx),
                egress=EgressConfig(allowed_private_subnets=["127.0.0.0/8"]),
                security_config=ctx.config.security,
                encryption=ctx.encryption if ctx.has_encryption_keyset else None,
            )
            handlers = JobHandlerRegistry()
            handlers.register(JobKind.EXECUTION, exec_handler)
            worker = WorkerLoop(
                ctx.admin_db,
                handlers,
                worker_config=WorkerConfig(),
                approved_result_retention_seconds=ctx.config.held_executions.result_retention_seconds,
            )

            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=broker_app),
                    base_url="http://broker.local",
                ) as broker_client,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=admin_app),
                    base_url="http://admin.local",
                ) as admin_client,
            ):
                yield ctx, _TwoPlaneRouter(broker_client, admin_client), worker
        finally:
            await _cleanup_seeded(ctx)


@pytest.mark.parametrize("jobs_api_base_url", ["http://admin.local", None])
async def test_trusted_issuer_async_202_poll_and_completed_result_e2e(
    integration_config: AppConfig,
    jwks_server: _JwksServer,
    signing_key: ec.EllipticCurvePrivateKey,
    jobs_api_base_url: str | None,
) -> None:
    """A trusted-JWKS agent follows 202 -> _links.self -> _links.result end-to-end."""
    token = _mint_jwks_token(signing_key)
    async with _running_planes(
        integration_config,
        jwks_server.url,
        jobs_api_base_url=jobs_api_base_url,
    ) as (_ctx, planes, worker):
        # Control plane continues to reject broker-only external JWKS tokens.
        admin_probe = await planes.admin.get("/jobs", headers={"Authorization": f"Bearer {token}"})
        assert admin_probe.status_code == 401

        # 1) Submit async execution on the broker with the external-JWKS token.
        accepted = await planes.broker.post(
            f"/{_UPSTREAM_HOST}/v1/widgets",
            content=b'{"name":"sprocket"}',
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "respond-async",
            },
        )
        assert accepted.status_code == 202, accepted.text
        queued_body = accepted.json()
        job_id = queued_body["job_id"]
        self_url = queued_body["_links"]["self"]

        # 2) Follow _links.self with the external-JWKS token while still queued: 200 OK.
        pre_poll = await planes.get(self_url, token=token)
        assert pre_poll.status_code == 200, (
            f"following _links.self ({self_url}) returned {pre_poll.status_code}: {pre_poll.text}"
        )
        assert self_url == f"http://broker.local/jobs/{job_id}"
        pre_json = pre_poll.json()
        assert pre_json["job_id"] == job_id
        assert pre_json["kind"] == "execution"
        assert pre_json["status"] == "queued"
        assert pre_json["actor_id"] == _AGENT_ID
        assert pre_json["actor_type"] == "agent"
        assert pre_json["_links"]["self"] == f"http://broker.local/jobs/{job_id}"
        assert pre_json["_links"]["result"] is None

        early_res = await planes.get(f"http://broker.local/jobs/{job_id}/result", token=token)
        assert early_res.status_code == 409
        assert early_res.json()["type"] == "job_not_completed"

        # 3) Process the queued job on the worker and poll _links.self -> _links.result.
        assert await worker._tick() is True

        done_poll = await planes.get(self_url, token=token)
        assert done_poll.status_code == 200, done_poll.text
        done_json = done_poll.json()
        assert done_json["status"] == "completed"
        assert done_json["_links"]["self"] == f"http://broker.local/jobs/{job_id}"
        result_url = done_json["_links"]["result"]
        assert result_url == f"http://broker.local/jobs/{job_id}/result"
        expected_exec_origin = jobs_api_base_url or "http://broker.local"
        assert done_json["_links"]["execution"] == (
            f"{expected_exec_origin}/executions/{done_json['execution_id']}"
        )

        result_resp = await planes.get(result_url, token=token)
        assert result_resp.status_code == 200, result_resp.text
        assert result_resp.headers["content-type"].startswith("application/json")
        result_json = result_resp.json()
        assert result_json["status"] == "completed"
        assert result_json["http_status"] == 201
        assert json.loads(base64.b64decode(result_json["body_b64"])) == {
            "id": "w_1",
            "created": True,
        }


async def test_trusted_issuer_async_failed_jobs_preserve_result_and_error_contracts(
    integration_config: AppConfig,
    jwks_server: _JwksServer,
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    """Trusted-JWKS polling preserves #2030 failed-job results, 409 no-result, and 410 expiry."""
    token = _mint_jwks_token(signing_key)
    async with _running_planes(
        integration_config,
        jwks_server.url,
        jobs_api_base_url="http://admin.local",
        upstream_status=502,
        upstream_body=b'{"error":"bad_gateway"}',
    ) as (ctx, planes, worker):
        # 1) End-to-end 202 -> worker upstream 502 failure -> poll -> terminal failed result.
        accepted = await planes.broker.get(
            f"/{_UPSTREAM_HOST}/v1/widgets",
            headers={
                "Authorization": f"Bearer {token}",
                "Prefer": "respond-async",
            },
        )
        assert accepted.status_code == 202
        upstream_fail_job_id = accepted.json()["job_id"]
        assert await worker._tick() is True

        polled = await planes.get(accepted.json()["_links"]["self"], token=token)
        assert polled.status_code == 200
        result_url = polled.json()["_links"]["result"]
        assert result_url == f"http://broker.local/jobs/{upstream_fail_job_id}/result"
        res = await planes.get(result_url, token=token)
        assert res.status_code == 200
        assert res.json()["status"] == "failed"
        assert res.json()["http_status"] == 502
        assert json.loads(base64.b64decode(res.json()["body_b64"])) == {"error": "bad_gateway"}

        # 2) Terminal FAILED job with stored unexpired JobResult (e.g. denied/failed hold).
        now = datetime.now(UTC)
        failed_with_res = Job(
            kind=JobKind.EXECUTION.value,
            status=JobStatus.FAILED.value,
            actor_type=ActorType.AGENT.value,
            created_by=_AGENT_ID,
            execution_id="exec_1807_fail_with_res",
        )
        failed_no_res = Job(
            kind=JobKind.EXECUTION.value,
            status=JobStatus.FAILED.value,
            actor_type=ActorType.AGENT.value,
            created_by=_AGENT_ID,
            error="handler crashed before dispatch",
        )
        failed_expired_res = Job(
            kind=JobKind.EXECUTION.value,
            status=JobStatus.FAILED.value,
            actor_type=ActorType.AGENT.value,
            created_by=_AGENT_ID,
            execution_id="exec_1807_fail_expired",
        )
        async with ctx.admin_db.transaction() as session:
            session.add_all([failed_with_res, failed_no_res, failed_expired_res])
            await session.flush()
            session.add_all(
                [
                    JobResult(
                        job_id=failed_with_res.id,
                        kind=JobKind.EXECUTION.value,
                        content_type=PROBLEM_CONTENT_TYPE,
                        body={
                            "type": "approval_denied",
                            "title": "Execution denied",
                            "status": 403,
                            "detail": "Denied by reviewer",
                            "error_origin": "broker",
                        },
                        available_until=now + timedelta(hours=1),
                        created_by=_AGENT_ID,
                    ),
                    JobResult(
                        job_id=failed_expired_res.id,
                        kind=JobKind.EXECUTION.value,
                        content_type="application/json",
                        body={"execution_id": "exec_1807_fail_expired", "status": "failed"},
                        available_until=now - timedelta(minutes=5),
                        created_by=_AGENT_ID,
                    ),
                ]
            )
            with_res_id = failed_with_res.id
            no_res_id = failed_no_res.id
            expired_id = failed_expired_res.id

        # Failed job WITH unexpired stored result exposes _links.result and serves 200.
        with_res_poll = await planes.get(f"http://broker.local/jobs/{with_res_id}", token=token)
        assert with_res_poll.status_code == 200
        assert with_res_poll.json()["status"] == "failed"
        assert (
            with_res_poll.json()["_links"]["result"]
            == f"http://broker.local/jobs/{with_res_id}/result"
        )
        with_res_body = await planes.get(with_res_poll.json()["_links"]["result"], token=token)
        assert with_res_body.status_code == 200
        assert with_res_body.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
        assert with_res_body.json()["type"] == "approval_denied"

        # Failed job WITHOUT stored result preserves job.error, omits result link, and returns 409.
        no_res_poll = await planes.get(f"http://broker.local/jobs/{no_res_id}", token=token)
        assert no_res_poll.status_code == 200
        assert no_res_poll.json()["status"] == "failed"
        assert no_res_poll.json()["error"] == "handler crashed before dispatch"
        assert no_res_poll.json()["_links"]["result"] is None
        no_res_body = await planes.get(f"http://broker.local/jobs/{no_res_id}/result", token=token)
        assert no_res_body.status_code == 409
        assert no_res_body.json()["type"] == "job_not_completed"

        # Failed job with EXPIRED result omits result link and returns 410.
        exp_poll = await planes.get(f"http://broker.local/jobs/{expired_id}", token=token)
        assert exp_poll.status_code == 200
        assert exp_poll.json()["_links"]["result"] is None
        exp_body = await planes.get(f"http://broker.local/jobs/{expired_id}/result", token=token)
        assert exp_body.status_code == 410
        assert exp_body.json()["type"] == "job_result_expired"


async def test_broker_job_links_enforce_actor_scoping_and_preserve_platform_tokens(
    integration_config: AppConfig,
    jwks_server: _JwksServer,
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    """Foreign actors get 404 anti-enumeration; bad tokens get 401/403; jak_/at_ work on both."""
    owner_jwt = _mint_jwks_token(signing_key, sub=_AGENT_ID)
    other_jwt = _mint_jwks_token(signing_key, sub=_OTHER_AGENT_ID)
    scopeless_jwt = _mint_jwks_token(signing_key, sub=_AGENT_ID, scopes=[])
    untrusted_jwt = _mint_jwks_token(signing_key, sub=_AGENT_ID, iss="https://untrusted.local")

    async with _running_planes(
        integration_config,
        jwks_server.url,
        jobs_api_base_url="http://admin.local",
    ) as (ctx, planes, worker):
        accepted = await planes.broker.post(
            f"/{_UPSTREAM_HOST}/v1/widgets",
            content=b'{"name":"gadget"}',
            headers={
                "Authorization": f"Bearer {owner_jwt}",
                "Content-Type": "application/json",
                "Prefer": "respond-async",
            },
        )
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]
        assert await worker._tick() is True

        # 1) Unauthenticated and untrusted-issuer tokens -> 401.
        for path in (f"/jobs/{job_id}", f"/jobs/{job_id}/result"):
            assert (await planes.get(f"http://broker.local{path}", token=None)).status_code == 401
            assert (
                await planes.get(f"http://broker.local{path}", token=untrusted_jwt)
            ).status_code == 401
            # Authenticated without capabilities:execute -> 403 insufficient_scope.
            denied = await planes.get(f"http://broker.local{path}", token=scopeless_jwt)
            assert denied.status_code == 403
            assert denied.json()["type"] == "insufficient_scope"

        # 2) Another active agent gets 404 anti-enumeration on both job and result.
        missing_job = await planes.get(
            "http://broker.local/jobs/job_nonexistent_1807", token=other_jwt
        )
        foreign_job = await planes.get(f"http://broker.local/jobs/{job_id}", token=other_jwt)
        foreign_res = await planes.get(f"http://broker.local/jobs/{job_id}/result", token=other_jwt)
        assert missing_job.status_code == 404
        assert foreign_job.status_code == 404
        assert foreign_res.status_code == 404
        assert foreign_job.json()["type"] == "job_not_found"
        assert foreign_res.json()["type"] == "job_not_found"

        # 3) Non-execution jobs (e.g. kind='import') are not exposed on the broker job proxy.
        import_job = Job(
            kind=JobKind.IMPORT.value,
            status=JobStatus.COMPLETED.value,
            actor_type=ActorType.AGENT.value,
            created_by=_AGENT_ID,
        )
        async with ctx.admin_db.transaction() as session:
            session.add(import_job)
            await session.flush()
            session.add(
                JobResult(
                    job_id=import_job.id,
                    kind=JobKind.IMPORT.value,
                    body={"revisions": []},
                    created_by=_AGENT_ID,
                )
            )
            import_job_id = import_job.id

        assert (
            await planes.get(f"http://broker.local/jobs/{import_job_id}", token=owner_jwt)
        ).status_code == 404
        assert (
            await planes.get(f"http://broker.local/jobs/{import_job_id}/result", token=owner_jwt)
        ).status_code == 404

        # 4) Platform jak_ and at_ tokens continue to work on BOTH broker and admin surfaces,
        #    and return identical non-link job fields.
        for platform_tok in (_JAK_PLAINTEXT, _AT_PLAINTEXT):
            b_job = await planes.get(f"http://broker.local/jobs/{job_id}", token=platform_tok)
            a_job = await planes.get(f"http://admin.local/jobs/{job_id}", token=platform_tok)
            assert b_job.status_code == 200
            assert a_job.status_code == 200
            b_body = {k: v for k, v in b_job.json().items() if k != "_links"}
            a_body = {k: v for k, v in a_job.json().items() if k != "_links"}
            assert b_body == a_body

            b_res = await planes.get(
                f"http://broker.local/jobs/{job_id}/result", token=platform_tok
            )
            a_res = await planes.get(f"http://admin.local/jobs/{job_id}/result", token=platform_tok)
            assert b_res.status_code == 200
            assert a_res.status_code == 200
            assert b_res.json() == a_res.json()
