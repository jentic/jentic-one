"""Unit tests for the §4.4 agent-picker consent variant (phase-3a, 3a-3).

Pins: the agent-model client renders the picker with only the user's own
active agents; zero agents → empty-state page with no code; approve mints
the grant + a grant-bearing code with the recomputed (server-side) scope
intersection and openid stripped (D11); deny and invalid selections mint
nothing; consent_model='user' clients keep today's flow byte-identical.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.authorize_service import AgentConsentOption
from jentic_one.auth.services.errors import ConsentAgentNotEligibleError
from jentic_one.auth.web.routers import authorize
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.state.backend import MemoryStateBackend

_JWT_SECRET = "test-agent-picker-secret"
_CLIENT_ID = "oc_agent_picker_app"
_REDIRECT_URI = "https://mcpapp.example.com/callback"
_HANDLE = "handle-agent-picker-test"


def _client_view(
    *,
    consent_model: str = "agent",
    allowed_scopes: list[str] | None = None,
) -> OAuthClientView:
    return OAuthClientView(
        id="oac_agent1",
        client_id=_CLIENT_ID,
        name="MCP App",
        description=None,
        redirect_uris=[_REDIRECT_URI],
        allowed_scopes=allowed_scopes,
        active=True,
        require_consent=True,
        token_endpoint_auth_method="none",
        consent_model=consent_model,
        registration_source="dcr",
        software_id=None,
        approval_status="approved",
        created_at=datetime.now(UTC),
        updated_at=None,
        created_by=None,
    )


def _make_app() -> tuple[TestClient, MemoryStateBackend]:
    app = FastAPI()
    app.include_router(authorize.router)

    ctx = MagicMock()
    ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        platform_clients=[],
    )
    ctx.config.admin.auth.jwt_secret.get_secret_value.return_value = _JWT_SECRET
    app.state.ctx = ctx

    backend = MemoryStateBackend()
    app.state.auth_state_backend = backend
    return TestClient(app), backend


def _seed_consent_handle(
    backend: MemoryStateBackend,
    *,
    scope: str = "openid apis:read apis:write",
    handle: str = _HANDLE,
    redirect_uri: str = _REDIRECT_URI,
) -> None:
    payload = json.dumps(
        {
            "claims": {
                "external_subject": "ext-agent-1",
                "email": "owner@example.com",
                "email_verified": True,
                "first_name": "Agent",
                "last_name": "Owner",
            },
            "redirect_uri": redirect_uri,
            "original_state": "xyz",
            "client_id": _CLIENT_ID,
            "code_challenge": "challenge",
            "scope": scope,
            "nonce": None,
            "client_name": "MCP App",
            "client_description": None,
            "user_email": "owner@example.com",
            "iat": int(time.time()),
        }
    ).encode()
    asyncio.run(backend.set(f"consent-handle:{handle}", payload, ttl_s=300.0))


def _agent_option(
    agent_id: str = "agnt_1",
    name: str = "runtime-agent",
    scopes: frozenset[str] = frozenset({"apis:read"}),
) -> AgentConsentOption:
    return AgentConsentOption(id=agent_id, name=name, scopes=scopes)


def _mock_authorize_svc(
    *,
    user_id: str | None = "usr_owner",
    agents: list[AgentConsentOption] | None = None,
) -> MagicMock:
    svc = MagicMock()
    svc.resolve_existing_user_id = AsyncMock(return_value=user_id)
    svc.list_consentable_agents = AsyncMock(return_value=agents or [])
    svc.provision_from_claims = AsyncMock(return_value="usr_owner")
    svc.record_consent_decision = AsyncMock()
    svc.issue_authorization_code = AsyncMock(return_value="code_grant")
    return svc


# ---------- consent page (GET) ----------


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_page_renders_picker_with_own_active_agents(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(allowed_scopes=["apis:read", "apis:write"])
    )
    svc = _mock_authorize_svc(
        agents=[
            _agent_option("agnt_1", "reader-agent", frozenset({"apis:read"})),
            _agent_option("agnt_2", "writer-agent", frozenset({"apis:read", "apis:write"})),
        ]
    )
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    body = resp.text
    assert 'name="agent_id" value="agnt_1"' in body
    assert 'name="agent_id" value="agnt_2"' in body
    assert "reader-agent" in body and "writer-agent" in body
    # The redirect-URI origin is rendered prominently (untrusted-name counter).
    assert "https://mcpapp.example.com" in body
    # The scope the reader agent lacks renders greyed-out, not hidden.
    assert "not granted (agent lacks this scope)" in body
    svc.list_consentable_agents.assert_awaited_once_with("usr_owner")
    # Rendering must not provision: only the read-only resolver ran.
    svc.provision_from_claims.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_page_renders_private_use_scheme_origin(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """RFC 8252 §7.1 DCR clients: the consent page renders the private-use
    redirect target (``scheme://authority``) just as prominently as an https
    origin — it stays the one client-controlled string the user can verify."""
    client, backend = _make_app()
    _seed_consent_handle(backend, redirect_uri="cursor://anysphere.cursor-mcp/oauth/callback")
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(allowed_scopes=["apis:read"])
    )
    svc = _mock_authorize_svc(agents=[_agent_option()])
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    assert "cursor://anysphere.cursor-mcp" in resp.text


@pytest.mark.parametrize(
    ("redirect_uri", "expected"),
    [
        ("https://mcpapp.example.com/callback", "https://mcpapp.example.com"),
        ("cursor://anysphere.cursor-mcp/oauth/callback", "cursor://anysphere.cursor-mcp"),
        # No authority component — fall back to the full (short) URI rather
        # than rendering a bare scheme the user can't compare to anything.
        ("com.example.app:/oauth/callback", "com.example.app:/oauth/callback"),
    ],
)
def test_redirect_origin_renders_sensibly_for_all_accepted_shapes(
    redirect_uri: str, expected: str
) -> None:
    assert authorize._redirect_origin(redirect_uri) == expected


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_zero_agents_renders_empty_state(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(agents=[])
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    assert "you don't have one yet" in resp.text
    assert 'name="agent_id"' not in resp.text
    svc.issue_authorization_code.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_unresolvable_user_renders_empty_state(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """Deferred provisioning: a brand-new user has no row yet, hence no
    agents — the page must not create one just to render."""
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id=None)
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    assert "you don't have one yet" in resp.text
    svc.list_consentable_agents.assert_not_awaited()
    svc.provision_from_claims.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_user_model_page_unchanged_no_picker(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """consent_model='user' clients keep today's page: no agent picker, no
    agent lookups — the pre-3a rendering path runs untouched."""
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(consent_model="user")
    )
    svc = _mock_authorize_svc()
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    assert 'name="agent_id"' not in resp.text
    assert "wants to access your account" in resp.text
    svc.resolve_existing_user_id.assert_not_awaited()
    svc.list_consentable_agents.assert_not_awaited()


# ---------- consent submit (POST) ----------


@patch("jentic_one.auth.web.routers.authorize.OAuthGrantService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_approve_mints_grant_and_grant_bearing_code(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_grant_cls: MagicMock,
) -> None:
    """Approve: grant minted with the server-side D2 intersection (openid
    stripped, allowlist and agent live scopes applied), code carries
    grant_id, redirect carries code + original state."""
    client, backend = _make_app()
    _seed_consent_handle(backend, scope="openid apis:read apis:write apis:admin")
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(allowed_scopes=["apis:read", "apis:write"])
    )
    svc = _mock_authorize_svc(
        agents=[_agent_option("agnt_1", "reader-agent", frozenset({"apis:read"}))]
    )
    mock_authorize_cls.return_value = svc
    grant_svc = MagicMock()
    grant_svc.create_grant = AsyncMock(return_value="ocg_new1")
    mock_grant_cls.return_value = grant_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "approve", "agent_id": "agnt_1"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_REDIRECT_URI}?code=code_grant&state=xyz"
    # requested ∩ allowlist ∩ agent scopes, openid stripped (D11):
    # openid → stripped; apis:admin → outside allowlist; apis:write → agent lacks.
    grant_svc.create_grant.assert_awaited_once_with(
        user_id="usr_owner",
        oauth_client_id=_CLIENT_ID,
        agent_id="agnt_1",
        scopes=["apis:read"],
        client_name="MCP App",
    )
    svc.issue_authorization_code.assert_awaited_once_with(
        user_id="usr_owner",
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        code_challenge="challenge",
        scopes="apis:read",
        nonce=None,
        grant_id="ocg_new1",
    )
    # The grant service writes the consent audit; the plain consent-decision
    # audit must not double up.
    svc.record_consent_decision.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.OAuthGrantService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_deny_mints_nothing(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_grant_cls: MagicMock,
) -> None:
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(agents=[_agent_option()])
    mock_authorize_cls.return_value = svc
    grant_svc = MagicMock()
    grant_svc.create_grant = AsyncMock()
    mock_grant_cls.return_value = grant_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "deny", "agent_id": "agnt_1"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith(f"{_REDIRECT_URI}?error=access_denied")
    grant_svc.create_grant.assert_not_awaited()
    svc.issue_authorization_code.assert_not_awaited()
    svc.provision_from_claims.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.OAuthGrantService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_invalid_agent_selection_rejected(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_grant_cls: MagicMock,
) -> None:
    """A posted agent_id outside the user's own active set (another user's
    agent, disabled, or unknown) → human error page, no grant, no code."""
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(agents=[_agent_option("agnt_mine")])
    mock_authorize_cls.return_value = svc
    grant_svc = MagicMock()
    grant_svc.create_grant = AsyncMock()
    mock_grant_cls.return_value = grant_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "approve", "agent_id": "agnt_not_mine"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_agent_selection"
    grant_svc.create_grant.assert_not_awaited()
    svc.issue_authorization_code.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.OAuthGrantService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_mint_time_refusal_renders_error_page(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_grant_cls: MagicMock,
) -> None:
    """The picker validation passes but the mint-time lock + re-check inside
    ``create_grant`` refuses (agent transferred/archived in between, review
    F1) → the same human error page as an invalid selection, no code, never
    a 500."""
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(agents=[_agent_option()])
    mock_authorize_cls.return_value = svc
    grant_svc = MagicMock()
    grant_svc.create_grant = AsyncMock(side_effect=ConsentAgentNotEligibleError("agnt_1"))
    mock_grant_cls.return_value = grant_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "approve", "agent_id": "agnt_1"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_agent_selection"
    grant_svc.create_grant.assert_awaited_once()
    svc.issue_authorization_code.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.OAuthGrantService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_missing_agent_id_rejected(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_grant_cls: MagicMock,
) -> None:
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(agents=[_agent_option()])
    mock_authorize_cls.return_value = svc
    grant_svc = MagicMock()
    grant_svc.create_grant = AsyncMock()
    mock_grant_cls.return_value = grant_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "approve"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_agent_selection"
    grant_svc.create_grant.assert_not_awaited()
    svc.issue_authorization_code.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.OAuthGrantService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_agent_model_empty_effective_scope_set_rejected(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_grant_cls: MagicMock,
) -> None:
    """§4.4 step 2: an empty server-side intersection (agent has none of the
    grantable scopes) → error page, no grant, no code."""
    client, backend = _make_app()
    _seed_consent_handle(backend, scope="openid apis:write")
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(allowed_scopes=["apis:write"])
    )
    svc = _mock_authorize_svc(agents=[_agent_option("agnt_1", scopes=frozenset({"apis:read"}))])
    mock_authorize_cls.return_value = svc
    grant_svc = MagicMock()
    grant_svc.create_grant = AsyncMock()
    mock_grant_cls.return_value = grant_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "approve", "agent_id": "agnt_1"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=no_grantable_scopes"
    grant_svc.create_grant.assert_not_awaited()
    svc.issue_authorization_code.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.OAuthGrantService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_user_model_submit_byte_identical(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_grant_cls: MagicMock,
) -> None:
    """consent_model='user' approve: same calls, same args, same redirect as
    before 3a-3 — no grant service involvement, scopes passed through
    unmodified (openid included), no grant_id on the code."""
    client, backend = _make_app()
    _seed_consent_handle(backend, scope="openid apis:read")
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(consent_model="user")
    )
    svc = _mock_authorize_svc()
    svc.issue_authorization_code = AsyncMock(return_value="code_user")
    mock_authorize_cls.return_value = svc
    grant_svc = MagicMock()
    grant_svc.create_grant = AsyncMock()
    mock_grant_cls.return_value = grant_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "approve"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_REDIRECT_URI}?code=code_user&state=xyz"
    grant_svc.create_grant.assert_not_awaited()
    svc.record_consent_decision.assert_awaited_once_with(
        user_id="usr_owner",
        oauth_client_id=_CLIENT_ID,
        approved=True,
        scopes="openid apis:read",
    )
    svc.issue_authorization_code.assert_awaited_once_with(
        user_id="usr_owner",
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        code_challenge="challenge",
        scopes="openid apis:read",
        nonce=None,
    )
