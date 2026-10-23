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
import time
from base64 import urlsafe_b64encode
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

# Starlette's TestClient rides httpx2 when it is installed (it is — the mcp
# SDK depends on it): upstream-mock responses stay httpx.Response (the app's
# own client), while TestClient calls answer httpx2 responses.
from httpx2 import Response as ClientResponse
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserRepository
from jentic_one.auth.core.idp import (
    AdmissionDecision,
    IdpClaims,
    get_admission_policy,
    get_default_idp_grants,
    set_admission_policy,
    set_default_idp_grants,
)
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.web.app import create_app
from jentic_one.shared.config import IdpConfig, PlatformClientConfig, SigningKeyConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

# Fake upstream OIDC provider (stands in for Google).
FAKE_IDP_ISSUER = "https://fake-idp.test"
FAKE_IDP_TOKEN = f"{FAKE_IDP_ISSUER}/oauth/token"
FAKE_IDP_USERINFO = f"{FAKE_IDP_ISSUER}/userinfo"
FAKE_IDP_DISCOVERY = f"{FAKE_IDP_ISSUER}/.well-known/openid-configuration"
FAKE_IDP_JWKS = f"{FAKE_IDP_ISSUER}/.well-known/jwks.json"
FAKE_PROVIDER = "fake-google"
UPSTREAM_CLIENT_ID = "upstream-client"

_UPSTREAM_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_UPSTREAM_JWK: dict[str, Any] = {
    **jwt.api_jwk.PyJWK.from_json(RSAAlgorithm.to_jwk(_UPSTREAM_RSA_KEY.public_key()))._jwk_data,
    "kid": "upstream-rs256-kid",
    "use": "sig",
    "alg": "RS256",
}
_LAST_UPSTREAM_NONCE: dict[str, str] = {"value": ""}
_LAST_UPSTREAM_CODE_CHALLENGE: dict[str, str] = {"value": ""}

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
    auth.platform_clients = [
        PlatformClientConfig.model_construct(
            client_id=CLIENT_ID,
            redirect_uris=[CLIENT_REDIRECT],
        ),
    ]
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


def _stub_idp(
    *,
    sub: str,
    email: str,
    email_verified: bool,
    issuer: str = FAKE_IDP_ISSUER,
    token_url: str = FAKE_IDP_TOKEN,
    userinfo_url: str = FAKE_IDP_USERINFO,
) -> None:
    """Install respx mocks for the upstream IdP discovery, JWKS, token, and userinfo endpoints."""
    discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    jwks_url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
    respx.get(discovery_url).mock(
        return_value=Response(200, json={"issuer": issuer, "jwks_uri": jwks_url})
    )
    respx.get(jwks_url).mock(return_value=Response(200, json={"keys": [_UPSTREAM_JWK]}))

    def _token_handler(request: Any) -> Response:
        form = parse_qs(request.content.decode())
        code_verifier = form.get("code_verifier", [""])[0]
        expected_challenge = _LAST_UPSTREAM_CODE_CHALLENGE["value"]
        if expected_challenge:
            computed = (
                urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            assert computed == expected_challenge, "upstream PKCE verifier mismatch"
        id_token = jwt.encode(
            {
                "iss": issuer,
                "sub": sub,
                "aud": UPSTREAM_CLIENT_ID,
                "nonce": _LAST_UPSTREAM_NONCE["value"],
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
            },
            _UPSTREAM_RSA_KEY,
            algorithm="RS256",
            headers={"kid": "upstream-rs256-kid"},
        )
        return Response(
            200,
            json={"access_token": "upstream-access-token", "id_token": id_token},
        )

    respx.post(token_url).mock(side_effect=_token_handler)
    respx.get(userinfo_url).mock(
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


def _begin_authorize(
    client: TestClient,
    *,
    state: str,
    nonce: str,
    expected_issuer: str = FAKE_IDP_ISSUER,
) -> AuthorizeStart:
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
    assert f"{parsed.scheme}://{parsed.netloc}" == expected_issuer, resp.headers["location"]
    query = parse_qs(parsed.query)
    signed_state = str(query["state"][0])
    _LAST_UPSTREAM_NONCE["value"] = str(query["nonce"][0])
    assert query.get("code_challenge_method") == ["S256"]
    _LAST_UPSTREAM_CODE_CHALLENGE["value"] = str(query["code_challenge"][0])
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


def _exchange(client: TestClient, *, code: str, code_verifier: str) -> ClientResponse:
    """Exchange the platform code + PKCE verifier at /oauth/token."""
    resp: ClientResponse = client.post(
        "/oauth/token",
        json={
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


def test_auth_idp_descriptor_advertises_enabled(client: TestClient) -> None:
    """WI-4 capability hint: the public descriptor reports enabled + provider."""
    resp = client.get("/auth/idp")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["provider"] == FAKE_PROVIDER


@respx.mock
def test_admission_policy_reject_blocks_new_user(client: TestClient, _cleanup: None) -> None:
    """WI-1: a reject-policy declines a brand-new email; no user is provisioned.

    The already-linked / existing-account paths are unaffected — the seam only
    gates never-seen emails. Here the policy rejects, so the callback redirects
    to the internal error page instead of back to the client with a code.
    """
    _stub_idp(sub="google-sub-reject", email="blocked@example.com", email_verified=True)

    original = get_admission_policy()

    def _reject_all(claims: IdpClaims) -> AdmissionDecision:
        return AdmissionDecision.REJECT

    set_admission_policy(_reject_all)
    try:
        start = _begin_authorize(client, state="s", nonce="n")
        resp = client.get(
            "/oauth/callback",
            params={"code": "fake-idp-code", "state": start.signed_state},
        )
    finally:
        set_admission_policy(original)

    assert resp.status_code == 302, resp.text
    # Declined by policy → internal error page (access_denied), not a client code.
    assert urlparse(resp.headers["location"]).path == "/error", resp.headers["location"]
    assert "access_denied" in resp.headers["location"]


async def _grants_for_email(ctx: Context, email: str) -> list[str]:
    """Return the permission names written for the user with this email."""
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.get_by_email(session, email)
        assert user is not None, f"expected a provisioned user for {email}"
        result = await session.execute(
            select(UserPermissionGrant.permission).where(UserPermissionGrant.user_id == user.id)
        )
        return sorted(result.scalars().all())


@respx.mock
async def test_new_user_gets_configured_default_grants(
    client: TestClient, oidc_context: Context, _cleanup: None
) -> None:
    """A configured default-grants provider seeds a brand-new user's baseline grants.

    An unknown scope in the configured list is dropped defensively rather than
    failing the login.
    """
    _stub_idp(sub="google-sub-grants", email="reader@example.com", email_verified=True)

    original = get_default_idp_grants()

    def _read_only(claims: IdpClaims) -> list[str]:
        return ["capabilities:read", "apis:read", "not:a:real:scope"]

    set_default_idp_grants(_read_only)
    try:
        start = _begin_authorize(client, state="s", nonce="n")
        code = _callback(client, signed_state=start.signed_state)
        resp = _exchange(client, code=code, code_verifier=start.code_verifier)
        assert resp.status_code == 200, resp.text
    finally:
        set_default_idp_grants(original)

    grants = await _grants_for_email(oidc_context, "reader@example.com")
    assert grants == ["apis:read", "capabilities:read"]


@respx.mock
async def test_new_user_defaults_to_no_grants(
    client: TestClient, oidc_context: Context, _cleanup: None
) -> None:
    """With the default provider installed, a new user starts with zero grants."""
    _stub_idp(sub="google-sub-nogrants", email="empty@example.com", email_verified=True)

    start = _begin_authorize(client, state="s", nonce="n")
    code = _callback(client, signed_state=start.signed_state)
    resp = _exchange(client, code=code, code_verifier=start.code_verifier)
    assert resp.status_code == 200, resp.text

    grants = await _grants_for_email(oidc_context, "empty@example.com")
    assert grants == []


SHARED_EMAIL = "victim@example.com"


@respx.mock
async def test_shared_provider_different_issuer_does_not_reuse_external_identity(
    oidc_context: Context, _cleanup: None
) -> None:
    """Two OIDC issuers sharing the same provider label must not share external identities."""
    issuer_a = "https://idp-a.test"
    issuer_b = "https://idp-b.test"
    shared_sub = "shared-subject-42"

    oidc_context.config.auth.idp = IdpConfig(
        enabled=True,
        provider=FAKE_PROVIDER,
        issuer=issuer_a,
        client_id=UPSTREAM_CLIENT_ID,
        client_secret="upstream-secret",  # type: ignore[arg-type]
        scopes=["openid", "email", "profile"],
    )
    _stub_idp(
        sub=shared_sub,
        email="alice@idp-a.test",
        email_verified=True,
        issuer=issuer_a,
        token_url=f"{issuer_a}/oauth/token",
        userinfo_url=f"{issuer_a}/userinfo",
    )

    with TestClient(_build_app(oidc_context), follow_redirects=False) as client_a:
        start_a = _begin_authorize(client_a, state="s1", nonce="n1", expected_issuer=issuer_a)
        code_a = _callback(client_a, signed_state=start_a.signed_state)
        resp_a = _exchange(client_a, code=code_a, code_verifier=start_a.code_verifier)
        assert resp_a.status_code == 200, resp_a.text
        claims_a = _verify_id_token(client_a, resp_a.json()["id_token"], expected_nonce="n1")

    # Switch to a different OIDC issuer while keeping the same provider name ("fake-google").
    oidc_context.config.auth.idp = IdpConfig(
        enabled=True,
        provider=FAKE_PROVIDER,
        issuer=issuer_b,
        client_id=UPSTREAM_CLIENT_ID,
        client_secret="upstream-secret",  # type: ignore[arg-type]
        scopes=["openid", "email", "profile"],
    )
    _stub_idp(
        sub=shared_sub,
        email="bob@idp-b.test",
        email_verified=True,
        issuer=issuer_b,
        token_url=f"{issuer_b}/oauth/token",
        userinfo_url=f"{issuer_b}/userinfo",
    )

    with TestClient(_build_app(oidc_context), follow_redirects=False) as client_b:
        start_b = _begin_authorize(client_b, state="s2", nonce="n2", expected_issuer=issuer_b)
        code_b = _callback(client_b, signed_state=start_b.signed_state)
        resp_b = _exchange(client_b, code=code_b, code_verifier=start_b.code_verifier)
        assert resp_b.status_code == 200, resp_b.text
        claims_b = _verify_id_token(client_b, resp_b.json()["id_token"], expected_nonce="n2")

    assert claims_a["sub"] != claims_b["sub"]
    assert claims_a["email"] == "alice@idp-a.test"
    assert claims_b["email"] == "bob@idp-b.test"


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


@pytest.mark.parametrize("unscoped_issuer", [FAKE_PROVIDER, ""])
@respx.mock
async def test_unscoped_external_identity_signs_in_and_pins_to_issuer(
    oidc_context: Context, _cleanup: None, unscoped_issuer: str
) -> None:
    """A link with no real issuer resolves its user once, then answers only to that issuer.

    ``issuer = provider`` is what the issuer backfill writes for a provider
    without a well-known issuer; ``issuer = ''`` is what a replica that does not
    set the column writes. The IdP sends ``email_verified=false``, so only the
    link (never the email match) can resolve the user.
    """
    subject = "unscoped-subject-7"
    email = "unscoped@example.com"
    async with oidc_context.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=email,
            first_name="Ada",
            last_name="Lovelace",
            auth_provider=FAKE_PROVIDER,
            invite_state=InviteState.ACCEPTED,
            created_by="self",
        )
        user_id = user.id
        session.add(
            ExternalIdentity(
                provider=FAKE_PROVIDER,
                issuer=unscoped_issuer,
                external_subject=subject,
                user_id=user_id,
                email=email,
                created_by=user_id,
            )
        )

    # The consent-page lookup resolves the unscoped link read-only, leaving it unpinned.
    probe = IdpClaims(
        external_subject=subject,
        email=email,
        first_name="Ada",
        last_name="Lovelace",
        email_verified=False,
        issuer=FAKE_IDP_ISSUER,
    )
    assert await AuthorizeService(oidc_context).resolve_existing_user_id(probe) == user_id
    async with oidc_context.admin_db.session() as session:
        unpinned = await session.execute(
            select(ExternalIdentity.issuer).where(ExternalIdentity.external_subject == subject)
        )
        assert list(unpinned.scalars()) == [unscoped_issuer]

    _stub_idp(sub=subject, email=email, email_verified=False)
    with TestClient(_build_app(oidc_context), follow_redirects=False) as client_a:
        start = _begin_authorize(client_a, state="s1", nonce="n1")
        code = _callback(client_a, signed_state=start.signed_state)
        resp = _exchange(client_a, code=code, code_verifier=start.code_verifier)
        assert resp.status_code == 200, resp.text
        claims = _verify_id_token(client_a, resp.json()["id_token"], expected_nonce="n1")
    assert claims["sub"] == user_id

    async with oidc_context.admin_db.session() as session:
        links = (
            await session.execute(
                select(ExternalIdentity.issuer, ExternalIdentity.user_id).where(
                    ExternalIdentity.external_subject == subject
                )
            )
        ).all()
        reasons = (
            await session.execute(
                select(AuditEntry.reason).where(
                    AuditEntry.target_id == user_id, AuditEntry.action == "update"
                )
            )
        ).scalars()
        assert f"keyed external identity ({FAKE_PROVIDER}) to its issuer" in list(reasons)
    assert [tuple(row) for row in links] == [(FAKE_IDP_ISSUER, user_id)]

    # Pinned: the same subject from another issuer under the same provider label
    # no longer resolves to the user (and its unverified email cannot link).
    issuer_b = "https://idp-b.test"
    oidc_context.config.auth.idp = IdpConfig(
        enabled=True,
        provider=FAKE_PROVIDER,
        issuer=issuer_b,
        client_id=UPSTREAM_CLIENT_ID,
        client_secret="upstream-secret",  # type: ignore[arg-type]
        scopes=["openid", "email", "profile"],
    )
    _stub_idp(
        sub=subject,
        email=email,
        email_verified=False,
        issuer=issuer_b,
        token_url=f"{issuer_b}/oauth/token",
        userinfo_url=f"{issuer_b}/userinfo",
    )
    with TestClient(_build_app(oidc_context), follow_redirects=False) as client_b:
        start_b = _begin_authorize(client_b, state="s2", nonce="n2", expected_issuer=issuer_b)
        refused = client_b.get(
            "/oauth/callback", params={"code": "fake-idp-code", "state": start_b.signed_state}
        )
    assert refused.status_code == 302, refused.text
    assert urlparse(refused.headers["location"]).path == "/error", refused.headers["location"]
    async with oidc_context.admin_db.session() as session:
        after = (
            await session.execute(
                select(ExternalIdentity.issuer).where(ExternalIdentity.external_subject == subject)
            )
        ).scalars()
        assert list(after) == [FAKE_IDP_ISSUER]
