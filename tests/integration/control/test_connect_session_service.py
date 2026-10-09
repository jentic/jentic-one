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
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.services.event_service import EventService
from jentic_one.admin.services.schemas.events import EventFilter
from jentic_one.control.core.schema.connect_session_outcomes import ConnectSessionOutcome
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.repos.connect_session_outcome_repo import (
    ConnectSessionOutcomeRepository,
)
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.credentials.state import StateReplayedError
from jentic_one.control.services.integrations import device_authorization as df
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.connect_session_service import (
    ApiTarget,
    AuthCodeConfirmResult,
    ConnectSessionService,
    DeviceAuthorizationConfirmResult,
)
from jentic_one.control.services.integrations.errors import (
    AgentInactiveError,
    AgentNotFoundError,
    ConfirmationForbiddenError,
    ConfirmKindMismatchError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    ManualFlowsDisabledError,
    OAuthAppChangedError,
    SecuritySchemesLookupUnavailableError,
)
from jentic_one.control.services.integrations.flow_handlers.base import StatusReport
from jentic_one.control.services.integrations.flow_handlers.device_authorization import (
    DeviceAuthorizationHandler,
)
from jentic_one.control.web.app import get_exception_handlers
from jentic_one.control.web.routers import integrations as integrations_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OWNER_CREDENTIALS_READ
from jentic_one.shared.config import (
    DirectOAuth2ProviderConfig,
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorIdentityProbeConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.crypto import hash_secret
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.web.deps import resolve_identity

pytestmark = pytest.mark.integration


_USER_ID = "usr_alice"
_AGENT_ID = "agnt_scout"
_OTHER_USER_ID = "usr_mallory"

# Confirming for an owned agent needs both write permissions (the bind
# route's own gate is ``agents:write``).
_USER_IDENTITY = Identity(sub=_USER_ID, permissions=["credentials:write", "agents:write"])
_OTHER_USER_IDENTITY = Identity(
    sub=_OTHER_USER_ID, permissions=["credentials:write", "agents:write"]
)
_AGENT_IDENTITY = Identity(
    sub=_AGENT_ID, permissions=["credentials:write"], actor_type=ActorType.AGENT
)


@pytest.fixture()
async def clean_session_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Reset every table this test file writes to, before and after."""
    tables = (
        ConnectSessionOutcome,
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
    # Approval URL points at the SPA's Agents page (where the credential
    # inventory lives) with only the session id: the owner / org:admin acts
    # without the poll token, so the token never rides in a browser URL.
    # Absolute even with no public URL configured: it is relayed out-of-band.
    approval = urlsplit(created.approval_url)
    assert approval.scheme in {"http", "https"}
    assert approval.path == "/app/agents"
    assert approval.query == f"approve={created.session_id}"
    assert "poll_token" not in created.approval_url
    assert created.poll_token not in created.approval_url

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
    # Only the digest of the poll token is persisted; the plaintext is
    # returned once from create and never written to the row.
    assert row.poll_token_hash == hash_secret(created.poll_token)
    assert row.poll_token_hash != created.poll_token


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
    review = await svc.get_review_data(
        created.session_id, poll_token=created.poll_token, identity=_USER_IDENTITY
    )
    assert review.requested_permission_rules == requested

    async with ctx.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None
    # The credential shares the platform's ``pending`` bootstrap state
    # with any other in-flight OAuth credential — the scanner + broker
    # both key off this to gate execution.
    assert credential.state == "pending"
    assert credential.catalog_api_id == "testdev.example/api.testdev.example"


class _RegisteredIdentityImporter:
    """Catalog importer seam that reports one already-imported API identity."""

    def __init__(self, identities: dict[str, tuple[str, str]]) -> None:
        self._identities = identities

    async def ensure_imported(self, *, api_id: str, initiator_actor_id: str) -> str | None:
        return None

    async def registered_identity(self, *, api_id: str) -> tuple[str, str] | None:
        return self._identities.get(api_id)

    async def current_version(self, *, api_id: str) -> str | None:
        return None


async def test_vendor_connect_stamps_the_catalog_import_identity(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # A catalog import names ``testdev.example/api.testdev.example`` by its
    # sub segment (``testdev-example/api-testdev-example``); a vendor connect
    # stamps that identity so the credential covers the registered API.
    ctx = integration_context
    created = await ConnectSessionService(ctx).create_session(
        vendor_key="testdev", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None
    assert (credential.api_vendor, credential.api_name) == (
        "testdev-example",
        "api-testdev-example",
    )


async def test_vendor_connect_stamps_an_already_imported_apis_identity(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # An API imported before the sub-segment naming keeps its whole-id name;
    # the vendor connect follows the registered identity, not the derivation.
    ctx = integration_context
    importer = _RegisteredIdentityImporter(
        {"testdev.example/api.testdev.example": ("testdev-example", "testdev-example-api-x")}
    )
    created = await ConnectSessionService(ctx, catalog_auto_importer=importer).create_session(
        vendor_key="testdev", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None
    assert credential.api_name == "testdev-example-api-x"


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
    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_USER_IDENTITY
    )
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
        await svc.get_status(
            created.session_id, poll_token="not-the-token", identity=_OTHER_USER_IDENTITY
        )


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
        # The outcome outlives the session row.
        outcome = await ConnectSessionOutcomeRepository.get_by_session_id(
            session, created.session_id
        )
    assert outcome is not None
    assert (outcome.outcome, outcome.error_code) == ("failed", "callback_error")
    assert outcome.poll_token_hash == hash_secret(created.poll_token)


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
        outcome = await ConnectSessionOutcomeRepository.get_by_session_id(
            session, created.session_id
        )
    assert outcome is not None
    assert (outcome.outcome, outcome.error_code) == ("connected", None)
    assert (outcome.agent_id, outcome.target_kind, outcome.vendor) == (
        _AGENT_ID,
        "vendor",
        "testauth",
    )


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
        await svc.get_status(
            "sess_does_not_exist", poll_token="whatever", identity=_OTHER_USER_IDENTITY
        )


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
        outcome = await ConnectSessionOutcomeRepository.get_by_session_id(session, stale.session_id)
        assert outcome is not None
        assert outcome.outcome == "expired"
        assert (
            await ConnectSessionOutcomeRepository.get_by_session_id(session, fresh.session_id)
            is None
        )


# ---------------------------------------------------------------------------
# list_all — console list scoping + pagination against real rows
# ---------------------------------------------------------------------------


def _list_identity(
    sub: str,
    permissions: list[str],
    parent_actor_id: str | None = None,
    actor_type: ActorType = ActorType.USER,
) -> Identity:
    return Identity(
        sub=sub,
        email="lister@example.com",
        permissions=permissions,
        actor_type=actor_type,
        parent_actor_id=parent_actor_id,
    )


async def _seed_sessions(svc: ConnectSessionService) -> dict[str, str]:
    """Create three sessions across two initiators; return initiator→session_id."""
    alice = await svc.create_session(
        vendor_key="testdev", agent_id=None, initiator_actor_id=_USER_ID
    )
    mallory = await svc.create_session(
        vendor_key="testdev", agent_id=None, initiator_actor_id=_OTHER_USER_ID
    )
    agent = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )
    return {
        _USER_ID: alice.session_id,
        _OTHER_USER_ID: mallory.session_id,
        _AGENT_ID: agent.session_id,
    }


async def test_list_all_plain_caller_sees_only_own_sessions(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    svc = ConnectSessionService(integration_context)
    ids = await _seed_sessions(svc)

    page = await svc.list_all(
        identity=_list_identity(_USER_ID, ["credentials:read"]),
    )
    assert [s.session_id for s in page.data] == [ids[_USER_ID]]
    assert page.has_more is False
    row = page.data[0]
    assert row.requested_by_actor_id == _USER_ID
    assert row.vendor_key == "testdev"
    assert row.vendor_display_name == "Test Device Vendor"
    async with integration_context.control_db.session() as session:
        stored = await ConnectSessionRepository.get_by_id(session, row.session_id)
    assert stored is not None
    assert row.credential_id == stored.credential_id


async def test_list_all_org_admin_sees_all_sessions(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    svc = ConnectSessionService(integration_context)
    ids = await _seed_sessions(svc)

    page = await svc.list_all(identity=_list_identity("usr_root", ["org:admin"]))
    assert {s.session_id for s in page.data} == set(ids.values())


async def test_list_all_delegated_agent_sees_owner_sessions(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    # An agent holding owner:credentials:read with parent_actor_id set sees
    # its own sessions AND its owner's — but never a stranger's.
    svc = ConnectSessionService(integration_context)
    ids = await _seed_sessions(svc)

    page = await svc.list_all(
        identity=_list_identity(
            _AGENT_ID,
            [OWNER_CREDENTIALS_READ],
            parent_actor_id=_USER_ID,
            actor_type=ActorType.AGENT,
        )
    )
    assert {s.session_id for s in page.data} == {ids[_AGENT_ID], ids[_USER_ID]}


async def test_list_all_filters_by_state_and_paginates(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    ids = await _seed_sessions(svc)

    # Flip one session to a terminal state directly at the repo layer.
    async with ctx.control_db.transaction() as session:
        await ConnectSessionRepository.update_fields(
            session, ids[_USER_ID], state="failed", error_code="vendor_denied"
        )

    admin = _list_identity("usr_root", ["org:admin"])

    failed_page = await svc.list_all(state="failed", identity=admin)
    assert [s.session_id for s in failed_page.data] == [ids[_USER_ID]]
    assert failed_page.data[0].error_code == "vendor_denied"

    created_page = await svc.list_all(state="created", identity=admin)
    assert {s.session_id for s in created_page.data} == {ids[_OTHER_USER_ID], ids[_AGENT_ID]}

    # Keyset pagination: page of 1 exposes has_more + a working next_cursor.
    first = await svc.list_all(limit=1, identity=admin)
    assert len(first.data) == 1
    assert first.has_more is True
    assert first.next_cursor is not None
    rest = await svc.list_all(cursor=first.next_cursor, limit=2, identity=admin)
    assert first.data[0].session_id not in {s.session_id for s in rest.data}
    assert len(rest.data) == 2
    assert rest.has_more is False


# ---------------------------------------------------------------------------
# Owner / org:admin access without the poll token (routes, real DB)
# ---------------------------------------------------------------------------

_ADMIN_IDENTITY = Identity(sub="usr_root", permissions=["org:admin"])
_OWNER_WITHOUT_AGENTS_WRITE = Identity(sub=_USER_ID, permissions=["credentials:write"])
_AGENT_CALLER = Identity(
    sub=_AGENT_ID,
    permissions=["credentials:connect", "credentials:write"],
    actor_type=ActorType.AGENT,
)
_CALLERS: dict[str, Identity] = {
    "owner": _USER_IDENTITY,
    "admin": _ADMIN_IDENTITY,
    "owner_without_agents_write": _OWNER_WITHOUT_AGENTS_WRITE,
    "stranger": _OTHER_USER_IDENTITY,
    "agent": _AGENT_CALLER,
}
_ROUTES = ("review", "status", "confirm", "cancel")
_OK = {"review": 200, "status": 200, "confirm": 200, "cancel": 204}


def _session_app(ctx: Context, identity: Identity) -> FastAPI:
    """The real integrations router over the real service; only identity is stubbed."""
    app = FastAPI()
    app.include_router(integrations_router.router)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.state.ctx = ctx
    app.dependency_overrides[resolve_identity] = lambda: identity
    return app


async def _call_route(
    ctx: Context, identity: Identity, route: str, session_id: str, poll_token: str | None
) -> Response:
    params = {"poll_token": poll_token} if poll_token is not None else {}
    transport = ASGITransport(app=_session_app(ctx, identity))
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        if route == "review":
            return await client.get(f"/connect-sessions/{session_id}", params=params)
        if route == "status":
            return await client.get(f"/connect-sessions/{session_id}/status", params=params)
        if route == "cancel":
            return await client.post(f"/connect-sessions/{session_id}:cancel", params=params)
        return await client.post(
            f"/connect-sessions/{session_id}:confirm",
            params=params,
            json={"confirmed_scopes": ["scope-a"], "permission_rules": []},
        )


def _expected_status(caller: str, route: str, *, with_token: bool) -> int:
    """Token holders pass the session gate; without it only owner (both writes) / admin do.

    Confirm then applies its own rules to everyone who got through: an agent
    cannot confirm an agent-started session, and approving for the agent
    needs ownership plus ``credentials:write`` and ``agents:write`` (or
    ``org:admin``).
    """
    approver = caller in ("owner", "admin")
    if not with_token and not approver:
        return 403
    if route == "confirm" and not approver:
        return 403
    return _OK[route]


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("caller", sorted(_CALLERS))
@pytest.mark.parametrize("with_token", [True, False], ids=["token", "no_token"])
async def test_session_routes_accept_token_or_owner_or_admin(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
    route: str,
    caller: str,
    with_token: bool,
) -> None:
    ctx = integration_context
    created = await ConnectSessionService(ctx).create_session(
        vendor_key="testauth",
        agent_id=_AGENT_ID,
        initiator_actor_id=_AGENT_ID,
        requested_scopes=["scope-a"],
    )
    resp = await _call_route(
        ctx,
        _CALLERS[caller],
        route,
        created.session_id,
        created.poll_token if with_token else None,
    )
    assert resp.status_code == _expected_status(caller, route, with_token=with_token), resp.text


@pytest.mark.parametrize("route", _ROUTES)
async def test_session_routes_refuse_uniformly_without_token(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
    route: str,
) -> None:
    # A stranger probing a real session and an owner probing a missing id
    # get byte-identical 403 bodies: no session-id enumeration oracle.
    ctx = integration_context
    created = await ConnectSessionService(ctx).create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )
    stranger = await _call_route(ctx, _OTHER_USER_IDENTITY, route, created.session_id, None)
    missing = await _call_route(ctx, _USER_IDENTITY, route, "cs_does_not_exist", None)
    wrong = await _call_route(ctx, _OTHER_USER_IDENTITY, route, created.session_id, "nope")
    assert stranger.status_code == missing.status_code == wrong.status_code == 403
    assert stranger.json() == {**missing.json(), "instance": stranger.json()["instance"]}
    assert stranger.json()["detail"] == wrong.json()["detail"]
    assert stranger.json()["type"] == "invalid_poll_token"


async def test_owner_without_token_cannot_act_on_ownerless_agent_session(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # An unclaimed agent has no owner; only org:admin may act on its sessions.
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await session.execute(
            text("UPDATE agents SET owner_id = NULL WHERE id = :id"), {"id": _AGENT_ID}
        )
        await session.commit()
    created = await ConnectSessionService(ctx).create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )
    owner = await _call_route(ctx, _USER_IDENTITY, "review", created.session_id, None)
    admin = await _call_route(ctx, _ADMIN_IDENTITY, "review", created.session_id, None)
    assert owner.status_code == 403
    assert admin.status_code == 200


# ---------------------------------------------------------------------------
# Confirm: attribution to the approver, unusable agents refused
# ---------------------------------------------------------------------------


@pytest.fixture()
async def agent_grants(integration_context: Context) -> AsyncGenerator[list[str], None]:
    """Permissions to grant the seeded agent directly (``actor_permission_grants``)."""
    granted: list[str] = []
    yield granted
    async with integration_context.admin_db.session() as session:
        await session.execute(
            text("DELETE FROM actor_permission_grants WHERE actor_id = :id"), {"id": _AGENT_ID}
        )
        await session.commit()


async def _grant_agent(ctx: Context, permission: str) -> None:
    async with ctx.admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO actor_permission_grants "
                "(id, actor_id, actor_type, permission, created_by) "
                "VALUES (:grant_id, :id, 'agent', :permission, :created_by)"
            ),
            {
                "grant_id": generate_ksuid("asg"),
                "id": _AGENT_ID,
                "permission": permission,
                "created_by": _USER_ID,
            },
        )
        await session.commit()


async def _credential_created_by(ctx: Context, session_id: str) -> str | None:
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, session_id)
        assert row is not None
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None
    return credential.created_by


@pytest.mark.parametrize(
    ("agent_permissions", "expected_creator"),
    [
        ([], _USER_ID),
        (["credentials:connect"], _USER_ID),
        (["credentials:write"], _USER_ID),
        (["org:admin"], _USER_ID),
    ],
    ids=["no-grants", "connect-only", "credentials-write", "org-admin"],
)
async def test_confirm_attributes_agent_started_credential_to_the_approver(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    agent_grants: list[str],
    clean_session_tables: None,
    agent_permissions: list[str],
    expected_creator: str,
) -> None:
    # The approver always becomes the credential's creator, whatever the
    # initiating agent's own permissions.
    ctx = integration_context
    for permission in agent_permissions:
        await _grant_agent(ctx, permission)
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )
    assert await _credential_created_by(ctx, created.session_id) == _AGENT_ID

    await svc.confirm(
        created.session_id,
        poll_token=None,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    assert await _credential_created_by(ctx, created.session_id) == expected_creator


async def test_confirm_attributes_user_started_credential_to_the_approver(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    await svc.confirm(
        created.session_id,
        poll_token=None,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_ADMIN_IDENTITY,
    )
    assert await _credential_created_by(ctx, created.session_id) == _ADMIN_IDENTITY.sub


@pytest.mark.parametrize("status", ["archived", "disabled", "rejected"])
async def test_confirm_refuses_an_unusable_agent(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
    status: str,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )
    async with ctx.admin_db.session() as session:
        await session.execute(
            text("UPDATE agents SET status = :status WHERE id = :id"),
            {"status": status, "id": _AGENT_ID},
        )
        await session.commit()

    for identity in (_USER_IDENTITY, _ADMIN_IDENTITY):
        with pytest.raises(AgentInactiveError):
            await svc.confirm(
                created.session_id,
                poll_token=created.poll_token,
                confirmed_scopes=["scope-a"],
                permission_rules=[],
                identity=identity,
            )
    # Nothing moved: no CAS, no binding, credential still the agent's.
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None
    assert row.state == "created"
    assert await _credential_created_by(ctx, created.session_id) == _AGENT_ID


async def test_confirm_with_token_requires_agents_write_for_the_owner(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    # Holding the token is not enough to bind for the agent: the owner
    # approver needs agents:write too (the bind route's own gate).
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )
    with pytest.raises(ConfirmationForbiddenError):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["scope-a"],
            permission_rules=[],
            identity=_OWNER_WITHOUT_AGENTS_WRITE,
        )


# ---------------------------------------------------------------------------
# Owner visibility (read-only owned-agent scoping axis)
# ---------------------------------------------------------------------------


async def test_list_all_owner_sees_sessions_of_owned_agents(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    svc = ConnectSessionService(integration_context)
    ids = await _seed_sessions(svc)

    owner_page = await svc.list_all(identity=_list_identity(_USER_ID, ["credentials:read"]))
    assert {s.session_id for s in owner_page.data} == {ids[_USER_ID], ids[_AGENT_ID]}

    stranger_page = await svc.list_all(
        identity=_list_identity(_OTHER_USER_ID, ["credentials:read"])
    )
    assert {s.session_id for s in stranger_page.data} == {ids[_OTHER_USER_ID]}


async def test_owner_reads_but_cannot_write_pending_credential_of_owned_agent(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    created = await ConnectSessionService(ctx).create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None
    credentials = CredentialService(ctx)
    owner = _list_identity(_USER_ID, ["credentials:read", "credentials:write"])
    stranger = _list_identity(_OTHER_USER_ID, ["credentials:read", "credentials:write"])

    view = await credentials.get(row.credential_id, identity=owner)
    assert view.credential_id == row.credential_id
    assert row.credential_id in {
        c.credential_id for c in (await credentials.list_all(identity=owner)).data
    }
    with pytest.raises(CredentialNotFoundError):
        await credentials.get(row.credential_id, identity=stranger)
    # Read-only: the owned-agent axis never reaches a write path.
    with pytest.raises(CredentialNotFoundError):
        await credentials.delete(row.credential_id, identity=owner)

    # Once the session ends the axis no longer applies.
    async with ctx.control_db.transaction() as session:
        await ConnectSessionRepository.update_fields(session, row.id, state="failed")
    with pytest.raises(CredentialNotFoundError):
        await credentials.get(row.credential_id, identity=owner)


# ---------------------------------------------------------------------------
# connect_session.created rail event
# ---------------------------------------------------------------------------


@pytest.fixture()
async def clean_session_events(integration_context: Context) -> AsyncGenerator[None, None]:
    """Drop the ``connect_session.created`` rows this section writes, before and after."""

    async def _purge() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(
                delete(Event).where(Event.type == EventType.CONNECT_SESSION_CREATED)
            )
            await session.commit()

    await _purge()
    yield
    await _purge()


async def _session_created_events(ctx: Context) -> list[Event]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(Event).where(Event.type == EventType.CONNECT_SESSION_CREATED)
        )
        return list(result.scalars().all())


async def test_agent_started_session_emits_an_informational_event_for_the_owner(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
    clean_session_events: None,
) -> None:
    ctx = integration_context
    created = await ConnectSessionService(ctx).create_session(
        vendor_key="testdev", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )

    events = await _session_created_events(ctx)
    assert len(events) == 1
    event = events[0]
    assert event.severity == EventSeverity.INFO
    assert event.requires_action is False
    assert event.summary == "Agent 'scout' asked to connect 'Test Device Vendor'"
    # The agent is the subject, so the owner-scoped event read shows it to
    # the agent's owner; the agent is also who acted.
    assert event.created_by == _AGENT_ID
    assert event.actor_id == _AGENT_ID
    assert event.actor_type == ActorType.AGENT.value
    assert event.data is not None
    assert event.data["session_id"] == created.session_id
    assert event.data["agent_id"] == _AGENT_ID
    assert event.data["vendor_key"] == "testdev"
    # Never the poll token, in any form.
    serialized = f"{event.summary} {event.detail} {event.data}"
    assert created.poll_token not in serialized
    assert hash_secret(created.poll_token) not in serialized

    owner_view = await EventService(ctx).list_all(
        EventFilter(event_type=[EventType.CONNECT_SESSION_CREATED]),
        identity=Identity(sub=_USER_ID, permissions=["events:read"]),
    )
    assert [e.id for e in owner_view.data] == [event.id]
    stranger_view = await EventService(ctx).list_all(
        EventFilter(event_type=[EventType.CONNECT_SESSION_CREATED]),
        identity=Identity(sub=_OTHER_USER_ID, permissions=["events:read"]),
    )
    assert stranger_view.data == []


async def test_user_started_session_emits_no_rail_event(
    integration_context: Context,
    seed_test_vendors: None,
    seed_agent: None,
    clean_session_tables: None,
    clean_session_events: None,
) -> None:
    # A human connecting in the SPA is already looking at the result.
    await ConnectSessionService(integration_context).create_session(
        vendor_key="testdev", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    assert await _session_created_events(integration_context) == []


# ---------------------------------------------------------------------------
# Target kinds, ``awaiting_app`` and terminal outcomes
# ---------------------------------------------------------------------------


async def _seed_api_session(
    ctx: Context,
    *,
    state: str = "created",
    resolved_flow: str = "manual_api_key",
    agent_id: str | None = _AGENT_ID,
    api_version: str = "1.0.0",
    poll_token: str = "api-poll-token",
) -> str:
    """Write an ``api``-target session and its pending credential directly.

    No code path creates one while ``control.connect.manual_flows_enabled`` is
    off, so the rows are seeded through the repositories.
    """
    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type="api_key",
            name="Example API",
            api_vendor="example-com",
            api_name="example",
            api_version=api_version,
            created_by=_AGENT_ID,
            state="pending",
        )
        row = await ConnectSessionRepository.create(
            session,
            credential_id=credential.id,
            target_kind="api",
            vendor="example-com",
            agent_id=agent_id,
            initiator_actor_id=_AGENT_ID,
            state=state,
            resolved_flow=resolved_flow,
            poll_token_hash=hash_secret(f"{poll_token}-{api_version}-{state}"),
            created_by=_AGENT_ID,
        )
        await ConnectSessionRepository.update_fields(
            session,
            row.id,
            api_name="example",
            api_version=api_version,
            scheme_type="apiKey",
            scheme_location="header",
            scheme_field_name="X-Api-Key",
            pinned_hosts=["api.example.com"],
        )
        return row.id


async def test_existing_sessions_default_to_vendor_targets(
    integration_context: Context, seed_test_vendors: None, clean_session_tables: None
) -> None:
    ctx = integration_context
    created = await ConnectSessionService(ctx).create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None
    assert row.target_kind == "vendor"
    assert (row.api_name, row.api_version, row.scheme_type, row.pinned_hosts) == (
        None,
        None,
        None,
        None,
    )


async def test_create_session_refuses_api_target_while_gate_is_off(
    integration_context: Context,
    clean_session_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    target = ApiTarget(vendor="example-com", name="example", version="1.0.0")
    assert ctx.config.control.connect.manual_flows_enabled is False
    with pytest.raises(ManualFlowsDisabledError):
        await svc.create_session(
            vendor_key="example-com",
            agent_id=_AGENT_ID,
            initiator_actor_id=_AGENT_ID,
            api_target=target,
        )
    # With the gate on, a process that cannot read the registry refuses too.
    monkeypatch.setattr(ctx.config.control.connect, "manual_flows_enabled", True)
    with pytest.raises(SecuritySchemesLookupUnavailableError):
        await svc.create_session(
            vendor_key="example-com",
            agent_id=_AGENT_ID,
            initiator_actor_id=_AGENT_ID,
            api_target=target,
        )
    async with ctx.control_db.session() as session:
        assert (await session.execute(text("SELECT COUNT(*) FROM connect_sessions"))).scalar() == 0


async def test_vendor_entry_reads_refuse_api_targets(
    integration_context: Context,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    session_id = await _seed_api_session(ctx)
    # An API target's review reads the registry; without the seam it refuses.
    with pytest.raises(SecuritySchemesLookupUnavailableError):
        await svc.get_review_data(session_id, poll_token=None, identity=_ADMIN_IDENTITY)
    # The OAuth confirm body does not apply to a manual_* session.
    with pytest.raises(ConfirmKindMismatchError):
        await svc.confirm(
            session_id,
            poll_token=None,
            confirmed_scopes=[],
            permission_rules=[],
            identity=_ADMIN_IDENTITY,
        )
    # The refusal leaves the session untouched.
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, session_id)
    assert row is not None
    assert row.state == "created"

    # The list never resolves an API target as a vendor-registry key.
    page = await svc.list_all(identity=_ADMIN_IDENTITY)
    (summary,) = [s for s in page.data if s.session_id == session_id]
    assert summary.vendor_display_name == "example-com/example"


async def test_awaiting_app_reports_pending_and_cancels_as_cancelled(
    integration_context: Context,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    session_id = await _seed_api_session(ctx, state="awaiting_app", resolved_flow="awaiting_app")
    status = await svc.get_status(session_id, poll_token=None, identity=_ADMIN_IDENTITY)
    assert status.status == "pending"

    await svc.cancel_session(session_id, poll_token=None, identity=_ADMIN_IDENTITY)
    async with ctx.control_db.session() as session:
        assert await ConnectSessionRepository.get_by_id(session, session_id) is None
        outcome = await ConnectSessionOutcomeRepository.get_by_session_id(session, session_id)
    assert outcome is not None
    assert (outcome.outcome, outcome.error_code) == ("cancelled", "cancelled")
    assert (outcome.target_kind, outcome.vendor, outcome.api_name, outcome.api_version) == (
        "api",
        "example-com",
        "example",
        "1.0.0",
    )


async def test_cancel_records_one_cancelled_outcome(
    integration_context: Context, seed_test_vendors: None, clean_session_tables: None
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    await svc.cancel_session(
        created.session_id, poll_token=created.poll_token, identity=_USER_IDENTITY
    )
    # The session is gone, so a repeat cancel is refused and writes nothing.
    with pytest.raises(InvalidPollTokenError):
        await svc.cancel_session(
            created.session_id, poll_token=created.poll_token, identity=_USER_IDENTITY
        )
    async with ctx.control_db.session() as session:
        outcome = await ConnectSessionOutcomeRepository.get_by_session_id(
            session, created.session_id
        )
        count = (
            await session.execute(text("SELECT COUNT(*) FROM connect_session_outcomes"))
        ).scalar()
    assert outcome is not None
    assert (outcome.outcome, outcome.error_code, outcome.created_by) == (
        "cancelled",
        "cancelled",
        _USER_ID,
    )
    assert count == 1


async def test_app_change_records_a_failed_outcome(
    integration_context: Context, seed_test_vendors: None, clean_session_tables: None
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    # The config entry the session was created against goes away.
    del ctx.config.vendors.entries["testauth"]
    with pytest.raises(OAuthAppChangedError):
        await svc.get_review_data(
            created.session_id, poll_token=created.poll_token, identity=_USER_IDENTITY
        )
    async with ctx.control_db.session() as session:
        outcome = await ConnectSessionOutcomeRepository.get_by_session_id(
            session, created.session_id
        )
    assert outcome is not None
    assert (outcome.outcome, outcome.error_code) == ("failed", "oauth_app_changed")


async def test_sweep_uses_the_manual_ttl_for_non_oauth_flows(
    integration_context: Context,
    seed_test_vendors: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    oauth = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    manual_young = await _seed_api_session(ctx, api_version="1.0.0")
    manual_old = await _seed_api_session(ctx, api_version="2.0.0")
    ttl_hours = ctx.config.control.connect.manual_flows_ttl_hours
    async with ctx.control_db.transaction() as session:
        for session_id, age in (
            (oauth.session_id, timedelta(hours=2)),
            (manual_young, timedelta(hours=2)),
            (manual_old, timedelta(hours=ttl_hours + 1)),
        ):
            await session.execute(
                update(ConnectSession)
                .where(ConnectSession.id == session_id)
                .values(created_at=datetime.now(UTC) - age)
            )

    assert await svc.expire_stale_sessions() == 2

    async with ctx.control_db.session() as session:
        assert await ConnectSessionRepository.get_by_id(session, oauth.session_id) is None
        assert await ConnectSessionRepository.get_by_id(session, manual_old) is None
        young = await ConnectSessionRepository.get_by_id(session, manual_young)
    assert young is not None
    assert young.state == "created"


async def test_sweep_drops_outcomes_past_retention(
    integration_context: Context, seed_test_vendors: None, clean_session_tables: None
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    old = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    recent = await svc.create_session(
        vendor_key="testauth", agent_id=None, initiator_actor_id=_USER_ID
    )
    for created in (old, recent):
        await svc.cancel_session(
            created.session_id, poll_token=created.poll_token, identity=_USER_IDENTITY
        )
    async with ctx.control_db.transaction() as session:
        await session.execute(
            update(ConnectSessionOutcome)
            .where(ConnectSessionOutcome.session_id == old.session_id)
            .values(ended_at=datetime.now(UTC) - timedelta(days=31))
        )

    await svc.expire_stale_sessions()

    async with ctx.control_db.session() as session:
        assert (
            await ConnectSessionOutcomeRepository.get_by_session_id(session, old.session_id) is None
        )
        assert (
            await ConnectSessionOutcomeRepository.get_by_session_id(session, recent.session_id)
            is not None
        )


async def test_open_api_target_dedupe_index(
    integration_context: Context, clean_session_tables: None
) -> None:
    ctx = integration_context
    await _seed_api_session(ctx)
    # A second open session for the same agent and API identity collides.
    with pytest.raises(DatabaseIntegrityError):
        await _seed_api_session(ctx, state="awaiting_app", poll_token="second")
    # A different version, an ended session, or no agent does not.
    await _seed_api_session(ctx, api_version="2.0.0")
    await _seed_api_session(ctx, state="connected", poll_token="third")
    await _seed_api_session(ctx, agent_id=None, poll_token="fourth")
    await _seed_api_session(ctx, agent_id=None, poll_token="fifth")


async def test_repeat_agent_vendor_ask_reuses_the_open_session(
    integration_context: Context, seed_test_vendors: None, clean_session_tables: None
) -> None:
    # An agent asking again for the same vendor, app and scopes gets its open
    # session back with a fresh poll token; the old token stops working.
    # Asks with different scopes, and sessions a user starts, are separate.
    svc = ConnectSessionService(integration_context)
    first = await svc.create_session(
        vendor_key="testauth",
        agent_id=_AGENT_ID,
        initiator_actor_id=_AGENT_ID,
        requested_scopes=["repo", "read:user"],
    )
    second = await svc.create_session(
        vendor_key="testauth",
        agent_id=_AGENT_ID,
        initiator_actor_id=_AGENT_ID,
        requested_scopes=["read:user", "repo", "repo"],
    )
    assert second.session_id == first.session_id
    assert second.approval_url == first.approval_url
    assert second.poll_token != first.poll_token
    with pytest.raises(InvalidPollTokenError):
        await svc.get_status(
            first.session_id, poll_token=first.poll_token, identity=_AGENT_IDENTITY
        )
    status = await svc.get_status(
        first.session_id, poll_token=second.poll_token, identity=_AGENT_IDENTITY
    )
    assert status.status == "pending"

    other_scopes = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_AGENT_ID
    )
    assert other_scopes.session_id != first.session_id
    user_started = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    user_again = await svc.create_session(
        vendor_key="testauth", agent_id=_AGENT_ID, initiator_actor_id=_USER_ID
    )
    assert user_started.session_id != user_again.session_id
