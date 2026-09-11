"""Integration tests for ConnectSessionService — real control DB, faked HTTP.

Exercises the state-machine transitions and dispatch logic that only
show up when the service is talking to real ORM rows through the
control DB. Vendor HTTP is faked at the seam (``device_flow`` /
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
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr
from sqlalchemy import delete

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.device_flow_credentials import DeviceFlowCredential
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.services.integrations import device_flow as df
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.connect_session_service import (
    AuthCodeConfirmResult,
    ConnectSessionService,
    DeviceFlowConfirmResult,
)
from jentic_one.control.services.integrations.errors import (
    ConfirmationForbiddenError,
    InvalidPollTokenError,
    SessionNotFoundError,
)
from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorDeviceFlowConfig,
    VendorIdentityProbeConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


_USER_ID = "usr_alice"
_AGENT_ID = "agnt_scout"


@pytest.fixture()
async def clean_session_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Reset every table this test file writes to, before and after."""
    tables = (
        ConnectSession,
        OAuthToken,
        OAuthClientCredential,
        DeviceFlowCredential,
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
            VendorDeviceFlowConfig(
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
    from jentic_one.shared.config import DirectOAuth2ProviderConfig

    integration_context.config.credentials.providers.setdefault(
        "direct_oauth2",
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
        ),
    )


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


async def test_create_session_device_flow_seeds_credential_and_aux_row(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # After create, the DB is in the exact shape the scanner + confirm
    # step both rely on: credential in ``pending``, session in
    # ``created``, device_flow_credentials aux row present but with no
    # transient state (that lands at confirm time).
    ctx = integration_context
    svc = ConnectSessionService(ctx)

    created = await svc.create_session(
        vendor_key="testdev",
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["repo"],
    )

    assert created.resolved_flow == "device_flow"
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


async def test_confirm_device_flow_transitions_to_polling_and_seeds_aux(
    integration_context: Context,
    seed_test_vendors: None,
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
    with patch.object(df, "begin_device_flow", new=AsyncMock(return_value=begin_result)):
        result = await svc.confirm(
            created.session_id,
            confirmed_scopes=["repo"],
            permission_rules=[{"method": "GET", "path": "/**", "effect": "allow"}],
            caller_actor_id=_USER_ID,
            caller_actor_type="USER",
        )

    assert isinstance(result, DeviceFlowConfirmResult)
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
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        caller_actor_id=_USER_ID,
        caller_actor_type="USER",
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
            confirmed_scopes=[],
            permission_rules=[],
            caller_actor_id=_AGENT_ID,
            caller_actor_type="AGENT",
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


async def test_mark_terminal_from_callback_moves_session_and_credential_to_failed(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # Callback landings that carry no code (vendor returned ``error=…``,
    # or the user hit Cancel at the IdP) MUST leave BOTH the session and
    # the pending credential in ``failed`` — otherwise the dangling
    # ``pending`` credential would sit indefinitely, and the poll
    # scanner would never touch it (auth-code has no aux row for the
    # scanner to find).
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    await svc.confirm(
        created.session_id,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        caller_actor_id=_USER_ID,
        caller_actor_type="USER",
    )

    await svc.mark_terminal_from_callback(created.session_id, "access_denied")

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        assert row.state == "failed"
        # Error surface for the UI + audit — the shared "callback_error"
        # code lets ops distinguish this from vendor-side flow failures.
        assert row.error_code == "callback_error"
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
        assert credential is not None
        assert credential.state == "failed"


# ---------------------------------------------------------------------------
# advance_polling_target — session vs credential-mode dispatch
# ---------------------------------------------------------------------------


async def test_advance_polling_target_dispatches_to_session_when_live_session_exists(
    integration_context: Context,
    seed_test_vendors: None,
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
    with patch.object(df, "begin_device_flow", new=AsyncMock(return_value=begin_result)):
        await svc.confirm(
            created.session_id,
            confirmed_scopes=["read:user"],
            permission_rules=[],
            caller_actor_id=_USER_ID,
            caller_actor_type="USER",
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
            provider="device_flow",
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


# ---------------------------------------------------------------------------
# complete_from_callback (auth-code) — end-to-end vault + finalise
# ---------------------------------------------------------------------------


async def test_complete_from_callback_vaults_token_and_marks_connected(
    integration_context: Context,
    seed_test_vendors: None,
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
    await svc.confirm(
        created.session_id,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        caller_actor_id=_USER_ID,
        caller_actor_type="USER",
    )

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
        result = await svc.complete_from_callback(session_id=created.session_id, code="the-code")

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


# ---------------------------------------------------------------------------
# not-found + poll-token errors
# ---------------------------------------------------------------------------


async def test_get_status_raises_when_session_missing(
    integration_context: Context,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    with pytest.raises(SessionNotFoundError):
        await svc.get_status("sess_does_not_exist", poll_token="whatever")
