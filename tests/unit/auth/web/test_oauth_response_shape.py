"""Unit tests pinning the raw JSON shape of OAuth token-plane responses.

RFC 6749 §5.1 / OIDC Core §3.1.3.3 (token endpoint) and RFC 7662 §2.2
(introspection): optional members the server has no value for are OMITTED
from the response body — never serialized as JSON ``null``. Strict clients
(mcp-remote 0.8.3's zod schema, Cursor's MCP SDK) reject a null ``id_token``
or ``refresh_token`` and drop the whole token response, which live-broke
Claude Desktop's refresh exchange. Pinned on the raw JSON body, mirroring
the anonymous DCR door's omission tests
(test_oauth_client_registration_router.py, PR #1250).

RFC 6749 §5.1 also REQUIRES the ``scope`` member whenever the granted scope
differs from the requested one — and the platform downscopes routinely
(effective scope = live scope grants ∩ client ceiling ∩ grant scopes; the D2
triple intersection on the agent channel). Every grant leg therefore carries
``scope`` with the minted token's effective (post-intersection, as-enforced)
value, so strict clients (mcp-remote pins requested_scope) learn what they
actually hold instead of presenting tokens for scopes they were never
granted (#1260). One exception, forced by §3.3's ABNF (``scope-token`` is
1*): an EMPTY effective set cannot be serialized, so the member is omitted —
reachable only where the caller requested no scopes at this endpoint (the
token request has no scope parameter; consent fails closed on an empty
intersection) or where every grant was revoked post-mint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, PlatformClientConfig
from jentic_one.shared.web.deps import resolve_identity

_PLATFORM_CLIENT_ID = "jentic-one-spa"
_PUBLIC_CLIENT_ID = "oc_public_mcp_client"


def _fake_identity() -> Identity:
    return Identity(sub="usr_test", email="test@example.com", permissions=[])


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.dependency_overrides[resolve_identity] = _fake_identity

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        platform_clients=[
            PlatformClientConfig(
                client_id=_PLATFORM_CLIENT_ID,
                redirect_uris=["https://app.example.com/auth/callback"],
            )
        ],
    )
    app.state.ctx = mock_ctx
    return TestClient(app)


# ---------- /oauth/token: RFC 6749 §5.1 + OIDC Core §3.1.3.3 ----------


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_agent_channel_code_exchange_omits_unset_id_token(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """A grant-bearing (agent-channel) code mints no id_token (D11): the
    member is absent from the raw body, not ``"id_token": null``."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.authenticate_for_token_endpoint = AsyncMock(return_value=True)
    mock_oauth_svc_cls.return_value = mock_oauth_svc

    mock_authorize_svc = MagicMock()
    mock_authorize_svc.precheck_auth_code = AsyncMock(return_value=None)
    mock_authorize_svc.exchange_code = AsyncMock(
        return_value=("at_agent", "rt_agent", None, ["apis:read"])
    )
    mock_authorize_cls.return_value = mock_authorize_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "auth_code_agent",
            "code_verifier": "verifier_agent",
            "redirect_uri": "http://localhost:33418/callback",
            "client_id": _PUBLIC_CLIENT_ID,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_agent"
    assert data["refresh_token"] == "rt_agent"
    assert "id_token" not in data
    # RFC 6749 §5.1: the effective scope is always present in the raw body.
    assert b'"scope":"apis:read"' in resp.content
    # Belt-and-braces: the raw body carries no null members at all.
    assert b"null" not in resp.content


@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_user_channel_code_exchange_keeps_set_id_token(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    client: TestClient,
) -> None:
    """exclude_none must not drop members that ARE set: a user-channel
    exchange that mints an id_token still returns it."""
    mock_authorize_svc = MagicMock()
    mock_authorize_svc.precheck_auth_code = AsyncMock(return_value=None)
    mock_authorize_svc.exchange_code = AsyncMock(
        return_value=("at_user", "rt_user", "id_token_user", ["openid", "email"])
    )
    mock_authorize_cls.return_value = mock_authorize_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "auth_code_user",
            "code_verifier": "verifier_user",
            "redirect_uri": "https://app.example.com/auth/callback",
            "client_id": _PLATFORM_CLIENT_ID,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id_token"] == "id_token_user"
    assert data["refresh_token"] == "rt_user"
    # Scopes are space-delimited (RFC 6749 §3.3), not a JSON array.
    assert data["scope"] == "openid email"
    assert b"null" not in resp.content


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_refresh_exchange_omits_unset_optional_members(
    mock_token_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """The refresh_token grant never mints an id_token: the member is absent
    from the raw body — the ``"id_token": null`` here is what strict clients
    (mcp-remote 0.8.3 zod) rejected, breaking Claude Desktop's refresh leg."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.is_public_client = AsyncMock(return_value=True)
    mock_oauth_svc_cls.return_value = mock_oauth_svc

    mock_token_svc = MagicMock(access_ttl_seconds=3600)
    mock_token_svc.refresh = AsyncMock(return_value=("at_new", "rt_new", ["apis:read"]))
    mock_token_cls.return_value = mock_token_svc

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": "rt_old",
            "client_id": _PUBLIC_CLIENT_ID,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_new"
    assert data["refresh_token"] == "rt_new"
    assert "id_token" not in data
    # The refresh leg reports the rotated pair's effective scope too (§5.1).
    assert b'"scope":"apis:read"' in resp.content
    assert b"null" not in resp.content


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_downscoped_exchange_reports_narrowed_scope_not_requested(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """Requested > granted: the response carries the NARROWED effective scope.

    The consent-time D2 triple intersection (requested ∩ agent live grants ∩
    client ceiling) mints a grant narrower than what the client asked for at
    /authorize. RFC 6749 §5.1 REQUIRES the token response to say so — a strict
    client (mcp-remote pins requested_scope) would otherwise present the token
    for scopes it never got and hit confusing downstream 403s. Here the client
    requested three scopes but the intersection kept exactly one: the raw body
    carries only the narrowed value, never an echo of the request.
    """
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.authenticate_for_token_endpoint = AsyncMock(return_value=True)
    mock_oauth_svc_cls.return_value = mock_oauth_svc

    mock_authorize_svc = MagicMock()
    mock_authorize_svc.precheck_auth_code = AsyncMock(return_value=None)
    # Requested at /authorize: "apis:read toolkits:read toolkits:execute".
    # The exchange mints the post-intersection grant set: apis:read only.
    mock_authorize_svc.exchange_code = AsyncMock(
        return_value=("at_narrow", "rt_narrow", None, ["apis:read"])
    )
    mock_authorize_cls.return_value = mock_authorize_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "auth_code_narrow",
            "code_verifier": "verifier_narrow",
            "redirect_uri": "http://localhost:33418/callback",
            "client_id": _PUBLIC_CLIENT_ID,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_narrow"
    # The narrowed scope, exactly — not the broader requested set.
    assert data["scope"] == "apis:read"
    assert b'"scope":"apis:read"' in resp.content
    assert b"toolkits:read" not in resp.content
    assert b"toolkits:execute" not in resp.content
    assert b"null" not in resp.content


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_jwt_bearer_exchange_includes_effective_scope(
    mock_token_cls: MagicMock,
    mock_assertion_cls: MagicMock,
    client: TestClient,
) -> None:
    """The jwt-bearer leg reports the agent's live-grant scope set (§5.1),
    space-delimited per RFC 6749 §3.3."""
    mock_assertion_svc = MagicMock()
    mock_assertion_svc.verify_and_exchange = AsyncMock(
        return_value=("at_agent_jwt", "rt_agent_jwt", ["apis:read", "capabilities:read"])
    )
    mock_assertion_cls.return_value = mock_assertion_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "eyJ.test.assertion",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_agent_jwt"
    assert data["scope"] == "apis:read capabilities:read"
    assert b'"scope":"apis:read capabilities:read"' in resp.content
    assert b"null" not in resp.content


@patch("jentic_one.auth.web.routers.oauth.ServiceAccountAuthService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_client_credentials_exchange_includes_effective_scope(
    mock_token_cls: MagicMock,
    mock_sa_auth_cls: MagicMock,
    client: TestClient,
) -> None:
    """The client_credentials leg reports the SA's live-grant scope set (§5.1)."""
    mock_sa_auth = MagicMock(access_ttl_seconds=3600)
    mock_sa_auth.authenticate_client_credentials = AsyncMock(
        return_value=("at_sa", "rt_sa", ["capabilities:execute"])
    )
    mock_sa_auth_cls.return_value = mock_sa_auth
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "client_credentials",
            "client_id": "sva_test123",
            "client_secret": "jcs_secret_value",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_sa"
    assert data["scope"] == "capabilities:execute"
    assert b'"scope":"capabilities:execute"' in resp.content
    assert b"null" not in resp.content


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_empty_effective_scope_set_omits_the_scope_member(
    mock_token_cls: MagicMock,
    mock_assertion_cls: MagicMock,
    client: TestClient,
) -> None:
    """A zero-grant agent (jwt-bearer, no scope requestable at this endpoint)
    mints a token with an empty effective set. RFC 6749 §3.3's ABNF forbids
    ``"scope": ""`` (scope-token is 1*), so the member is OMITTED from the
    raw body — never emitted as the empty string, never as JSON null."""
    mock_assertion_svc = MagicMock()
    mock_assertion_svc.verify_and_exchange = AsyncMock(
        return_value=("at_zero_grant", "rt_zero_grant", [])
    )
    mock_assertion_cls.return_value = mock_assertion_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "eyJ.test.assertion",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_zero_grant"
    assert "scope" not in data
    assert b'"scope"' not in resp.content
    assert b'""' not in resp.content
    assert b"null" not in resp.content


# ---------- /oauth/introspect: RFC 7662 §2.2 ----------


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_introspect_inactive_token_body_is_exactly_active_false(
    mock_token_cls: MagicMock,
    client: TestClient,
) -> None:
    """RFC 7662 §2.2: for an inactive/unknown token the body is exactly
    ``{"active": false}`` — the unset optional members (sub, scope, exp,
    token_type) are omitted, never serialized as JSON null."""
    mock_token_svc = MagicMock()
    mock_token_svc.introspect = AsyncMock(return_value={"active": False})
    mock_token_cls.return_value = mock_token_svc

    resp = client.post(
        "/oauth/introspect",
        json={"token": "at_unknown"},
        headers={"Authorization": "Bearer platform-token"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"active": False}
    assert b"null" not in resp.content
