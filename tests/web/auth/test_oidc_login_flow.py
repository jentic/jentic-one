"""End-to-end integration test for the M9 AuthCode+PKCE + external-OIDC login flow.

Drives the full browser flow against a fake Google-like OIDC provider, entirely
in-process: the upstream IdP's ``/token`` and ``/userinfo`` calls are intercepted
with respx, and the IdP ``/authorize`` hop is a 302 the test follows by hand. The
flow ends in platform access/refresh tokens plus an ES256 ID token, which is
verified against the published JWKS.
"""

from __future__ import annotations

import hashlib
import secrets
from base64 import urlsafe_b64encode
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from jwt.algorithms import ECAlgorithm
from sqlalchemy import delete

from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserRepository
from jentic_one.auth.web.app import create_app
from jentic_one.shared.config import IdpConfig, SigningKeyConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

# Fake upstream OIDC provider (stands in for Google).
FAKE_IDP_ISSUER = "https://fake-idp.test"
FAKE_IDP_TOKEN = f"{FAKE_IDP_ISSUER}/oauth/token"
FAKE_IDP_USERINFO = f"{FAKE_IDP_ISSUER}/userinfo"
FAKE_PROVIDER = "fake-google"

# The platform OAuth client (e.g. the web app).
CLIENT_ID = "jentic-web"
CLIENT_REDIRECT = "http://testserver/cb"
CANONICAL_BASE = "http://testserver"
SIGNING_KID = "test-es256"


def _gen_es256_pem() -> str:
    """Generate a fresh P-256 private key in PEM form for ID-token signing."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode()


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _build_app(ctx: Context) -> FastAPI:
    """Build the auth app using the real factory, with lifespan disabled."""
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan
    return app


@pytest.fixture()
def oidc_context(web_context: Context) -> Context:
    """web_context with the auth surface configured for the OIDC login flow."""
    auth = web_context.config.auth
    auth.canonical_base_url = CANONICAL_BASE
    auth.id_signing = [
        SigningKeyConfig(kid=SIGNING_KID, private_key_pem=_gen_es256_pem())  # type: ignore[arg-type]
    ]
    auth.idp = IdpConfig(
        enabled=True,
        provider=FAKE_PROVIDER,
        issuer=FAKE_IDP_ISSUER,
        client_id="upstream-client",
        client_secret="upstream-secret",  # type: ignore[arg-type]
        scopes=["openid", "email", "profile"],
        exchange_endpoint=FAKE_IDP_TOKEN,
        userinfo_endpoint=FAKE_IDP_USERINFO,
    )
    return web_context


@pytest.fixture()
def client(oidc_context: Context) -> Iterator[TestClient]:
    app = _build_app(oidc_context)
    with TestClient(app, follow_redirects=False) as tc:
        yield tc


@pytest.fixture()
async def _cleanup(oidc_context: Context) -> AsyncGenerator[None, None]:
    """Remove users/identities/codes created by the flow after each test."""
    yield
    async with oidc_context.admin_db.transaction() as session:
        await session.execute(
            delete(ExternalIdentity).where(ExternalIdentity.provider == FAKE_PROVIDER)
        )
        await session.execute(
            delete(AuthorizationCode).where(AuthorizationCode.client_id == CLIENT_ID)
        )
        await session.execute(delete(User).where(User.auth_provider == FAKE_PROVIDER))


def _stub_idp(*, sub: str, email: str, email_verified: bool) -> None:
    """Install respx mocks for the upstream IdP token + userinfo endpoints."""
    respx.post(FAKE_IDP_TOKEN).mock(
        return_value=Response(200, json={"access_token": "upstream-access-token"})
    )
    respx.get(FAKE_IDP_USERINFO).mock(
        return_value=Response(
            200,
            json={
                "sub": sub,
                "email": email,
                "email_verified": email_verified,
                "given_name": "Ada",
                "family_name": "Lovelace",
            },
        )
    )


@dataclass
class AuthorizeStart:
    signed_state: str
    code_verifier: str


def _begin_authorize(client: TestClient, *, state: str, nonce: str) -> AuthorizeStart:
    """Hit /authorize and capture the signed internal state + PKCE verifier."""
    verifier, challenge = _pkce_pair()
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": CLIENT_REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
        },
    )
    assert resp.status_code == 302, resp.text
    parsed = urlparse(resp.headers["location"])
    assert f"{parsed.scheme}://{parsed.netloc}" == FAKE_IDP_ISSUER, resp.headers["location"]
    signed_state = str(parse_qs(parsed.query)["state"][0])
    return AuthorizeStart(signed_state=signed_state, code_verifier=verifier)


def _callback(client: TestClient, *, signed_state: str) -> str:
    """Drive /oauth/callback and return the platform authorization code."""
    resp = client.get(
        "/oauth/callback",
        params={"code": "fake-idp-code", "state": signed_state},
    )
    assert resp.status_code == 302, resp.text
    parsed = urlparse(resp.headers["location"])
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == CLIENT_REDIRECT, resp.headers[
        "location"
    ]
    return str(parse_qs(parsed.query)["code"][0])


def _exchange(client: TestClient, *, code: str, code_verifier: str) -> Response:
    """Exchange the platform code + PKCE verifier at /oauth/token."""
    resp: Response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": CLIENT_REDIRECT,
            "client_id": CLIENT_ID,
        },
    )
    return resp


def _verify_id_token(
    client: TestClient, id_token: str, *, expected_nonce: str
) -> dict[str, object]:
    """Verify an ES256 ID token against the published JWKS."""
    jwks = client.get("/.well-known/jwks.json").json()
    assert jwks["keys"], "JWKS must publish at least one key"
    public_key = ECAlgorithm.from_jwk(jwks["keys"][0])
    claims: dict[str, object] = jwt.decode(
        id_token,
        public_key,  # type: ignore[arg-type]
        algorithms=["ES256"],
        audience=CLIENT_ID,
    )
    assert claims["iss"] == CANONICAL_BASE
    assert claims["nonce"] == expected_nonce
    return claims


@respx.mock
def test_full_oidc_login_flow(client: TestClient, _cleanup: None) -> None:
    """Happy path: /authorize -> IdP -> /oauth/callback -> /oauth/token -> verified ID token."""
    nonce = "client-nonce-abc"
    _stub_idp(sub="google-sub-1", email="ada@example.com", email_verified=True)

    start = _begin_authorize(client, state="client-state-123", nonce=nonce)
    code = _callback(client, signed_state=start.signed_state)

    resp = _exchange(client, code=code, code_verifier=start.code_verifier)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["id_token"]
    assert body["token_type"] == "bearer"

    claims = _verify_id_token(client, body["id_token"], expected_nonce=nonce)
    assert claims["email"] == "ada@example.com"


@respx.mock
def test_unverified_email_does_not_link_existing_account(
    client: TestClient, existing_local_user: str, _cleanup: None
) -> None:
    """An IdP asserting email_verified=false must NOT link to an existing local account.

    Emails are unique, so a duplicate account cannot be created either; the login
    must fail closed rather than take over the existing account.
    """
    _stub_idp(sub="google-sub-2", email=SHARED_EMAIL, email_verified=False)

    start = _begin_authorize(client, state="s", nonce="n")
    resp = client.get(
        "/oauth/callback",
        params={"code": "fake-idp-code", "state": start.signed_state},
    )
    assert resp.status_code == 302, resp.text
    # Rejected: redirected to the internal error page, not back to the client with a code.
    assert urlparse(resp.headers["location"]).path == "/error", resp.headers["location"]


@respx.mock
def test_unverified_email_without_collision_creates_user(
    client: TestClient, _cleanup: None
) -> None:
    """An unverified IdP email with no existing local account still mints a new user."""
    _stub_idp(sub="google-sub-unique", email="newcomer@example.com", email_verified=False)

    start = _begin_authorize(client, state="s", nonce="n")
    code = _callback(client, signed_state=start.signed_state)
    resp = _exchange(client, code=code, code_verifier=start.code_verifier)
    assert resp.status_code == 200, resp.text

    claims = _verify_id_token(client, resp.json()["id_token"], expected_nonce="n")
    assert claims["email"] == "newcomer@example.com"


@respx.mock
def test_wrong_pkce_verifier_rejected(client: TestClient, _cleanup: None) -> None:
    """A mismatched PKCE verifier must fail the token exchange with 400."""
    _stub_idp(sub="google-sub-3", email="pkce@example.com", email_verified=True)

    start = _begin_authorize(client, state="s", nonce="n")
    code = _callback(client, signed_state=start.signed_state)

    resp = _exchange(client, code=code, code_verifier="totally-wrong-verifier")
    assert resp.status_code == 400, resp.text


@respx.mock
def test_authorization_code_is_single_use(client: TestClient, _cleanup: None) -> None:
    """Replaying a consumed authorization code must fail with 400."""
    _stub_idp(sub="google-sub-4", email="replay@example.com", email_verified=True)

    start = _begin_authorize(client, state="s", nonce="n")
    code = _callback(client, signed_state=start.signed_state)

    first = _exchange(client, code=code, code_verifier=start.code_verifier)
    assert first.status_code == 200, first.text

    replay = _exchange(client, code=code, code_verifier=start.code_verifier)
    assert replay.status_code == 400, replay.text


SHARED_EMAIL = "victim@example.com"


@pytest.fixture()
async def existing_local_user(oidc_context: Context) -> AsyncGenerator[str, None]:
    """A pre-existing local user whose email an unverified IdP claim will try to match."""
    async with oidc_context.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=SHARED_EMAIL,
            first_name="Real",
            last_name="Owner",
            auth_provider="local",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        user_id = user.id
    yield user_id

    async with oidc_context.admin_db.transaction() as session:
        await session.execute(delete(User).where(User.id == user_id))
