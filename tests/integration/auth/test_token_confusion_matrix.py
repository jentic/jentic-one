"""Cross-surface token-confusion matrix across control plane, standalone surface, MCP, and broker.

Verifies that every surface (combined control plane, standalone admin surface,
mounted ``/mcp`` endpoint, and broker) enforces uniform 401 refusals for
non-access or invalid credentials, honours ``x-jentic-api-key`` header precedence
over ``Authorization: Bearer``, and exposes no response oracle between unknown,
expired, wrong-type, and revoked credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt
import pytest
import structlog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from jwt.algorithms import OKPAlgorithm
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import SecretStr
from sqlalchemy import update

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos import UserRepository
from jentic_one.admin.services.user_service import UserService
from jentic_one.admin.web import app as admin_app
from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.registration_service import RegistrationService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.broker.web.deps import RequireExecuteAccess
from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import encode_state, generate_nonce
from jentic_one.mcp.app import MCP_PRM_PATH
from jentic_one.mcp.installer import mcp_lifespan
from jentic_one.shared.auth import CachedJWKSPublisher
from jentic_one.shared.auth.api_key_resolver import RETIRED_SERVICE_ACCOUNT_KEY_DETAIL
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_WRITE,
    APIS_READ,
    BROKER_EXECUTE_PERMISSION,
    ORG_ADMIN,
    USERS_READ,
    USERS_WRITE,
)
from jentic_one.shared.auth.tokens import issue_jwt
from jentic_one.shared.config import (
    JwtVerificationConfig,
    TrustedIssuerConfig,
    resolved_auth_base_url,
)
from jentic_one.shared.context import Context
from jentic_one.shared.crypto import load_es256_private_key
from jentic_one.shared.models import ActorType
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.wiring import build_default_container
from tests.integration.auth.conftest import (
    LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    LOCAL_LOGIN_PLATFORM_REDIRECT,
)
from tests.integration.auth.seeds import (
    CODE_VERIFIER,
    code_challenge,
    seed_agent,
    seed_permissions,
    seed_user,
)

pytestmark = pytest.mark.integration

_WRONG_JWT_SECRET = "wrong-hs256-secret-key-at-least-32-bytes-long!"  # pragma: allowlist secret
_RETIRED_SAK = "sak_retired_service_account_key_0123456789"  # pragma: allowlist secret
_UNKNOWN_AT = "at_unknown_nonexistent_token_0123456789abcdef"  # pragma: allowlist secret
_TRUSTED_ISS = "https://idp.matrix.test"
_BROKER_AUD = "jentic-broker"
_AGENT_KID = "matrix-ed25519-k1"

_CONTROL_UNIFORM_DETAIL = "Invalid or expired token"
_BROKER_UNIFORM_DETAIL = "Invalid or expired access token"

_MCP_ACCEPT_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_MCP_WHOAMI_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "whoami", "arguments": {}},
}


class _JwksServer:
    """Loopback HTTP server serving a static JWKS document for broker JWT verification."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        self._body = json.dumps(jwks).encode()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = outer._body
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

    def set_keys(self, keys: list[dict[str, Any]]) -> None:
        self._body = json.dumps({"keys": keys}).encode()

    def __enter__(self) -> _JwksServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_hs256_raw(header: dict[str, Any], payload: dict[str, Any], key_bytes: bytes) -> str:
    """Construct a compact HS256 JWS signed with raw ``key_bytes``."""
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(key_bytes, signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def _ed25519_keypair(kid: str = _AGENT_KID) -> tuple[Ed25519PrivateKey, dict[str, Any]]:
    private_key = Ed25519PrivateKey.generate()
    jwk = OKPAlgorithm().to_jwk(private_key.public_key())
    jwk_dict = json.loads(jwk) if isinstance(jwk, str) else dict(jwk)
    jwk_dict["kid"] = kid
    return private_key, {"keys": [jwk_dict]}


def _make_combined_app(ctx: Context) -> FastAPI:
    return create_combined_app(
        ctx,
        ["registry", "admin", "control", "auth"],
        container=build_default_container(ctx),
    )


def _make_standalone_admin_app(ctx: Context) -> FastAPI:
    return admin_app.create_app(ctx)


def _make_broker_probe_app(ctx: Context) -> FastAPI:
    """Broker auth + execute-permission dependency wired to ``ctx``."""
    app = FastAPI()
    app.state.ctx = ctx
    install_broker_auth(app, ctx)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]

    @app.post("/probe-execute")
    async def _probe(identity: RequireExecuteAccess) -> dict[str, object]:
        return {"sub": identity.sub, "permissions": identity.permissions}

    return app


@dataclass
class _MatrixEnv:
    ctx: Context
    admin_id: str
    user_id: str
    agent_id: str
    agent_private_key: Ed25519PrivateKey
    _combined_app: FastAPI | None = None
    _standalone_app: FastAPI | None = None
    _broker_app: FastAPI | None = None

    @property
    def combined_app(self) -> FastAPI:
        if self._combined_app is None:
            self._combined_app = _make_combined_app(self.ctx)
        return self._combined_app

    @property
    def standalone_app(self) -> FastAPI:
        if self._standalone_app is None:
            self._standalone_app = _make_standalone_admin_app(self.ctx)
        return self._standalone_app

    @property
    def broker_app(self) -> FastAPI:
        if self._broker_app is None:
            self._broker_app = _make_broker_probe_app(self.ctx)
        return self._broker_app


@pytest.fixture(scope="module")
def jwks_server() -> Iterator[_JwksServer]:
    with _JwksServer({"keys": []}) as server:
        yield server


@pytest.fixture()
async def matrix_env(
    local_login_ctx: Context,
    jwks_server: _JwksServer,
    clean_grants: None,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_MatrixEnv]:
    ctx = local_login_ctx
    jwks = CachedJWKSPublisher(ctx.config.auth).get_jwks()
    jwks_server.set_keys([{**k, "alg": "ES256", "use": "sig"} for k in jwks["keys"]])

    monkeypatch.setattr(ctx.config.server.mcp, "enabled", True)
    monkeypatch.setattr(ctx.config.server.mcp.oauth, "enabled", True)
    monkeypatch.setattr(ctx.config.broker, "resolve_cache_ttl_seconds", 3600.0)
    monkeypatch.setattr(
        ctx.config.broker,
        "jwt_verification",
        JwtVerificationConfig(
            audience=_BROKER_AUD,
            trusted_issuers=[
                TrustedIssuerConfig(issuer=_TRUSTED_ISS, jwks_url=jwks_server.url),
            ],
        ),
    )

    admin_id = await seed_user(ctx, "usr_tcm_admin")
    await seed_permissions(ctx, admin_id, [ORG_ADMIN, USERS_WRITE, AGENTS_WRITE])

    user_id = await seed_user(ctx, "usr_tcm_user")
    await seed_permissions(
        ctx, user_id, [USERS_READ, APIS_READ, BROKER_EXECUTE_PERMISSION, AGENTS_WRITE]
    )

    agent_private_key, agent_jwks = _ed25519_keypair()
    agent_id = await seed_agent(
        ctx,
        owner_id=user_id,
        scopes=[USERS_READ, APIS_READ, BROKER_EXECUTE_PERMISSION],
        name="tcm-agent",
    )
    async with ctx.admin_db.transaction() as session:
        await session.execute(update(Agent).where(Agent.id == agent_id).values(jwks=agent_jwks))

    yield _MatrixEnv(
        ctx=ctx,
        admin_id=admin_id,
        user_id=user_id,
        agent_id=agent_id,
        agent_private_key=agent_private_key,
    )


@asynccontextmanager
async def _http_client(
    app: FastAPI, headers: dict[str, str] | None = None
) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        headers=headers or {},
    ) as client:
        yield client


async def _call_surface(env: _MatrixEnv, surface: str, headers: dict[str, str]) -> Response:
    if surface == "combined_users_me":
        async with _http_client(env.combined_app, headers) as client:
            return await client.get("/users/me")
    if surface == "standalone_users_me":
        async with _http_client(env.standalone_app, headers) as client:
            return await client.get("/users/me")
    if surface == "mcp_tools_call":
        app = env.combined_app
        mount = app.state.mcp_mount
        if getattr(mount.session_manager, "_has_started", False):
            mount.session_manager = StreamableHTTPSessionManager(
                app=mount.server,
                json_response=True,
                stateless=True,
            )
        merged = {**_MCP_ACCEPT_HEADERS, **headers}
        async with (
            mcp_lifespan(app, env.ctx),
            _http_client(app, merged) as client,
        ):
            return await client.post("/mcp", json=_MCP_WHOAMI_BODY)
    if surface == "broker_probe_execute":
        async with _http_client(env.broker_app, headers) as client:
            return await client.post("/probe-execute")
    raise ValueError(f"Unknown surface: {surface!r}")


def _assert_surface_refusal(resp: Response, *, surface: str, expected_detail: str) -> None:
    assert resp.status_code == 401, f"surface={surface} status={resp.status_code} body={resp.text}"
    body = resp.json()
    if surface == "mcp_tools_call":
        assert body == {"detail": "Unauthorized"}
        challenge = resp.headers.get("www-authenticate", "")
        assert challenge == f'Bearer resource_metadata="https://testserver{MCP_PRM_PATH}"'
        return
    assert body["type"] == "unauthorized"
    assert body["status"] == 401
    assert body["detail"] == expected_detail


# ── Credential builders ───────────────────────────────────────────────────────


async def _build_hs256_wrong_secret(env: _MatrixEnv) -> str:
    return issue_jwt(
        {
            "sub": env.user_id,
            "actor_type": ActorType.USER.value,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
        },
        _WRONG_JWT_SECRET,
        300,
    )


async def _build_alg_none_jwt(env: _MatrixEnv) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": _TRUSTED_ISS,
            "aud": _BROKER_AUD,
            "sub": env.user_id,
            "actor_type": ActorType.USER.value,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
            "iat": now,
            "exp": now + 300,
        },
        "",
        algorithm="none",
    )


async def _build_hs256_es256_jwk_bytes(env: _MatrixEnv) -> str:
    jwks = CachedJWKSPublisher(env.ctx.config.auth).get_jwks()
    jwk_entry = jwks["keys"][0]
    jwk_bytes = json.dumps(jwk_entry, sort_keys=True).encode()
    now = int(time.time())
    return _sign_hs256_raw(
        {"alg": "HS256", "typ": "JWT", "kid": jwk_entry["kid"]},
        {
            "iss": _TRUSTED_ISS,
            "aud": _BROKER_AUD,
            "sub": env.agent_id,
            "actor_type": ActorType.AGENT.value,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "scopes": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
            "iat": now,
            "exp": now + 300,
        },
        jwk_bytes,
    )


async def _build_hs256_es256_pem_bytes(env: _MatrixEnv) -> str:
    signing_cfg = env.ctx.config.auth.id_signing[0]
    pub_key = load_es256_private_key(signing_cfg).public_key()
    pem_bytes = pub_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    now = int(time.time())
    return _sign_hs256_raw(
        {"alg": "HS256", "typ": "JWT", "kid": signing_cfg.kid},
        {
            "iss": _TRUSTED_ISS,
            "aud": _BROKER_AUD,
            "sub": env.agent_id,
            "actor_type": ActorType.AGENT.value,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "scopes": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
            "iat": now,
            "exp": now + 300,
        },
        pem_bytes,
    )


async def _build_expired_login_jwt(env: _MatrixEnv) -> str:
    secret = env.ctx.config.admin.auth.jwt_secret.get_secret_value()
    return issue_jwt(
        {
            "sub": env.user_id,
            "actor_type": ActorType.USER.value,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
        },
        secret,
        -300,
    )


async def _build_login_jwt_missing_actor_type(env: _MatrixEnv) -> str:
    secret = env.ctx.config.admin.auth.jwt_secret.get_secret_value()
    return issue_jwt(
        {
            "sub": env.user_id,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
        },
        secret,
        300,
    )


async def _build_login_jwt_service_account_actor_type(env: _MatrixEnv) -> str:
    secret = env.ctx.config.admin.auth.jwt_secret.get_secret_value()
    return issue_jwt(
        {
            "sub": env.user_id,
            "actor_type": "service_account",
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
        },
        secret,
        300,
    )


async def _build_login_jwt_missing_exp(env: _MatrixEnv) -> str:
    secret = env.ctx.config.admin.auth.jwt_secret.get_secret_value()
    return jwt.encode(
        {
            "sub": env.user_id,
            "actor_type": ActorType.USER.value,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
            "iat": int(time.time()),
        },
        secret,
        algorithm="HS256",
    )


async def _build_login_jwt_stale_sgen(env: _MatrixEnv) -> str:
    secret = env.ctx.config.admin.auth.jwt_secret.get_secret_value()
    token = issue_jwt(
        {
            "sub": env.user_id,
            "actor_type": ActorType.USER.value,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
        },
        secret,
        300,
    )
    async with env.ctx.admin_db.transaction() as session:
        await UserRepository.revoke_login_sessions(session, env.user_id)
    return token


async def _build_refresh_token_as_bearer(env: _MatrixEnv) -> str:
    _at, rt = await TokenService(env.ctx).issue_pair(
        env.user_id, ActorType.USER, [USERS_READ, BROKER_EXECUTE_PERMISSION]
    )
    return rt


async def _build_rat_as_bearer(env: _MatrixEnv) -> str:
    _priv, jwks = _ed25519_keypair("rat-kid")
    reg = await RegistrationService(env.ctx).register(
        "tcm-rat-agent", jwks, scope=BROKER_EXECUTE_PERMISSION
    )
    return reg.registration_access_token


async def _build_es256_id_token_as_bearer(env: _MatrixEnv) -> str:
    authz = AuthorizeService(env.ctx)
    code = await authz.issue_authorization_code(
        user_id=env.user_id,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes=f"openid {USERS_READ} {BROKER_EXECUTE_PERMISSION}",
        nonce="tcm-oidc-nonce",
    )
    _at, _rt, id_token, _scopes = await authz.exchange_code(
        code=code,
        code_verifier=CODE_VERIFIER,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    )
    assert id_token is not None
    return id_token


async def _build_eddsa_assertion_as_bearer(env: _MatrixEnv) -> str:
    now = int(time.time())
    base_url = resolved_auth_base_url(env.ctx.config).rstrip("/")
    return jwt.encode(
        {
            "iss": env.agent_id,
            "sub": env.agent_id,
            "aud": f"{base_url}/oauth/token",
            "iat": now,
            "exp": now + 60,
            "jti": secrets.token_urlsafe(16),
        },
        env.agent_private_key,
        algorithm="EdDSA",
        headers={"kid": _AGENT_KID},
    )


async def _build_connect_state_jwt_as_bearer(env: _MatrixEnv) -> str:
    state_secret = env.ctx.config.credentials.connect.state_secret.get_secret_value()
    state = ConnectState(
        credential_id="crd_tcm_connect_state",
        provider="github",
        actor_id=env.user_id,
        actor_type=ActorType.USER.value,
        issued_at=datetime.now(UTC),
        nonce=generate_nonce(),
        redirect_uri="https://testserver/credentials/oauth/callback",
        browser_binding_hash="a" * 64,
    )
    return encode_state(state_secret, state, 600)


async def _build_revoked_access_token(env: _MatrixEnv) -> str:
    at, _rt = await TokenService(env.ctx).issue_pair(
        env.user_id, ActorType.USER, [USERS_READ, BROKER_EXECUTE_PERMISSION]
    )
    # Warm the broker's in-process CachedTokenValidator before revoking.
    warm_resp = await _call_surface(env, "broker_probe_execute", {"Authorization": f"Bearer {at}"})
    assert warm_resp.status_code == 200

    async with _http_client(env.combined_app, {"Authorization": f"Bearer {at}"}) as client:
        rev_resp = await client.post("/oauth/revoke", json={"token": at})
        assert rev_resp.status_code == 200, rev_resp.text
    return at


async def _build_unknown_access_token(_env: _MatrixEnv) -> str:
    return _UNKNOWN_AT


async def _build_disabled_user_access_token(env: _MatrixEnv) -> str:
    at, _rt = await TokenService(env.ctx).issue_pair(
        env.user_id, ActorType.USER, [USERS_READ, BROKER_EXECUTE_PERMISSION]
    )
    warm_resp = await _call_surface(env, "broker_probe_execute", {"Authorization": f"Bearer {at}"})
    assert warm_resp.status_code == 200

    admin_identity = Identity(
        sub=env.admin_id,
        actor_type=ActorType.USER,
        permissions=[ORG_ADMIN, USERS_WRITE],
    )
    await UserService(env.ctx).disable(env.user_id, identity=admin_identity)
    return at


async def _build_disabled_owner_agent_access_token(env: _MatrixEnv) -> str:
    at, _rt = await TokenService(env.ctx).issue_pair(
        env.agent_id, ActorType.AGENT, [USERS_READ, BROKER_EXECUTE_PERMISSION]
    )
    warm_resp = await _call_surface(env, "broker_probe_execute", {"Authorization": f"Bearer {at}"})
    assert warm_resp.status_code == 200

    admin_identity = Identity(
        sub=env.admin_id,
        actor_type=ActorType.USER,
        permissions=[ORG_ADMIN, USERS_WRITE],
    )
    await UserService(env.ctx).disable(env.user_id, identity=admin_identity)
    return at


async def _build_retired_sak(_env: _MatrixEnv) -> str:
    return _RETIRED_SAK


CredentialBuilder = Callable[[_MatrixEnv], Coroutine[Any, Any, str]]

_REFUSED_CREDENTIAL_CASES: list[tuple[str, CredentialBuilder, bool]] = [
    ("hs256_wrong_secret", _build_hs256_wrong_secret, False),
    ("alg_none_login_jwt", _build_alg_none_jwt, False),
    ("hs256_with_es256_public_jwk_bytes", _build_hs256_es256_jwk_bytes, False),
    ("hs256_with_es256_public_pem_bytes", _build_hs256_es256_pem_bytes, False),
    ("expired_login_jwt", _build_expired_login_jwt, False),
    ("login_jwt_missing_actor_type", _build_login_jwt_missing_actor_type, False),
    ("login_jwt_service_account_actor_type", _build_login_jwt_service_account_actor_type, False),
    ("login_jwt_missing_exp", _build_login_jwt_missing_exp, False),
    ("login_jwt_stale_sgen", _build_login_jwt_stale_sgen, False),
    ("refresh_token_as_bearer", _build_refresh_token_as_bearer, False),
    ("registration_access_token_as_bearer", _build_rat_as_bearer, False),
    ("es256_id_token_as_bearer", _build_es256_id_token_as_bearer, False),
    ("eddsa_assertion_as_bearer", _build_eddsa_assertion_as_bearer, False),
    ("connect_state_jwt_as_bearer", _build_connect_state_jwt_as_bearer, False),
    ("revoked_access_token", _build_revoked_access_token, False),
    ("unknown_access_token", _build_unknown_access_token, False),
    ("disabled_user_access_token", _build_disabled_user_access_token, False),
    ("disabled_owner_agent_access_token", _build_disabled_owner_agent_access_token, False),
    ("retired_service_account_key", _build_retired_sak, True),
]

_SURFACES = (
    "combined_users_me",
    "standalone_users_me",
    "mcp_tools_call",
    "broker_probe_execute",
)


@pytest.mark.parametrize("surface", _SURFACES)
@pytest.mark.parametrize(
    ("case_id", "builder", "is_retired_sak"),
    _REFUSED_CREDENTIAL_CASES,
    ids=[c[0] for c in _REFUSED_CREDENTIAL_CASES],
)
async def test_cross_surface_token_confusion_refused_uniformly(
    matrix_env: _MatrixEnv,
    case_id: str,
    builder: CredentialBuilder,
    is_retired_sak: bool,
    surface: str,
) -> None:
    """Every non-access, malformed, expired, wrong-type, or revoked credential is
    refused with 401 and the surface's uniform detail string across all surfaces."""
    _ = case_id
    token = await builder(matrix_env)
    resp = await _call_surface(matrix_env, surface, {"Authorization": f"Bearer {token}"})
    if is_retired_sak:
        expected_detail = RETIRED_SERVICE_ACCOUNT_KEY_DETAIL
    elif surface == "broker_probe_execute":
        expected_detail = _BROKER_UNIFORM_DETAIL
    else:
        expected_detail = _CONTROL_UNIFORM_DETAIL
    _assert_surface_refusal(resp, surface=surface, expected_detail=expected_detail)


async def test_valid_user_login_jwt_refused_at_broker(
    matrix_env: _MatrixEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid user login JWT succeeds on control-plane surfaces and MCP, but is
    refused with 401 at the broker under every broker JWT verifier configuration."""
    secret = matrix_env.ctx.config.admin.auth.jwt_secret.get_secret_value()
    user_jwt = issue_jwt(
        {
            "sub": matrix_env.user_id,
            "actor_type": ActorType.USER.value,
            "permissions": [USERS_READ, BROKER_EXECUTE_PERMISSION],
            "sgen": 0,
        },
        secret,
        300,
    )
    headers = {"Authorization": f"Bearer {user_jwt}"}

    # Accepted on combined control plane, standalone admin, and MCP.
    assert (await _call_surface(matrix_env, "combined_users_me", headers)).status_code == 200
    assert (await _call_surface(matrix_env, "standalone_users_me", headers)).status_code == 200
    mcp_resp = await _call_surface(matrix_env, "mcp_tools_call", headers)
    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["result"].get("isError") is not True

    # 1) Broker with TrustedIssuerVerifier wired: HS256 user login JWT refused (401).
    broker_trusted_resp = await _call_surface(matrix_env, "broker_probe_execute", headers)
    _assert_surface_refusal(
        broker_trusted_resp,
        surface="broker_probe_execute",
        expected_detail=_BROKER_UNIFORM_DETAIL,
    )

    # 2) Broker with TrustedIssuerVerifier wired and ES256 token declaring actor_type="user":
    # refused specifically with jwt_actor_type_not_allowed and uniform 401 on the wire.
    signing_cfg = matrix_env.ctx.config.auth.id_signing[0]
    es256_priv = load_es256_private_key(signing_cfg)
    now = int(time.time())
    es256_user_jwt = jwt.encode(
        {
            "iss": _TRUSTED_ISS,
            "aud": _BROKER_AUD,
            "sub": matrix_env.user_id,
            "actor_type": ActorType.USER.value,
            "scopes": [BROKER_EXECUTE_PERMISSION],
            "iat": now,
            "exp": now + 300,
        },
        es256_priv,
        algorithm="ES256",
        headers={"kid": signing_cfg.kid},
    )
    with structlog.testing.capture_logs() as logs:
        es256_resp = await _call_surface(
            matrix_env,
            "broker_probe_execute",
            {"Authorization": f"Bearer {es256_user_jwt}"},
        )
    _assert_surface_refusal(
        es256_resp,
        surface="broker_probe_execute",
        expected_detail=_BROKER_UNIFORM_DETAIL,
    )
    assert any(
        e.get("event") == "jwt_refused" and e.get("reason") == "jwt_actor_type_not_allowed"
        for e in logs
    )

    # 3) Broker with dev HS256 JwtVerifier sharing the secret: refused with
    # jwt_actor_type_not_allowed.
    monkeypatch.setattr(
        matrix_env.ctx.config.broker,
        "jwt_verification",
        JwtVerificationConfig(trusted_issuers=[]),
    )
    monkeypatch.setattr(matrix_env.ctx.config.broker, "jwt_secret", SecretStr(secret))
    hs256_broker_app = _make_broker_probe_app(matrix_env.ctx)
    with structlog.testing.capture_logs() as hs_logs:
        async with _http_client(hs256_broker_app, headers) as client:
            hs_resp = await client.post("/probe-execute")
    _assert_surface_refusal(
        hs_resp,
        surface="broker_probe_execute",
        expected_detail=_BROKER_UNIFORM_DETAIL,
    )
    assert any(
        e.get("event") == "jwt_refused" and e.get("reason") == "jwt_actor_type_not_allowed"
        for e in hs_logs
    )

    # 4) Broker with no JWT verifier configured: falls through to opaque lookup and misses (401).
    monkeypatch.setattr(matrix_env.ctx.config.broker, "jwt_secret", None)
    opaque_only_broker_app = _make_broker_probe_app(matrix_env.ctx)
    async with _http_client(opaque_only_broker_app, headers) as client:
        opaque_resp = await client.post("/probe-execute")
    _assert_surface_refusal(
        opaque_resp,
        surface="broker_probe_execute",
        expected_detail=_BROKER_UNIFORM_DETAIL,
    )


@pytest.mark.parametrize(
    "surface",
    ("combined_me", "standalone_users", "mcp_tools_call", "broker_probe_execute"),
)
async def test_valid_jak_in_api_key_header_precedes_invalid_bearer(
    matrix_env: _MatrixEnv,
    surface: str,
) -> None:
    """A valid ``jak_`` in ``x-jentic-api-key`` takes precedence over an invalid
    ``Authorization: Bearer`` header across every surface."""
    owner_identity = Identity(
        sub=matrix_env.user_id,
        actor_type=ActorType.USER,
        permissions=[AGENTS_WRITE, USERS_READ, BROKER_EXECUTE_PERMISSION],
    )
    raw_jak = await AgentAuthService(matrix_env.ctx).register_api_key(
        matrix_env.agent_id, identity=owner_identity
    )
    headers = {
        "x-jentic-api-key": raw_jak,
        "Authorization": "Bearer invalid.bearer.credential",
    }

    if surface == "combined_me":
        async with _http_client(matrix_env.combined_app, headers) as client:
            resp = await client.get("/me")
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == matrix_env.agent_id
        return

    if surface == "standalone_users":
        async with _http_client(matrix_env.standalone_app, headers) as client:
            resp = await client.get("/users")
        assert resp.status_code == 200, resp.text
        return

    if surface == "mcp_tools_call":
        resp = await _call_surface(matrix_env, "mcp_tools_call", headers)
        assert resp.status_code == 200, resp.text
        result = resp.json()["result"]
        assert result.get("isError") is not True
        payload = json.loads(result["content"][0]["text"])
        assert payload["id"] == matrix_env.agent_id
        return

    resp = await _call_surface(matrix_env, "broker_probe_execute", headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["sub"] == matrix_env.agent_id


@pytest.mark.parametrize("surface", _SURFACES)
async def test_no_oracle_between_unknown_and_revoked_tokens(
    matrix_env: _MatrixEnv,
    surface: str,
) -> None:
    """Unknown, revoked, expired, and disabled-actor tokens produce identical
    401 response bodies on each surface."""
    revoked_at = await _build_revoked_access_token(matrix_env)
    unknown_at = await _build_unknown_access_token(matrix_env)
    wrong_secret_jwt = await _build_hs256_wrong_secret(matrix_env)
    expired_jwt = await _build_expired_login_jwt(matrix_env)
    stale_sgen_jwt = await _build_login_jwt_stale_sgen(matrix_env)
    disabled_user_at = await _build_disabled_user_access_token(matrix_env)

    bodies: list[dict[str, Any]] = []
    for tok in (
        unknown_at,
        revoked_at,
        wrong_secret_jwt,
        expired_jwt,
        stale_sgen_jwt,
        disabled_user_at,
    ):
        resp = await _call_surface(matrix_env, surface, {"Authorization": f"Bearer {tok}"})
        assert resp.status_code == 401
        bodies.append(resp.json())

    first = bodies[0]
    for candidate in bodies[1:]:
        assert candidate == first
