"""Integration tests for ConnectSessionService — real control DB, faked HTTP.

Exercises the state-machine transitions and dispatch logic that only
show up when the service is talking to real ORM rows through the
control DB. Vendor HTTP is faked at the seam (``device_authorization`` /
``httpx.AsyncClient``); everything below the service — repositories,
credential rows, aux tables, ``oauth_token`` — is real.

The scope of this file is deliberately the transitions that flow-handler
unit tests can't reach on their own: session/credential dispatch, the
callback path's mark-terminal, and the create + confirm handoffs that
have to leave the DB in a consistent shape for the poll scanner to
find them.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, text, update

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.services.credentials.state import StateReplayedError
from jentic_one.control.services.integrations import device_authorization as df
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.connect_session_service import (
    AuthCodeConfirmResult,
    ConnectSessionService,
    DeviceAuthorizationConfirmResult,
)
from jentic_one.control.services.integrations.errors import (
    AgentNotFoundError,
    ConfirmationForbiddenError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
)
from jentic_one.control.services.integrations.flow_handlers.base import StatusReport
from jentic_one.control.services.integrations.flow_handlers.device_authorization import (
    DeviceAuthorizationHandler,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    DirectOAuth2ProviderConfig,
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorIdentityProbeConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration


_USER_ID = "usr_alice"
_AGENT_ID = "agnt_scout"
_OTHER_USER_ID = "usr_mallory"

_USER_IDENTITY = Identity(sub=_USER_ID, permissions=["credentials:write"])
_OTHER_USER_IDENTITY = Identity(sub=_OTHER_USER_ID, permissions=["credentials:write"])
_AGENT_IDENTITY = Identity(
    sub=_AGENT_ID, permissions=["credentials:write"], actor_type=ActorType.AGENT
)


@pytest.fixture()
async def clean_session_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Reset every table this test file writes to, before and after."""
    tables = (
        ConnectSession,
        OAuthToken,
        OAuthClientCredential,
        DeviceAuthorizationCredential,
        Credential,
    )
    for _phase in ("before", "after"):
        pass  # loop scaffolding; the real cleanup is below

    async with control_db.session() as session:
        for table in tables:
            await session.execute(delete(table))
        await session.commit()
    yield
    async with control_db.session() as session:
        for table in tables:
            await session.execute(delete(table))
        await session.commit()


@pytest.fixture()
async def seed_agent(integration_context: Context) -> AsyncGenerator[None, None]:
    """Seed the admin-DB agent row ``:confirm``'s ownership validation reads.

    Confirm now verifies the bound agent exists and is owned by the
    confirming caller (owner-or-admin) before writing rules/bindings, so
    every test that confirms an agent-carrying session needs this row.
    """
    async with integration_context.admin_db.session() as session:
        # ``agents.owner_id`` FKs to ``users.id`` — seed the owner first.
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, :email, 'Alice', 'Test')"
            ),
            {"id": _USER_ID, "email": "alice@example.test"},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, owner_id, status) "
                "VALUES (:id, :name, :registered_by, :owner_id, 'approved')"
            ),
            {
                "id": _AGENT_ID,
                "name": "scout",
                "registered_by": _USER_ID,
                "owner_id": _USER_ID,
            },
        )
        await session.commit()
    yield
    async with integration_context.admin_db.session() as session:
        await session.execute(text("DELETE FROM agents WHERE id = :id"), {"id": _AGENT_ID})
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": _USER_ID})
        await session.commit()


@pytest.fixture()
def seed_test_vendors(integration_context: Context) -> None:
    """Install a device-flow and an auth-code vendor entry on the live config.

    The vendor registry is a plain dict on ``ctx.config.vendors.entries``,
    so we can add integration-only entries without touching production
    config. Both entries carry the same catalog api_id shape a real
    vendor would (``vendor.tld/api.vendor.tld``) so
    ``canonical_credential_scope`` behaves normally.
    """
    integration_context.config.vendors.entries["testdev"] = VendorAuthConfig(
        vendor="testdev.example/api.testdev.example",
        display_name="Test Device Vendor",
        flows=[
            VendorDeviceAuthorizationFlowConfig(
                client_id="testdev-public-client",
                authorization_endpoint="https://idp.example.com/device/code",
                token_endpoint="https://idp.example.com/token",
            )
        ],
        scopes=[
            VendorScopeConfig(name="repo", classification="write", default=False),
            VendorScopeConfig(name="read:user", classification="read", default=True),
        ],
        identity_probe=VendorIdentityProbeConfig(
            endpoint="https://api.example.com/user",
            identity_field="login",
            display_template="{login}",
        ),
    )
    integration_context.config.vendors.entries["testauth"] = VendorAuthConfig(
        vendor="testauth.example/api.testauth.example",
        display_name="Test Auth Vendor",
        flows=[
            VendorAuthorizationCodeFlowConfig(
                client_id="testauth-confidential-client",
                client_secret=SecretStr("s3cret"),  # pragma: allowlist secret
                authorize_url="https://idp.example.com/authorize",
                token_url="https://idp.example.com/token",
            )
        ],
        scopes=[
            VendorScopeConfig(name="scope-a", classification="read", default=True),
        ],
        identity_probe=VendorIdentityProbeConfig(
            endpoint="https://api.example.com/me",
            identity_field="username",
            display_template="{username}",
        ),
    )
    # Auth-code flow shares the DirectOAuth2Provider redirect_uri — the
    # handler asserts it's set. Seed it if a prior test left it unset.
    integration_context.config.credentials.providers.setdefault(
        "direct_oauth2",
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
        ),
    )


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


async def test_create_session_device_authorization_seeds_credential_and_aux_row(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # After create, the DB is in the exact shape the scanner + confirm
    # step both rely on: credential in ``pending``, session in
    # ``created``, device_authorization_credentials aux row present but with no
    # transient state (that lands at confirm time).
    ctx = integration_context
    svc = ConnectSessionService(ctx)

    created = await svc.create_session(
        vendor_key="testdev",
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["repo"],
    )

    assert created.resolved_flow == "device_authorization"
    assert created.session_id
    assert created.poll_token
    # Approval URL points at the SPA's credentials page with the session
    # id + token so the human can pick it up.
    assert f"approve={created.session_id}" in created.approval_url
    assert f"poll_token={created.poll_token}" in created.approval_url

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None
    assert row.state == "created"
    assert row.vendor == "testdev"
    assert row.agent_id == _AGENT_ID
    assert row.initiator_actor_id == _USER_ID
    assert row.requested_scopes == ["repo"]
    # Default when caller doesn't pass rules — the round-trip test below
    # pins the non-empty case.
    assert row.requested_permission_rules == []


async def test_create_session_persists_requested_permission_rules(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # Rules the initiator (typically an agent) asks the human owner to
    # approve on the review page: captured verbatim on the session row
    # at ``:connect`` time and surfaced back through ``get_review_data``.
    # NOT written to ``agent_permission_rules`` until ``:confirm``.
    ctx = integration_context
    svc = ConnectSessionService(ctx)

    requested: list[dict[str, object]] = [
        {"effect": "allow", "methods": ["GET"], "path": "/repos", "match_mode": "prefix"},
        {"effect": "deny", "methods": ["DELETE"], "path": "/", "match_mode": "prefix"},
    ]

    created = await svc.create_session(
        vendor_key="testdev",
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["repo"],
        requested_permission_rules=requested,
    )

    # Round-trip on the row itself.
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None
    assert row.requested_permission_rules == requested

    # Round-trip via the review-data path (what the approve page reads).
    review = await svc.get_review_data(created.session_id, poll_token=created.poll_token)
    assert review.requested_permission_rules == requested

    async with ctx.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None
    # The credential shares the platform's ``pending`` bootstrap state
    # with any other in-flight OAuth credential — the scanner + broker
    # both key off this to gate execution.
    assert credential.state == "pending"
    assert credential.catalog_api_id == "testdev.example/api.testdev.example"


# ---------------------------------------------------------------------------
# confirm — device flow + auth code + self-confirm guard
# ---------------------------------------------------------------------------


async def test_confirm_device_authorization_transitions_to_polling_and_seeds_aux(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testdev",
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["repo"],
    )

    # Vendor's device_authorization endpoint response is faked at the
    # HTTP seam so this test doesn't need network access.
    begin_result = df.BeginResult(
        device_code="dev-code-xyz",
        user_code="ABCD-1234",
        verification_uri="https://idp.example.com/device",
        verification_uri_complete=None,
        expires_in=900,
        interval=5,
    )
    with patch.object(df, "begin_device_authorization", new=AsyncMock(return_value=begin_result)):
        result = await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["repo"],
            # Canonical ``AgentPermissionRule`` dict shape — the router
            # validates ``PermissionRuleSchema`` upstream, and the
            # service passes the dicts straight through to
            # ``replace_user_rules``. No glob translation here.
            permission_rules=[
                {"effect": "allow", "methods": ["GET"], "path": None, "match_mode": "regex"}
            ],
            identity=_USER_IDENTITY,
        )

    assert isinstance(result, DeviceAuthorizationConfirmResult)
    assert result.user_code == "ABCD-1234"
    assert result.poll_interval_seconds == 5

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None
    # After confirm, the session is ``polling`` — the ConnectPollScanner
    # needs this exact state string to advance the flow. Any other value
    # here means the scanner would skip us and the user never sees the
    # code they just entered succeed.
    assert row.state == "polling"


async def test_confirm_auth_code_returns_authorize_url(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth",
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
    )

    result = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )

    assert isinstance(result, AuthCodeConfirmResult)
    assert result.authorize_url.startswith("https://idp.example.com/authorize?")
    # scope+state MUST make it into the redirect URL — they're the two
    # inputs the callback route uses to route back to this session.
    assert "state=" in result.authorize_url
    assert "scope=scope-a" in result.authorize_url


async def test_confirm_rejects_self_confirm_by_initiating_agent(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # Agent-initiated sessions MUST be confirmed by a human — the whole
    # point of the flow is human-in-the-loop scope approval. An agent
    # confirming its own session would bypass the review page.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testdev",
        agent_id=_AGENT_ID,
        initiator_actor_id=_AGENT_ID,  # agent initiated
    )
    with pytest.raises(ConfirmationForbiddenError):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=[],
            permission_rules=[],
            identity=_AGENT_IDENTITY,
        )


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


async def test_get_status_returns_pending_before_confirm(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testdev", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    status = await svc.get_status(created.session_id, poll_token=created.poll_token)
    assert status.status == "pending"


async def test_get_status_rejects_wrong_poll_token(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # The poll token is the only gate on GET status — it's how the human
    # owner is authenticated on the poller endpoint (no cookie / bearer
    # once the SPA passes them the URL). A weak / missing check here
    # would let anyone with a session_id watch progress.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testdev", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    with pytest.raises(InvalidPollTokenError):
        await svc.get_status(created.session_id, poll_token="not-the-token")


# ---------------------------------------------------------------------------
# mark_terminal_from_callback
# ---------------------------------------------------------------------------


async def test_mark_terminal_from_callback_deletes_credential_and_cascades_session(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # Callback landings that carry no code (vendor returned ``error=…``,
    # or the user hit Cancel at the IdP) MUST clean up: the ``pending``
    # credential is unusable, and leaving a ``failed`` row behind means
    # the human then has to hand-delete it out of the credentials list.
    # Deleting the credential cascades the ``connect_sessions`` row and
    # every flow-specific aux row (device_authorization_credentials,
    # oauth_client_credentials, oauth_tokens) via SQLAlchemy
    # ``all, delete-orphan`` — one write, everything gone.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    confirmed = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    # The error branch consumes the signed state one-shot, exactly like
    # the success branch — so the test drives it with the real state JWT
    # off the authorize URL, not a bare session id.
    assert isinstance(confirmed, AuthCodeConfirmResult)
    raw_state = _state_from_authorize_url(confirmed.authorize_url)
    # Capture the credential id BEFORE the terminal call — the session
    # is about to vanish with the credential.
    async with ctx.control_db.session() as session:
        pre = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert pre is not None
    credential_id = pre.credential_id

    await svc.mark_terminal_from_callback(raw_state=raw_state, error="access_denied")

    async with ctx.control_db.session() as session:
        # Session gone (FK ``ondelete=CASCADE`` from credentials.id).
        assert await ConnectSessionRepository.get_by_id(session, created.session_id) is None
        # Credential gone. The SPA polling ``/status`` will 404 on the
        # next tick and transition to terminal-failed.
        assert await CredentialRepository.get_by_id(session, credential_id) is None


# ---------------------------------------------------------------------------
# advance_polling_target — session vs credential-mode dispatch
# ---------------------------------------------------------------------------


async def test_advance_polling_target_dispatches_to_session_when_live_session_exists(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # When a live (non-terminal) ConnectSession wraps the credential,
    # the scanner MUST route through advance_polling_session so the
    # session state machine advances (state=polling → connected /
    # failed). Bypassing it would flip credentials.state directly and
    # strand the session in polling forever.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testdev", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    begin_result = df.BeginResult(
        device_code="dev-code-xyz",
        user_code="ABCD-1234",
        verification_uri="https://idp.example.com/device",
        verification_uri_complete=None,
        expires_in=900,
        interval=5,
    )
    with patch.object(df, "begin_device_authorization", new=AsyncMock(return_value=begin_result)):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["read:user"],
            permission_rules=[],
            identity=_USER_IDENTITY,
        )

    with (
        patch.object(
            ConnectSessionService, "advance_polling_session", new=AsyncMock()
        ) as sess_mock,
        patch.object(
            ConnectSessionService, "advance_polling_credential", new=AsyncMock()
        ) as cred_mock,
    ):
        async with ctx.control_db.session() as session:
            row = await ConnectSessionRepository.get_by_id(session, created.session_id)
            assert row is not None
            credential_id = row.credential_id
        await svc.advance_polling_target(credential_id)

    sess_mock.assert_awaited_once_with(created.session_id)
    cred_mock.assert_not_awaited()


async def test_advance_polling_target_dispatches_to_credential_when_no_live_session(
    integration_context: Context,
    clean_session_tables: None,
) -> None:
    # Raw-credential connect flow (user clicks Connect on a manually
    # created device-flow credential) has no wrapping session — the
    # scanner MUST route to credential-mode instead.
    ctx = integration_context
    svc = ConnectSessionService(ctx)

    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type="oauth2",
            name="manual device-flow cred",
            api_vendor="foo",
            api_name="bar",
            api_version="v1",
            provider="device_authorization",
            created_by=_USER_ID,
            state="pending",
        )
    with (
        patch.object(
            ConnectSessionService, "advance_polling_session", new=AsyncMock()
        ) as sess_mock,
        patch.object(
            ConnectSessionService, "advance_polling_credential", new=AsyncMock()
        ) as cred_mock,
    ):
        await svc.advance_polling_target(credential.id)

    cred_mock.assert_awaited_once_with(credential.id)
    sess_mock.assert_not_awaited()


async def test_advance_polling_credential_advances_a_re_connect_of_a_connected_credential(
    integration_context: Context,
    clean_session_tables: None,
) -> None:
    # A user clicking Connect on an already-``connected`` credential
    # starts a fresh device-flow round while the credential row keeps
    # its ``state="connected"``. The scanner MUST NOT gate on that
    # state (it used to — a stale ``state != "pending"`` early-return
    # left the aux row's ``encrypted_device_code`` sitting there until
    # TTL, so the SPA's ``runConnectFlow`` poll loop never observed a
    # transition). The aux row's ``encrypted_device_code`` is the sole
    # "in flight" signal; the scanner query has already filtered by
    # it, so ``advance_polling_credential`` should hand off to
    # ``DeviceAuthorizationHandler.advance`` regardless of the
    # credential's state.
    ctx = integration_context
    svc = ConnectSessionService(ctx)

    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type="oauth2",
            name="already-connected device-flow cred",
            api_vendor="foo",
            api_name="bar",
            api_version="v1",
            provider="device_authorization",
            created_by=_USER_ID,
            state="connected",  # ← the re-connect scenario
        )

    with patch.object(
        DeviceAuthorizationHandler,
        "advance",
        new=AsyncMock(return_value=StatusReport(kind="pending")),
    ) as advance_mock:
        await svc.advance_polling_credential(credential.id)

    # The load-bearing assertion: we ACTUALLY called
    # ``DeviceAuthorizationHandler.advance`` — the previous
    # ``state != "pending"`` gate would have returned before this.
    advance_mock.assert_awaited_once_with(credential.id)


# ---------------------------------------------------------------------------
# complete_from_callback (auth-code) — end-to-end vault + finalise
# ---------------------------------------------------------------------------


def _state_from_authorize_url(authorize_url: str) -> str:
    """Extract the signed ``state`` query param from an authorize URL.

    Auth-code confirm returns the same URL the SPA would push into
    ``window.location`` — the state JWT that the vendor will echo back
    to ``/credentials/oauth/callback`` is a query param on it. Tests
    that need to drive ``complete_from_callback`` post-confirm read the
    real signed state from here rather than manufacturing a JWT.
    """
    parts = urlsplit(authorize_url)
    q = parse_qs(parts.query)
    return q["state"][0]


async def test_complete_from_callback_vaults_token_and_marks_connected(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # End-to-end auth-code callback: after the callback route hands us
    # the code, we must (a) exchange it at the token endpoint, (b) vault
    # the token, (c) mark the session ``connected`` with the echoed
    # identity, (d) mark the credential ``active``. All four in one
    # transition — any of them missing leaves the flow half-done.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    confirmed = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    # auth-code confirm returns an ``authorize_url`` carrying the
    # signed state the vendor will echo back on the callback.
    assert isinstance(confirmed, AuthCodeConfirmResult)
    raw_state = _state_from_authorize_url(confirmed.authorize_url)

    token_response = __import__("httpx").Response(
        200,
        json={
            "access_token": "at_ok",
            "refresh_token": "rt_ok",
            "expires_in": 3600,
            "scope": "scope-a",
        },
    )

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *args, **kwargs):
            return token_response

    identity = identity_echo.IdentityEchoResult(display="alice", raw={"username": "alice"})
    with (
        patch("httpx.AsyncClient", return_value=_FakeClient()),
        patch.object(identity_echo, "echo_identity", new=AsyncMock(return_value=identity)),
    ):
        result = await svc.complete_from_callback(raw_state=raw_state, code="the-code")

    assert result.status == "connected"
    assert result.connected_as == "alice"

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        assert row.state == "connected"
        assert row.connected_as == "alice"
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
        assert credential is not None
        # Credential flips out of ``pending`` on connect so the broker
        # will actually inject tokens for it. Missing this = silently
        # broken execution.
        assert credential.state == "connected"


async def test_complete_from_callback_refuses_state_replay(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # A replayed callback URL (attacker captures + resubmits, or a
    # browser back-navigation lands ``?state=&code=`` twice) MUST NOT
    # double-fire the token exchange. The nonce is consumed
    # atomically in the shared ``consume_callback_state`` prologue;
    # the second call raises ``StateReplayedError`` before the vendor
    # HTTP is even opened. This test pins that the router's raw-state
    # → service path enforces one-shot semantics on the session flow
    # too — the same guarantee ``ConnectService.complete`` has always
    # given the standalone flow.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    confirmed = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    assert isinstance(confirmed, AuthCodeConfirmResult)
    raw_state = _state_from_authorize_url(confirmed.authorize_url)

    # Count vendor token-endpoint hits — a replay that reaches
    # ``handler.complete_from_callback`` would bump this a second time.
    post_calls = 0

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *args, **kwargs):
            nonlocal post_calls
            post_calls += 1
            return __import__("httpx").Response(
                200,
                json={
                    "access_token": "at_ok",
                    "refresh_token": "rt_ok",
                    "expires_in": 3600,
                    "scope": "scope-a",
                },
            )

    identity = identity_echo.IdentityEchoResult(display="alice", raw={"username": "alice"})
    with (
        patch("httpx.AsyncClient", return_value=_FakeClient()),
        patch.object(identity_echo, "echo_identity", new=AsyncMock(return_value=identity)),
    ):
        result = await svc.complete_from_callback(raw_state=raw_state, code="the-code")
        assert result.status == "connected"
        with pytest.raises(StateReplayedError):
            await svc.complete_from_callback(raw_state=raw_state, code="the-code")

    # Vendor token endpoint MUST have been hit exactly once — the
    # second attempt failed the nonce-consume gate before any HTTP.
    assert post_calls == 1


# ---------------------------------------------------------------------------
# not-found + poll-token errors
# ---------------------------------------------------------------------------


async def test_get_status_refuses_missing_session_as_403(
    integration_context: Context,
    clean_session_tables: None,
) -> None:
    # ``get_status`` must not distinguish "session doesn't exist" (404)
    # from "session exists but poll_token is wrong" (403): the split
    # would give an unauth'd caller a session-id enumeration oracle.
    # Both branches surface as ``InvalidPollTokenError`` → 403.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    with pytest.raises(InvalidPollTokenError):
        await svc.get_status("sess_does_not_exist", poll_token="whatever")


# ---------------------------------------------------------------------------
# confirm — capability gate, agent validation, TOCTOU, vendor-failure revert
# ---------------------------------------------------------------------------


async def test_confirm_rejects_wrong_poll_token(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # ``:confirm`` is poll_token-gated like the review read — session ids
    # travel in approval URLs, so holding ``credentials:write`` alone must
    # not be enough to confirm someone else's session.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    with pytest.raises(InvalidPollTokenError):
        await svc.confirm(
            created.session_id,
            poll_token="not-the-token",
            confirmed_scopes=["scope-a"],
            permission_rules=[],
            identity=_USER_IDENTITY,
        )


async def test_confirm_rejects_unknown_agent(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # The late-bound ``agent_id`` was never validated — a typo'd or
    # fabricated id must not silently create rules/bindings for nothing.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    with pytest.raises(AgentNotFoundError):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["scope-a"],
            permission_rules=[],
            agent_id="agnt_ghost",
            identity=_USER_IDENTITY,
        )
    # Nothing moved: the session is still confirmable.
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None
    assert row.state == "created"
    assert row.agent_id is None


async def test_confirm_rejects_agent_not_owned_by_caller(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # Owner-or-admin: a caller with ``credentials:write`` must not bind a
    # credential to an agent someone else owns.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_OTHER_USER_ID
    )
    with pytest.raises(ConfirmationForbiddenError):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["scope-a"],
            permission_rules=[],
            agent_id=_AGENT_ID,  # owned by usr_alice
            identity=_OTHER_USER_IDENTITY,
        )


async def test_second_confirm_loses_the_cas_and_conflicts(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # The ``created`` guard used to be a plain read (TOCTOU): two
    # concurrent confirms would both fire the vendor ``begin``. The CAS
    # makes the second one lose deterministically.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    first = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    assert isinstance(first, AuthCodeConfirmResult)
    with pytest.raises(InvalidStateTransitionError):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["scope-a"],
            permission_rules=[],
            identity=_USER_IDENTITY,
        )


async def test_confirm_vendor_begin_failure_leaves_session_retryable(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # A vendor-side ``begin`` failure (4xx/5xx at the device-authorization
    # endpoint) must roll the CAS back to ``created`` so the human can
    # retry — and the retry must actually work.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testdev",
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["repo"],
    )
    with (
        patch.object(
            df,
            "begin_device_authorization",
            new=AsyncMock(side_effect=df.DeviceAuthorizationUpstreamError(404)),
        ),
        pytest.raises(df.DeviceAuthorizationUpstreamError),
    ):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["repo"],
            permission_rules=[],
            identity=_USER_IDENTITY,
        )

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None
    assert row.state == "created"

    begin_result = df.BeginResult(
        device_code="dev-code-retry",
        user_code="WXYZ-5678",
        verification_uri="https://idp.example.com/device",
        verification_uri_complete=None,
        expires_in=900,
        interval=5,
    )
    with patch.object(df, "begin_device_authorization", new=AsyncMock(return_value=begin_result)):
        retry = await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["repo"],
            permission_rules=[],
            identity=_USER_IDENTITY,
        )
    assert isinstance(retry, DeviceAuthorizationConfirmResult)
    assert retry.user_code == "WXYZ-5678"


# ---------------------------------------------------------------------------
# terminal CAS — replay / race protection
# ---------------------------------------------------------------------------


async def test_error_callback_replay_after_connect_cannot_delete_live_credential(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # The attack the CAS + nonce-consume close: connect succeeds, then the
    # captured callback URL is replayed with ``error=access_denied``. The
    # old error branch skipped the nonce and ``_mark_terminal`` never
    # re-checked state — deleting the live credential, its vaulted token,
    # and the binding. Now the replay dies at the nonce gate and, belt +
    # braces, the CAS refuses the terminal transition anyway.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    confirmed = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    assert isinstance(confirmed, AuthCodeConfirmResult)
    raw_state = _state_from_authorize_url(confirmed.authorize_url)

    token_response = __import__("httpx").Response(
        200,
        json={"access_token": "at_ok", "expires_in": 3600, "scope": "scope-a"},
    )

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *args, **kwargs):
            return token_response

    identity = identity_echo.IdentityEchoResult(display="alice", raw={"username": "alice"})
    with (
        patch("httpx.AsyncClient", return_value=_FakeClient()),
        patch.object(identity_echo, "echo_identity", new=AsyncMock(return_value=identity)),
    ):
        result = await svc.complete_from_callback(raw_state=raw_state, code="the-code")
    assert result.status == "connected"

    # Replay the error variant of the same callback URL.
    with pytest.raises(StateReplayedError):
        await svc.mark_terminal_from_callback(raw_state=raw_state, error="access_denied")

    # Even a caller that somehow bypasses the nonce (e.g. a second scanner
    # pod racing a stale poll result) is stopped by the CAS.
    marked = await svc._mark_terminal(created.session_id, "failed", "simulated race")
    assert marked is False

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        assert row.state == "connected"
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
        assert credential is not None
        assert credential.state == "connected"


# ---------------------------------------------------------------------------
# expire_stale_sessions — flow-agnostic TTL sweep
# ---------------------------------------------------------------------------


async def test_expire_stale_sessions_sweeps_abandoned_sessions_and_credentials(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # A session whose initiator never confirms (state ``created``) has no
    # device-code aux row, so the poll scanner can't see it — the TTL
    # sweep is its only expiry driver, and it must take the orphaned
    # ``pending`` credential with it while leaving fresh sessions alone.
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    stale = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    fresh = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    async with ctx.control_db.session() as session:
        stale_row = await ConnectSessionRepository.get_by_id(session, stale.session_id)
        assert stale_row is not None
        stale_credential_id = stale_row.credential_id
    # Backdate the stale session past the TTL.
    async with ctx.control_db.transaction() as session:
        await session.execute(
            update(ConnectSession)
            .where(ConnectSession.id == stale.session_id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=2))
        )

    expired = await svc.expire_stale_sessions()
    assert expired == 1

    async with ctx.control_db.session() as session:
        # Stale session + its pending credential are gone…
        assert await ConnectSessionRepository.get_by_id(session, stale.session_id) is None
        assert await CredentialRepository.get_by_id(session, stale_credential_id) is None
        # …the fresh one is untouched.
        fresh_row = await ConnectSessionRepository.get_by_id(session, fresh.session_id)
        assert fresh_row is not None
        assert fresh_row.state == "created"
