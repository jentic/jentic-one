"""Unit tests for the inline agent-create arm on the consent page (P4).

Pins: the zero-agents agent-model consent page renders the create-agent form
(never the terminal empty state) whenever the handle names a provisionable
subject; POST /oauth/consent/agent verifies + burns the single-use
``agent-create`` blob, re-validates the handle and the D7 gate, creates the
agent as the consenting user through ``AgentService.create`` (default scopes),
and 303s back into consent where a single agent renders pre-selected; the
replay/expiry/splice/validation/race arms fail closed without creating
anything.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.authorize_service import AgentConsentOption, PendingAgentRef
from jentic_one.auth.services.errors import AuthServiceError, InvalidGrantError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.flow import (
    agent_create_signing_key,
    agent_status_signing_key,
    approval_state_key,
    login_signing_key,
    session_signing_key,
    sign_payload,
    state_signing_key,
    verify_payload,
)
from jentic_one.auth.web.routers import authorize, local_login
from jentic_one.shared.config import AuthConfig, LocalLoginConfig
from jentic_one.shared.models import ActorStatus, ActorType
from jentic_one.shared.models.actors import Origin
from jentic_one.shared.state.backend import MemoryStateBackend

_JWT_SECRET = "test-agent-create-secret"
_CLIENT_ID = "oc_agent_create_app"
_REDIRECT_URI = "https://mcpapp.example.com/callback"
_HANDLE = "handle-agent-create-test"


def _client_view(
    *,
    consent_model: str = "agent",
    allowed_scopes: list[str] | None = None,
) -> OAuthClientView:
    return OAuthClientView(
        id="oac_create1",
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


def _make_app() -> tuple[TestClient, MemoryStateBackend, MagicMock]:
    app = FastAPI()
    app.include_router(authorize.router)
    # The status poll answers verification failures through the shared
    # service-error handler (InvalidGrantError → 400), same as production.
    app.add_exception_handler(AuthServiceError, service_error_handler)

    ctx = MagicMock()
    ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        platform_clients=[],
    )
    ctx.config.admin.auth.jwt_secret.get_secret_value.return_value = _JWT_SECRET
    app.state.ctx = ctx

    backend = MemoryStateBackend()
    app.state.auth_state_backend = backend
    return TestClient(app), backend, ctx


def _seed_consent_handle(
    backend: MemoryStateBackend,
    *,
    scope: str = "openid apis:read",
    handle: str = _HANDLE,
    subject: dict[str, object] | None = None,
) -> None:
    if subject is None:
        subject = {
            "claims": {
                "external_subject": "ext-create-1",
                "email": "newbie@example.com",
                "email_verified": True,
                "first_name": "New",
                "last_name": "User",
            }
        }
    payload = json.dumps(
        {
            **subject,
            "redirect_uri": _REDIRECT_URI,
            "original_state": "xyz",
            "client_id": _CLIENT_ID,
            "code_challenge": "challenge",
            "scope": scope,
            "nonce": None,
            "client_name": "MCP App",
            "client_description": None,
            "user_email": "newbie@example.com",
            "iat": int(time.time()),
        }
    ).encode()
    asyncio.run(backend.set(f"consent-handle:{handle}", payload, ttl_s=300.0))


def _mint_blob(
    ctx: MagicMock,
    *,
    handle: str = _HANDLE,
    user_key: str = "idp:ext-create-1",
    nonce: str | None = None,
) -> str:
    payload: dict[str, str | None] = {
        "ch_digest": hashlib.sha256(handle.encode()).hexdigest(),
        "user_key": user_key,
        "iat": str(int(time.time())),
    }
    if nonce is not None:
        # Mirrors the mint site's per-mint entropy — lets a test hold two
        # DISTINCT valid blobs for the same handle+subject+second.
        payload["n"] = nonce
    return sign_payload(payload, agent_create_signing_key(ctx), purpose="agent-create")


def _extract_create_state(html: str) -> str:
    marker = 'name="create_state" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def _mock_authorize_svc(
    *,
    user_id: str | None = None,
    agents: list[AgentConsentOption] | None = None,
    has_any_agents: bool | None = None,
    pending_agent: PendingAgentRef | None = None,
    can_create_active: bool = True,
) -> MagicMock:
    svc = MagicMock()
    svc.resolve_existing_user_id = AsyncMock(return_value=user_id)
    svc.list_consentable_agents = AsyncMock(return_value=agents or [])
    # Any-status ownership defaults to mirroring the active list — pass
    # has_any_agents=True to model a user whose agents are all non-active.
    svc.owner_has_any_agents = AsyncMock(
        return_value=bool(agents) if has_any_agents is None else has_any_agents
    )
    # The hybrid arm inputs (P4 security review): no pending agent and an
    # agents:write-holding user by default, so the pre-hybrid tests keep
    # exercising the original ACTIVE arm unchanged.
    svc.newest_pending_agent = AsyncMock(return_value=pending_agent)
    svc.user_can_create_active_agent = AsyncMock(return_value=can_create_active)
    svc.provision_from_claims = AsyncMock(return_value="usr_new")
    svc.issue_authorization_code = AsyncMock(return_value="code_x")
    svc.record_consent_decision = AsyncMock()
    return svc


def _mock_agent_svc() -> MagicMock:
    svc = MagicMock()
    view = MagicMock()
    view.id = "agnt_created"
    view.name = "created-agent"
    svc.create = AsyncMock(return_value=view)
    return svc


# ---------- zero-agents GET renders the create form ----------


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_zero_agents_renders_create_form_for_idp_subject(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """Zero active agents + IdP claims on the handle → the create form, not
    the terminal empty state; the form carries the handle and a verifiable
    agent-create blob bound to handle + subject."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id="usr_new", agents=[])
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    body = resp.text
    assert 'action="/oauth/consent/agent"' in body
    assert 'name="agent_name"' in body
    assert "you don't have one yet" not in body
    assert "create your first one to continue" in body
    # Explanation + verifiable origin + signed-in identity are all present.
    assert "An agent is the identity this application will act as" in body
    assert "https://mcpapp.example.com" in body
    assert "newbie@example.com" in body
    # The embedded blob verifies under the agent-create purpose and binds
    # this very handle + subject.
    blob = _extract_create_state(body)
    payload = verify_payload(
        blob, agent_create_signing_key(ctx), purpose="agent-create", max_age=300
    )
    assert payload["ch_digest"] == hashlib.sha256(_HANDLE.encode()).hexdigest()
    assert payload["user_key"] == "idp:ext-create-1"
    # Rendering must not provision or create anything.
    svc.provision_from_claims.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_zero_agents_renders_create_form_for_local_login_subject(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """Local-login handles (already-provisioned user) get the form too, bound
    to the local user id."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend, subject={"local_user_id": "usr_local1"})
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(agents=[])
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    blob = _extract_create_state(resp.text)
    payload = verify_payload(
        blob, agent_create_signing_key(ctx), purpose="agent-create", max_age=300
    )
    assert payload["user_key"] == "local:usr_local1"
    svc.list_consentable_agents.assert_awaited_once_with("usr_local1")


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_zero_agents_without_subject_keeps_terminal_empty_state(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """A handle naming no subject (no claims, no local user) cannot offer a
    create form — the pre-P4 empty state survives as the fallback."""
    client, backend, _ctx = _make_app()
    _seed_consent_handle(backend, subject={})
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(agents=[])
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    assert "you don't have one yet" in resp.text
    assert 'action="/oauth/consent/agent"' not in resp.text


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_nonzero_agents_keeps_the_picker_no_create_form(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    client, backend, _ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(
        user_id="usr_new",
        agents=[AgentConsentOption(id="agnt_1", name="mine", scopes=frozenset({"apis:read"}))],
    )
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    assert 'name="agent_id" value="agnt_1"' in resp.text
    assert 'action="/oauth/consent/agent"' not in resp.text


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_only_nonactive_agents_keeps_terminal_empty_state(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """A user whose agents were all disabled/archived by an admin owns zero
    ACTIVE agents but is NOT first-run: the create form must not render — it
    would let the owner mint a fresh active agent and sidestep the admin
    action. The terminal empty state stays, with the out-of-service copy
    (NOT the first-run "you don't have one yet" lie — review-lap eyeball)."""
    client, backend, _ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id="usr_new", agents=[], has_any_agents=True)
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    assert "you have no available agents" in resp.text
    assert "Contact your" in resp.text
    assert "you don't have one yet" not in resp.text
    assert 'action="/oauth/consent/agent"' not in resp.text
    svc.owner_has_any_agents.assert_awaited_once_with("usr_new")


# ---------- POST /oauth/consent/agent ----------


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_happy_path_creates_agent_and_reenters_consent(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """Happy path: agent created as the consenting user with default scopes
    (scopes=None → DEFAULT_AGENT_SCOPES in the service), then a 303 back into
    GET /oauth/consent where the single new agent renders pre-selected."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id="usr_new", agents=[])
    mock_authorize_cls.return_value = svc
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    blob = _mint_blob(ctx)
    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": blob, "agent_name": "  my-assistant  "},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/oauth/consent?ch={_HANDLE}"
    agent_svc.create.assert_awaited_once()
    call = agent_svc.create.await_args
    payload = call.args[0]
    assert payload.name == "my-assistant"  # whitespace-stripped
    assert payload.scopes is None  # platform default scopes, never invented
    assert call.kwargs["owner_id"] == "usr_new"
    assert call.kwargs["status"] is ActorStatus.ACTIVE  # agents:write holder → ACTIVE arm
    identity = call.kwargs["identity"]
    assert identity.sub == "usr_new"
    assert identity.actor_type == ActorType.USER
    assert identity.origin == Origin.API  # TestClient sends no browser UA
    # An existing user resolved read-only — no provisioning.
    svc.provision_from_claims.assert_not_awaited()

    # Follow the redirect: the picker now renders the new agent pre-checked.
    svc.list_consentable_agents = AsyncMock(
        return_value=[
            AgentConsentOption(id="agnt_created", name="my-assistant", scopes=frozenset())
        ]
    )
    followup = client.get("/oauth/consent", params={"ch": _HANDLE})
    assert followup.status_code == 200
    assert 'value="agnt_created" required checked' in followup.text


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_provisions_deferred_user_then_creates(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """First-run IdP user with no row yet: the create submit provisions from
    the handle's claims (affirmative action) and owns the new agent."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id=None, agents=[])
    mock_authorize_cls.return_value = svc
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": _mint_blob(ctx), "agent_name": "first"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    svc.provision_from_claims.assert_awaited_once()
    assert agent_svc.create.await_args.kwargs["owner_id"] == "usr_new"


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_replayed_blob_rejected(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """The blob is single-use: the second submit with the same blob fails
    closed with the standard error and creates nothing more."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id="usr_new", agents=[])
    mock_authorize_cls.return_value = svc
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    blob = _mint_blob(ctx)
    first = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": blob, "agent_name": "first"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    replay = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": blob, "agent_name": "second"},
        follow_redirects=False,
    )
    assert replay.status_code == 302
    assert replay.headers["location"] == "/error?error=invalid_consent"
    agent_svc.create.assert_awaited_once()  # only the first submit created


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_expired_blob_rejected(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id="usr_new", agents=[])
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    stale = sign_payload(
        {
            "ch_digest": hashlib.sha256(_HANDLE.encode()).hexdigest(),
            "user_key": "idp:ext-create-1",
            "iat": str(int(time.time()) - 4000),
        },
        agent_create_signing_key(ctx),
        purpose="agent-create",
    )
    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": stale, "agent_name": "late"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_consent"
    agent_svc.create.assert_not_awaited()


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_wrong_purpose_blob_rejected(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """Purpose discrimination: a blob signed under any sibling flow purpose
    (with that purpose's own derived key) can never drive the create form —
    the fifth purpose rejects all five siblings, same discipline as the
    state/approval/login/session/agent-status matrix."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id="usr_new", agents=[])
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    payload = {
        "ch_digest": hashlib.sha256(_HANDLE.encode()).hexdigest(),
        "user_key": "idp:ext-create-1",
        "iat": str(int(time.time())),
    }
    siblings = [
        ("state", state_signing_key(ctx)),
        ("approval", approval_state_key(ctx)),
        ("login", login_signing_key(ctx)),
        ("session", session_signing_key(ctx)),
        ("agent-status", agent_status_signing_key(ctx)),
    ]
    for purpose, key in siblings:
        foreign = sign_payload(dict(payload), key, purpose=purpose)
        resp = client.post(
            "/oauth/consent/agent",
            data={"consent_token": _HANDLE, "create_state": foreign, "agent_name": "nope"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, purpose
        assert resp.headers["location"] == "/error?error=invalid_consent"
    # A forged _purpose under the agent-create key is equally refused.
    forged = sign_payload(dict(payload), agent_create_signing_key(ctx), purpose="approval")
    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": forged, "agent_name": "nope"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_consent"
    agent_svc.create.assert_not_awaited()


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_blob_spliced_onto_other_handle_rejected(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """CSRF/splice binding: a validly-signed blob minted for one consent
    handle must not drive a create against a different (live) handle, and a
    blob bound to another subject must not drive this handle's create."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    _seed_consent_handle(backend, handle="other-handle")
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id="usr_new", agents=[])
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    # Blob minted for "other-handle", posted with _HANDLE.
    spliced = _mint_blob(ctx, handle="other-handle")
    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": spliced, "agent_name": "nope"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_consent"

    # Blob bound to a different subject, posted with the right handle.
    wrong_subject = _mint_blob(ctx, user_key="idp:someone-else")
    resp2 = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": wrong_subject, "agent_name": "nope"},
        follow_redirects=False,
    )
    assert resp2.status_code == 302
    assert resp2.headers["location"] == "/error?error=invalid_consent"
    agent_svc.create.assert_not_awaited()


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_expired_consent_handle_rejected(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """Consent-handle expiry behaves exactly like the consent submit's
    invalid-handle arm: the standard error redirect, nothing created."""
    client, _backend, ctx = _make_app()  # handle deliberately NOT seeded
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id="usr_new", agents=[])
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": _mint_blob(ctx), "agent_name": "late"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_consent"
    agent_svc.create.assert_not_awaited()


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_invalid_name_rerenders_form_with_inline_error(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """A blank (or over-length) name re-renders the form with the error
    inline and a FRESH blob — no side effects, no provisioning; the retry
    with the fresh blob then succeeds."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id="usr_new", agents=[])
    mock_authorize_cls.return_value = svc
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    blob = _mint_blob(ctx)
    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": blob, "agent_name": "   "},
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert 'class="error"' in resp.text
    assert "Enter an agent name" in resp.text
    agent_svc.create.assert_not_awaited()
    svc.provision_from_claims.assert_not_awaited()

    fresh = _extract_create_state(resp.text)
    assert fresh != blob  # the burned blob is not re-embedded

    # Over-length names hit the same arm.
    resp2 = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": fresh, "agent_name": "x" * 256},
        follow_redirects=False,
    )
    assert resp2.status_code == 200
    assert "Enter an agent name" in resp2.text
    agent_svc.create.assert_not_awaited()

    # The re-rendered blob is live: a valid retry completes the flow.
    fresh2 = _extract_create_state(resp2.text)
    resp3 = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": fresh2, "agent_name": "ok-now"},
        follow_redirects=False,
    )
    assert resp3.status_code == 303
    agent_svc.create.assert_awaited_once()


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_invalid_name_rerender_escapes_hostile_input(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """A hostile over-length name echoed back into the form value is
    HTML-escaped — no markup survives into the page."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id="usr_new", agents=[])
    mock_agent_svc_cls.return_value = _mock_agent_svc()

    hostile = '"><script>alert(1)</script>' + "x" * 256
    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": _mint_blob(ctx), "agent_name": hostile},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_race_agent_appeared_skips_creation_and_reenters_consent(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """If an agent appeared between the form render and the submit (another
    tab, an admin), nothing is created — the submit just re-enters consent,
    which now renders the picker."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(
        user_id="usr_new",
        agents=[AgentConsentOption(id="agnt_race", name="appeared", scopes=frozenset())],
    )
    mock_authorize_cls.return_value = svc
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": _mint_blob(ctx), "agent_name": "dupe"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/oauth/consent?ch={_HANDLE}"
    agent_svc.create.assert_not_awaited()


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_skipped_when_user_owns_only_nonactive_agents(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """The submit-side twin of the render guard: zero ACTIVE agents but
    non-active rows exist (admin disabled/archived them) → nothing is
    created; a stale or hand-crafted submit cannot mint a fresh active agent
    past the admin action."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id="usr_new", agents=[], has_any_agents=True)
    mock_authorize_cls.return_value = svc
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": _mint_blob(ctx), "agent_name": "sneaky"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    agent_svc.create.assert_not_awaited()
    svc.owner_has_any_agents.assert_awaited_once_with("usr_new")


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_rejected_for_user_consent_model_client(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """Only the agent-picker consent variant renders the form — a
    consent_model='user' client can never drive a create through it."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(consent_model="user")
    )
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id="usr_new", agents=[])
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": _mint_blob(ctx), "agent_name": "nope"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_consent"
    agent_svc.create.assert_not_awaited()


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_gated_client_rejected_midflow(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """Mid-flow D7 re-check: a client denied/deactivated between the form
    render and the submit must not cause resource creation."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    gated = _client_view()
    gated = gated.model_copy(update={"active": False})
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=gated)
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id="usr_new", agents=[])
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": _mint_blob(ctx), "agent_name": "nope"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=access_denied"
    agent_svc.create.assert_not_awaited()


# ---------- the hybrid arms (P4 security review) ----------


def _mint_status_blob(ctx: MagicMock, *, handle: str = _HANDLE, agent_id: str = "agnt_p") -> str:
    return sign_payload(
        {
            "ch_digest": hashlib.sha256(handle.encode()).hexdigest(),
            "agent_id": agent_id,
            "iat": str(int(time.time())),
        },
        agent_status_signing_key(ctx),
        purpose="agent-status",
    )


def _extract_awaiting_config(html: str) -> dict[str, object]:
    marker = '<script id="agent-approval-config" type="application/json">'
    start = html.index(marker) + len(marker)
    raw = html[start : html.index("</script>", start)]
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_form_copy_tells_the_truth_per_arm(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """The create form's footer never promises the wrong outcome (security
    review): an agents:write holder reads the immediate-use wording, an
    unpermissioned resolved user reads "will need administrator approval",
    and a not-yet-provisioned IdP subject (arm undecidable at render) reads
    the neutral "may need"."""
    client, backend, _ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())

    # Resolved user WITH agents:write → the original immediate-use wording.
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id="usr_new", agents=[])
    body = client.get("/oauth/consent", params={"ch": _HANDLE}).text
    assert "default agent\n            permissions" in body
    assert "administrator approval" not in body

    # Resolved user WITHOUT agents:write → "will need administrator approval".
    mock_authorize_cls.return_value = _mock_authorize_svc(
        user_id="usr_new", agents=[], can_create_active=False
    )
    body = client.get("/oauth/consent", params={"ch": _HANDLE}).text
    assert "will need administrator approval" in body

    # Unprovisioned IdP subject → neutral "may need administrator approval".
    mock_authorize_cls.return_value = _mock_authorize_svc(user_id=None, agents=[])
    body = client.get("/oauth/consent", params={"ch": _HANDLE}).text
    assert "may need administrator approval" in body


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_pending_agent_reentry_renders_awaiting_page(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """A flow (re-)entry while the user's only agents sit in PENDING parks on
    the awaiting page — not the create form, not the terminal empty state —
    with a status blob bound to THIS handle and THIS agent."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(
        user_id="usr_new",
        agents=[],
        has_any_agents=True,
        pending_agent=PendingAgentRef(id="agnt_p", name="my-pending"),
    )
    mock_authorize_cls.return_value = svc

    resp = client.get("/oauth/consent", params={"ch": _HANDLE})

    assert resp.status_code == 200
    body = resp.text
    assert "awaiting\n            administrator approval" in body
    assert "my-pending" in body
    assert 'action="/oauth/consent/agent"' not in body  # no create form
    assert 'name="agent_id"' not in body  # no picker
    config = _extract_awaiting_config(body)
    assert config["continue_url"] == f"/oauth/consent?ch={_HANDLE}"
    status_url = str(config["status_url"])
    assert status_url.startswith("/oauth/consent/agent/status?st=")
    blob = status_url.removeprefix("/oauth/consent/agent/status?st=")
    payload = verify_payload(
        unquote(blob), agent_status_signing_key(ctx), purpose="agent-status", max_age=300
    )
    assert payload["ch_digest"] == hashlib.sha256(_HANDLE.encode()).hexdigest()
    assert payload["agent_id"] == "agnt_p"


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_create_unpermissioned_lands_pending_and_awaiting_page(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """The hybrid's pending arm: a consenting user without agents:write gets
    a PENDING agent (the /register posture) and the awaiting page — never an
    immediately-ACTIVE agent (the security-review bypass)."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    svc = _mock_authorize_svc(user_id="usr_new", agents=[], can_create_active=False)
    mock_authorize_cls.return_value = svc
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": _mint_blob(ctx), "agent_name": "gated"},
        follow_redirects=False,
    )

    assert resp.status_code == 200  # the awaiting page, not the 303 re-entry
    agent_svc.create.assert_awaited_once()
    assert agent_svc.create.await_args.kwargs["status"] is ActorStatus.PENDING
    config = _extract_awaiting_config(resp.text)
    assert config["continue_url"] == f"/oauth/consent?ch={_HANDLE}"
    # The status blob is bound to the agent the create just minted.
    blob = str(config["status_url"]).removeprefix("/oauth/consent/agent/status?st=")
    payload = verify_payload(
        unquote(blob), agent_status_signing_key(ctx), purpose="agent-status", max_age=300
    )
    assert payload["agent_id"] == "agnt_created"
    svc.user_can_create_active_agent.assert_awaited_once_with("usr_new")


@patch("jentic_one.auth.web.deps.AgentService")
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.flow.OAuthClientService")
def test_parallel_submits_create_exactly_one_agent(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_agent_svc_cls: MagicMock,
) -> None:
    """The per-subject slot claim (security review): two DISTINCT valid blobs
    (two GET renders) whose submits both pass the plain-read zero-agents
    check still create exactly one agent — the loser just re-enters consent."""
    client, backend, ctx = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    # owner_has_any_agents stays False for BOTH submits — modelling the
    # in-flight window in which the first insert has not committed.
    svc = _mock_authorize_svc(user_id="usr_new", agents=[], has_any_agents=False)
    mock_authorize_cls.return_value = svc
    agent_svc = _mock_agent_svc()
    mock_agent_svc_cls.return_value = agent_svc

    first = client.post(
        "/oauth/consent/agent",
        data={
            "consent_token": _HANDLE,
            "create_state": _mint_blob(ctx, nonce="a"),
            "agent_name": "winner",
        },
        follow_redirects=False,
    )
    second = client.post(
        "/oauth/consent/agent",
        data={
            "consent_token": _HANDLE,
            "create_state": _mint_blob(ctx, nonce="b"),
            "agent_name": "loser",
        },
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert second.status_code == 303  # loser re-enters consent, creates nothing
    assert second.headers["location"] == f"/oauth/consent?ch={_HANDLE}"
    agent_svc.create.assert_awaited_once()
    assert agent_svc.create.await_args.args[0].name == "winner"


# ---------- the pending-agent status poll ----------


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
def test_status_poll_collapses_lifecycle_into_tri_state(
    mock_authorize_cls: MagicMock,
) -> None:
    """ACTIVE → approved (the picker's own predicate), REJECTED → denied,
    and everything else — pending, disabled, archived, a vanished row —
    reads as pending so the endpoint is no lifecycle/deletion oracle."""
    client, _backend, ctx = _make_app()
    svc = MagicMock()
    mock_authorize_cls.return_value = svc

    expectations = [
        ("pending", "pending"),
        ("active", "approved"),
        ("rejected", "denied"),
        ("disabled", "pending"),
        ("archived", "pending"),
        (None, "pending"),
    ]
    for raw, expected in expectations:
        svc.get_agent_status = AsyncMock(return_value=raw)
        resp = client.get("/oauth/consent/agent/status", params={"st": _mint_status_blob(ctx)})
        assert resp.status_code == 200, raw
        assert resp.json() == {"status": expected}, raw
        assert resp.headers["cache-control"] == "no-store"
        svc.get_agent_status.assert_awaited_once_with("agnt_p")


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
def test_status_poll_rejects_foreign_and_expired_blobs(
    mock_authorize_cls: MagicMock,
) -> None:
    """The poll accepts ONLY a live agent-status blob: garbage, expired, and
    every sibling purpose (including the agent-create form blob riding the
    same page family) get a 400 — and the agent id is never read from
    anything but the verified blob."""
    client, _backend, ctx = _make_app()
    svc = MagicMock()
    svc.get_agent_status = AsyncMock(return_value="active")
    mock_authorize_cls.return_value = svc

    assert client.get("/oauth/consent/agent/status", params={"st": "garbage"}).status_code == 400

    stale = sign_payload(
        {
            "ch_digest": hashlib.sha256(_HANDLE.encode()).hexdigest(),
            "agent_id": "agnt_p",
            "iat": str(int(time.time()) - 4000),
        },
        agent_status_signing_key(ctx),
        purpose="agent-status",
    )
    assert client.get("/oauth/consent/agent/status", params={"st": stale}).status_code == 400

    siblings = [
        ("state", state_signing_key(ctx)),
        ("approval", approval_state_key(ctx)),
        ("login", login_signing_key(ctx)),
        ("session", session_signing_key(ctx)),
        ("agent-create", agent_create_signing_key(ctx)),
    ]
    payload = {
        "ch_digest": hashlib.sha256(_HANDLE.encode()).hexdigest(),
        "agent_id": "agnt_p",
        "iat": str(int(time.time())),
    }
    for purpose, key in siblings:
        foreign = sign_payload(dict(payload), key, purpose=purpose)
        resp = client.get("/oauth/consent/agent/status", params={"st": foreign})
        assert resp.status_code == 400, purpose
    svc.get_agent_status.assert_not_awaited()


# ---------- purposes five & six in the matrix: rejected in ALL directions ----------


def test_agent_create_purpose_rejected_at_every_other_endpoint() -> None:
    """The remaining matrix cells (P3 precedent, #1300 review F4): an
    agent-create blob — which rides in page HTML, the most exposed of the
    six — never opens the login form, the IdP callback, the anonymous
    approval-status poll, or the pending-agent status poll."""
    app = FastAPI()
    app.include_router(authorize.router)
    app.include_router(local_login.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)

    ctx = MagicMock()
    ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        local_login=LocalLoginConfig(enabled=True),
        platform_clients=[],
    )
    ctx.config.admin.auth.jwt_secret.get_secret_value.return_value = _JWT_SECRET
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    client = TestClient(app)

    blob = _mint_blob(ctx)

    # GET /login → invalid_state redirect.
    resp = client.get("/login", params={"ls": blob}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"

    # IdP callback → invalid_state redirect.
    resp = client.get(
        "/oauth/callback",
        params={"code": "upstream-code", "state": blob},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"

    # Anonymous approval-status poll → 400.
    resp = client.get("/oauth/approval/status", params={"st": blob})
    assert resp.status_code == 400

    # Pending-agent status poll (the sixth purpose's own door) → 400.
    resp = client.get("/oauth/consent/agent/status", params={"st": blob})
    assert resp.status_code == 400


def test_agent_status_purpose_rejected_at_every_other_endpoint() -> None:
    """The sixth purpose's reverse directions: an agent-status blob — which
    also rides in page HTML — never opens the login form, the IdP callback,
    the anonymous approval-status poll, or the create submit."""
    app = FastAPI()
    app.include_router(authorize.router)
    app.include_router(local_login.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)

    ctx = MagicMock()
    ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        local_login=LocalLoginConfig(enabled=True),
        platform_clients=[],
    )
    ctx.config.admin.auth.jwt_secret.get_secret_value.return_value = _JWT_SECRET
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    client = TestClient(app)

    blob = _mint_status_blob(ctx)

    resp = client.get("/login", params={"ls": blob}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"

    resp = client.get(
        "/oauth/callback",
        params={"code": "upstream-code", "state": blob},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"

    resp = client.get("/oauth/approval/status", params={"st": blob})
    assert resp.status_code == 400

    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": blob, "agent_name": "nope"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_consent"


def test_agent_create_key_is_purpose_derived() -> None:
    """The fifth purpose has its own derived key: a blob whose _purpose field
    says "agent-create" but which was signed with any sibling key fails
    verification — forging the discriminator alone is never enough."""
    ctx = MagicMock()
    ctx.config.admin.auth.jwt_secret.get_secret_value.return_value = _JWT_SECRET
    payload = {
        "ch_digest": hashlib.sha256(_HANDLE.encode()).hexdigest(),
        "user_key": "idp:ext-create-1",
        "iat": str(int(time.time())),
    }
    for wrong_key in (
        state_signing_key(ctx),
        approval_state_key(ctx),
        login_signing_key(ctx),
        session_signing_key(ctx),
        agent_status_signing_key(ctx),
    ):
        forged = sign_payload(dict(payload), wrong_key, purpose="agent-create")
        with pytest.raises(InvalidGrantError):
            verify_payload(
                forged, agent_create_signing_key(ctx), purpose="agent-create", max_age=300
            )


def test_agent_status_key_is_purpose_derived() -> None:
    """Same discipline for the sixth purpose: forging _purpose="agent-status"
    under any sibling key (including agent-create's) fails verification."""
    ctx = MagicMock()
    ctx.config.admin.auth.jwt_secret.get_secret_value.return_value = _JWT_SECRET
    payload = {
        "ch_digest": hashlib.sha256(_HANDLE.encode()).hexdigest(),
        "agent_id": "agnt_p",
        "iat": str(int(time.time())),
    }
    for wrong_key in (
        state_signing_key(ctx),
        approval_state_key(ctx),
        login_signing_key(ctx),
        session_signing_key(ctx),
        agent_create_signing_key(ctx),
    ):
        forged = sign_payload(dict(payload), wrong_key, purpose="agent-status")
        with pytest.raises(InvalidGrantError):
            verify_payload(
                forged, agent_status_signing_key(ctx), purpose="agent-status", max_age=300
            )
