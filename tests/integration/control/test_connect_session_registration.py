"""Integration tests for connect sessions against a shared OAuth app registration.

Covers the branching that only shows up end-to-end against real ORM rows:

* Initiate + confirm against a DB registration writes
  ``credentials.oauth_app_registration_id`` and does NOT create an
  ``oauth_client_credentials`` aux row (the shared registration is the
  client-material source of truth for every credential minted through it).
* ``DirectOAuth2Provider.refresh`` takes client material from the
  registration when the credential is FK'd to one, refuses to mint through an
  ``is_active=False`` registration, and falls back to the embedded
  ``oauth_client_credentials`` row when there is no FK.
* Device-flow ``prepare`` always writes its aux row and stamps the FK only on
  the shared-registration path.
* Legacy embedded path (config-source vendor with no DB registration) still
  writes ``oauth_client_credentials`` untouched.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, text

from jentic_one.control.core.schema.authorization_code_app_registration_details import (
    AuthorizationCodeAppRegistrationDetails,
)
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.device_authorization_app_registration_details import (
    DeviceAuthorizationAppRegistrationDetails,
)
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import CredentialRepository, OAuthClientCredentialRepository
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.services.credentials.providers.direct_oauth2 import (
    DirectOAuth2Provider,
    InactiveRegistrationError,
)
from jentic_one.control.services.credentials.schemas.provision import OAuthTokenView
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.connect_session_service import (
    AuthCodeConfirmResult,
    ConnectSessionService,
)
from jentic_one.control.services.integrations.errors import OAuthAppChangedError
from jentic_one.control.services.integrations.flow_handlers.base import SuccessTokens
from jentic_one.control.services.integrations.flow_handlers.device_authorization import (
    DeviceAuthorizationHandler,
)
from jentic_one.control.services.integrations.flow_handlers.session_app import SessionApp
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    DirectOAuth2ProviderConfig,
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorIdentityProbeConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


_USER_ID = "usr_alice"
_AGENT_ID = "agnt_scout"
# Confirming for an owned agent needs both write permissions (the bind
# route's own gate is ``agents:write``).
_USER_IDENTITY = Identity(sub=_USER_ID, permissions=["credentials:write", "agents:write"])

_VENDOR_KEY = "shareddev"
_VENDOR_API_ID = "shareddev.example/api.shareddev.example"


@pytest.fixture()
async def clean_session_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Reset every table this suite writes to, before and after."""
    # Order matters: ``credentials.oauth_app_registration_id`` is
    # ``ON DELETE RESTRICT``, so every credential (and its cascading
    # aux rows) must go before the registrations they point at.
    tables = (
        OAuthToken,
        OAuthClientCredential,
        DeviceAuthorizationCredential,
        ConnectSession,
        Credential,
        AuthorizationCodeAppRegistrationDetails,
        DeviceAuthorizationAppRegistrationDetails,
        OAuthAppRegistration,
    )
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
    """Seed the admin-DB user + agent rows the confirm path validates."""
    async with integration_context.admin_db.session() as session:
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
def seed_shared_vendor(integration_context: Context) -> None:
    """Register an auth-code vendor in the config catalog.

    Tests that seed a same-slug registration pin it on ``:connect``; with no
    pin the config entry is the session's source.
    """
    integration_context.config.vendors.entries[_VENDOR_KEY] = VendorAuthConfig(
        vendor=_VENDOR_API_ID,
        display_name="Shared Auth Vendor",
        flows=[
            VendorAuthorizationCodeFlowConfig(
                client_id="config-client",
                client_secret=SecretStr("config-secret"),  # pragma: allowlist secret
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
    integration_context.config.credentials.providers.setdefault(
        "direct_oauth2",
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
        ),
    )


async def _seed_active_registration(
    ctx: Context, *, name: str = "Org GitHub App", display_name: str = "Shared Dev"
) -> OAuthAppRegistration:
    """Insert an active auth-code registration and return the created row."""
    async with ctx.control_db.transaction() as session:
        registration = await OAuthAppRegistrationRepository.create_authorization_code(
            session,
            name=name,
            api_vendor=_VENDOR_KEY,
            catalog_api_id=_VENDOR_API_ID,
            display_name=display_name,
            client_id="shared-registration-client",
            encrypted_client_secret=ctx.encryption.encrypt("shared-registration-secret"),
            authorize_url="https://idp.example.com/authorize",
            token_url="https://idp.example.com/token",
            default_scopes=["scope-a"],
            created_by=_USER_ID,
        )
    return registration


def _state_from_authorize_url(authorize_url: str) -> str:
    parts = urlsplit(authorize_url)
    q = parse_qs(parts.query)
    return q["state"][0]


async def test_confirm_against_db_registration_stamps_fk_and_skips_occ(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """Initiate + confirm against a DB registration writes the FK on the
    credential and does not create an ``oauth_client_credentials`` row.

    Also asserts:
    - the authorize URL uses the registration's ``client_id`` (not the
      operator-config one), proving the pin won.
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)

    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
        oauth_app_registration_id=registration.id,
    )

    confirmed = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    assert isinstance(confirmed, AuthCodeConfirmResult)
    q = parse_qs(urlsplit(confirmed.authorize_url).query)
    # Pinned registration's client_id, not the operator-config one.
    assert q["client_id"] == ["shared-registration-client"]
    # PKCE (RFC 7636) rides every new auth-code flow.
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and q["code_challenge"][0]

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
        assert credential is not None
        # FK stamped, legacy aux row skipped.
        assert credential.oauth_app_registration_id == registration.id
        occ = await OAuthClientCredentialRepository.get_by_credential(session, credential.id)
        assert occ is None

    # The redacted read (what the UI card renders) carries the shared
    # registration's id + name so the card can show a "Shared: <name>" badge.
    cred_svc = CredentialService(ctx)
    redacted = await cred_svc.get(row.credential_id, identity=_USER_IDENTITY)
    assert redacted.oauth_app_registration_id == registration.id
    assert redacted.oauth_app_registration_name == "Org GitHub App"


async def test_refresh_refuses_inactive_registration(
    integration_context: Context,
    seed_shared_vendor: None,
    clean_session_tables: None,
) -> None:
    """Once a registration is flipped ``is_active=False``, refresh through
    ``DirectOAuth2Provider`` must fail with ``InactiveRegistrationError``.

    We plant a credential FK'd to an inactive registration + an existing
    (expired) token, then invoke ``refresh`` directly.
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)

    # Flip is_active=False on the registration.
    async with ctx.control_db.transaction() as session:
        await OAuthAppRegistrationRepository.update_base(session, registration.id, is_active=False)

    # Plant a credential + expired oauth_tokens row referencing the reg.
    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type="oauth2",
            name="Alice's GitHub",
            api_vendor=_VENDOR_KEY,
            api_name="api.shareddev.example",
            catalog_api_id=_VENDOR_API_ID,
            created_by=_USER_ID,
            provider="direct_oauth2",
            state="connected",
        )
        await CredentialRepository.set_oauth_app_registration(
            session,
            credential.id,
            registration_id=registration.id,
        )
    credential_id = credential.id

    provider = DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
        )
    )

    async def _decrypt() -> str:
        return "some-refresh-token"

    token_view = OAuthTokenView(
        credential_id=credential_id,
        provider="direct_oauth2",
        expires_at=datetime.now(UTC),
        decrypt=_decrypt,
    )

    with pytest.raises(InactiveRegistrationError, match="inactive"):
        await provider.refresh(ctx, token=token_view)


async def test_legacy_embedded_path_still_writes_oauth_client_credentials(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """No DB registration for the vendor → connect falls back to the
    operator-config OAuth app and writes ``oauth_client_credentials`` as
    before.
    """
    ctx = integration_context
    # Deliberately do NOT seed a registration — legacy embedded path.

    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
    )
    confirmed = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    assert isinstance(confirmed, AuthCodeConfirmResult)
    q = parse_qs(urlsplit(confirmed.authorize_url).query)
    # Legacy config-source client_id.
    assert q["client_id"] == ["config-client"]

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
        assert credential is not None
        assert credential.oauth_app_registration_id is None
        occ = await OAuthClientCredentialRepository.get_by_credential(session, credential.id)
        # Legacy write happened.
        assert occ is not None
        assert occ.client_id == "config-client"


async def test_complete_from_callback_through_registration_end_to_end(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """End-to-end callback exchange through a shared registration.

    Verifies the token exchange dereferences the registration for client
    material and the vaulted ``oauth_tokens`` row is stamped with
    ``app_registration_id``.
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)

    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
        oauth_app_registration_id=registration.id,
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

    posted_payloads: list[dict[str, str]] = []
    token_response = httpx.Response(
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

        async def post(self, url, data=None, **kwargs):
            posted_payloads.append(dict(data or {}))
            return token_response

    identity_result = identity_echo.IdentityEchoResult(display="alice", raw={"username": "alice"})
    with (
        patch("httpx.AsyncClient", return_value=_FakeClient()),
        patch.object(identity_echo, "echo_identity", new=AsyncMock(return_value=identity_result)),
    ):
        result = await svc.complete_from_callback(raw_state=raw_state, code="the-code")

    assert result.status == "connected"
    # The same-slug config entry lends its identity probe to the registration.
    assert result.connected_as == "alice"
    # Client material came off the DB registration, not the config vendor.
    assert posted_payloads
    payload = posted_payloads[0]
    assert payload["client_id"] == "shared-registration-client"
    assert payload["client_secret"] == "shared-registration-secret"  # pragma: allowlist secret
    # PKCE verifier travelled through the exchange.
    assert payload.get("code_verifier")

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        # Single-use verifier is not left at rest once the code is exchanged.
        assert row.pkce_code_verifier is None
        # Token row stamped with registration provenance.
        stmt = text("SELECT app_registration_id FROM oauth_tokens WHERE credential_id = :cid")
        result_rs = await session.execute(stmt, {"cid": row.credential_id})
        stamp = result_rs.first()
        assert stamp is not None
        assert stamp[0] == registration.id


async def test_finalise_fails_session_when_registration_disabled_mid_flow(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """Disabling the registration after the vendor issued tokens fails the
    session as ``registration_inactive`` instead of raising out of finalise
    (which would strand the session in a non-terminal state).
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)

    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
        oauth_app_registration_id=registration.id,
    )
    await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )

    async with ctx.control_db.transaction() as session:
        await OAuthAppRegistrationRepository.update_base(session, registration.id, is_active=False)

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None

    result = await svc._finalise_connected(
        row,
        MagicMock(),
        SuccessTokens(
            access_token="at",
            refresh_token=None,
            expires_in=None,
            scope=None,
            granted_scopes=["scope-a"],
        ),
    )
    assert result.status == "failed"
    assert result.error_code == "registration_inactive"

    async with ctx.control_db.session() as session:
        # Terminal sessions are deleted along with their pending credential
        # (see ``_mark_terminal``); nothing is vaulted.
        assert await ConnectSessionRepository.get_by_id(session, created.session_id) is None
        assert await CredentialRepository.get_by_id(session, row.credential_id) is None
        token_rows = await session.execute(
            text("SELECT 1 FROM oauth_tokens WHERE credential_id = :cid"),
            {"cid": row.credential_id},
        )
        assert token_rows.first() is None


async def _assert_session_cancelled(ctx: Context, session_id: str, credential_id: str) -> None:
    async with ctx.control_db.session() as session:
        # Terminal sessions are deleted along with their pending credential.
        assert await ConnectSessionRepository.get_by_id(session, session_id) is None
        assert await CredentialRepository.get_by_id(session, credential_id) is None


async def test_config_session_ignores_registration_added_after_connect(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """A config-app session keeps its config source through review and
    confirm — a same-slug registration created after ``:connect`` never
    supplies its scopes or client material.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
    )

    await _seed_active_registration(ctx)

    review = await svc.get_review_data(
        created.session_id, poll_token=created.poll_token, identity=_USER_IDENTITY
    )
    assert review.vendor_display_name == "Shared Auth Vendor"
    confirmed = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    assert isinstance(confirmed, AuthCodeConfirmResult)
    q = parse_qs(urlsplit(confirmed.authorize_url).query)
    assert q["client_id"] == ["config-client"]


async def test_confirm_cancels_when_config_entry_removed_after_connect(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """Removing the config entry mid-session cancels rather than falling
    through to a registration for the same slug.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
    )
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None

    await _seed_active_registration(ctx)
    del ctx.config.vendors.entries[_VENDOR_KEY]

    with pytest.raises(OAuthAppChangedError):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["scope-a"],
            permission_rules=[],
            identity=_USER_IDENTITY,
        )
    await _assert_session_cancelled(ctx, created.session_id, row.credential_id)


async def test_confirm_cancels_when_pinned_registration_disabled(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """Disabling the session's registration between ``:connect`` and confirm
    cancels the session rather than leaving it stuck in ``created``.
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
        oauth_app_registration_id=registration.id,
    )
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None

    async with ctx.control_db.transaction() as session:
        await OAuthAppRegistrationRepository.update_base(session, registration.id, is_active=False)

    with pytest.raises(OAuthAppChangedError):
        await svc.confirm(
            created.session_id,
            poll_token=created.poll_token,
            confirmed_scopes=["scope-a"],
            permission_rules=[],
            identity=_USER_IDENTITY,
        )
    await _assert_session_cancelled(ctx, created.session_id, row.credential_id)


async def _plant_credential(ctx: Context, *, registration_id: str | None) -> str:
    """Insert a connected direct_oauth2 credential, optionally FK'd to a registration."""
    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type="oauth2",
            name="Alice's Shared Dev",
            api_vendor=_VENDOR_KEY,
            api_name="api.shareddev.example",
            catalog_api_id=_VENDOR_API_ID,
            created_by=_USER_ID,
            provider="direct_oauth2",
            state="connected",
        )
        if registration_id is not None:
            await CredentialRepository.set_oauth_app_registration(
                session, credential.id, registration_id=registration_id
            )
    return credential.id


async def _refresh_capturing_post(ctx: Context, credential_id: str) -> tuple[str, dict[str, str]]:
    """Run ``DirectOAuth2Provider.refresh`` against a fake token endpoint.

    Returns the new access token and the form body posted to the endpoint.
    """
    posted: list[dict[str, str]] = []
    token_response = httpx.Response(
        200, json={"access_token": "at_new", "expires_in": 3600, "scope": "scope-a"}
    )

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **kwargs):
            posted.append(dict(data or {}))
            return token_response

    async def _decrypt() -> str:
        return "old-refresh"

    provider = DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
        )
    )
    token_view = OAuthTokenView(
        credential_id=credential_id,
        provider="direct_oauth2",
        expires_at=datetime.now(UTC),
        decrypt=_decrypt,
    )
    with patch("httpx.AsyncClient", return_value=_FakeClient()):
        result = await provider.refresh(ctx, token=token_view)
    assert len(posted) == 1
    return result.access_token, posted[0]


async def test_refresh_takes_client_material_from_registration(
    integration_context: Context,
    seed_shared_vendor: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    registration = await _seed_active_registration(ctx)
    credential_id = await _plant_credential(ctx, registration_id=registration.id)

    access_token, posted = await _refresh_capturing_post(ctx, credential_id)

    assert access_token == "at_new"
    assert posted["client_id"] == "shared-registration-client"
    assert posted["client_secret"] == "shared-registration-secret"  # pragma: allowlist secret


async def test_refresh_falls_back_to_embedded_client_without_registration(
    integration_context: Context,
    seed_shared_vendor: None,
    clean_session_tables: None,
) -> None:
    ctx = integration_context
    credential_id = await _plant_credential(ctx, registration_id=None)
    async with ctx.control_db.transaction() as session:
        await OAuthClientCredentialRepository.create(
            session,
            credential_id=credential_id,
            token_url="https://idp.example.com/token",
            client_id="embedded-client",
            encrypted_client_secret=ctx.encryption.encrypt("embedded-secret"),
            created_by=_USER_ID,
        )

    access_token, posted = await _refresh_capturing_post(ctx, credential_id)

    assert access_token == "at_new"
    assert posted["client_id"] == "embedded-client"
    assert posted["client_secret"] == "embedded-secret"  # pragma: allowlist secret


def _device_app(*, registration_id: str | None) -> SessionApp:
    return SessionApp(
        flow_kind="device_authorization",
        client_id="device-client",
        client_secret_provider=None,
        default_scopes=[],
        registration_id=registration_id,
        authorization_endpoint="https://idp.example.com/device",
        token_endpoint="https://idp.example.com/token",
    )


@pytest.mark.parametrize("pinned", [True, False], ids=["registration", "config"])
async def test_device_prepare_writes_aux_and_stamps_fk_only_for_registration(
    integration_context: Context,
    clean_session_tables: None,
    pinned: bool,
) -> None:
    """The aux row carries transient device_code state, so it's written on
    both paths; the FK is stamped only when the app is a shared registration.
    """
    ctx = integration_context
    registration_id: str | None = None
    if pinned:
        async with ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.create_device_authorization(
                session,
                name="Org Device App",
                api_vendor=_VENDOR_KEY,
                catalog_api_id=_VENDOR_API_ID,
                display_name="Shared Dev",
                client_id="device-client",
                authorization_endpoint="https://idp.example.com/device",
                token_endpoint="https://idp.example.com/token",
                created_by=_USER_ID,
            )
        registration_id = registration.id
    credential_id = await _plant_credential(ctx, registration_id=None)

    async with ctx.control_db.transaction() as session:
        await DeviceAuthorizationHandler(ctx).prepare(
            session,
            credential_id=credential_id,
            app=_device_app(registration_id=registration_id),
            requested_scopes=[],
            created_by=_USER_ID,
        )

    async with ctx.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, credential_id)
        aux = await session.get(DeviceAuthorizationCredential, credential_id)
    assert credential is not None
    assert credential.oauth_app_registration_id == registration_id
    assert aux is not None
    assert aux.client_id == "device-client"
    assert aux.authorization_endpoint == "https://idp.example.com/device"


async def _pinned_session_awaiting_callback(
    ctx: Context, svc: ConnectSessionService, registration: OAuthAppRegistration
) -> tuple[str, str]:
    """Create + confirm a session pinned to ``registration``; return (session id, state)."""
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
        oauth_app_registration_id=registration.id,
    )
    confirmed = await svc.confirm(
        created.session_id,
        poll_token=created.poll_token,
        confirmed_scopes=["scope-a"],
        permission_rules=[],
        identity=_USER_IDENTITY,
    )
    assert isinstance(confirmed, AuthCodeConfirmResult)
    return created.session_id, _state_from_authorize_url(confirmed.authorize_url)


async def test_callback_fails_as_registration_inactive_when_disabled_after_confirm(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """Disabling the registration between confirm and the callback fails the
    session as ``registration_inactive``, not a generic token-exchange failure.
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)
    svc = ConnectSessionService(ctx)
    session_id, raw_state = await _pinned_session_awaiting_callback(ctx, svc, registration)
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, session_id)
    assert row is not None

    async with ctx.control_db.transaction() as session:
        await OAuthAppRegistrationRepository.update_base(session, registration.id, is_active=False)

    result = await svc.complete_from_callback(raw_state=raw_state, code="the-code")

    assert result.status == "failed"
    assert result.error_code == "registration_inactive"
    await _assert_session_cancelled(ctx, session_id, row.credential_id)


async def test_review_ends_session_when_pinned_registration_disabled(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """The review page gets the same 409 ``OAuthAppChangedError`` as confirm
    when the session's registration is disabled, and the session ends.
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)
    svc = ConnectSessionService(ctx)
    created = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
        oauth_app_registration_id=registration.id,
    )
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
    assert row is not None

    async with ctx.control_db.transaction() as session:
        await OAuthAppRegistrationRepository.update_base(session, registration.id, is_active=False)

    with pytest.raises(OAuthAppChangedError):
        await svc.get_review_data(
            created.session_id, poll_token=created.poll_token, identity=_USER_IDENTITY
        )
    await _assert_session_cancelled(ctx, created.session_id, row.credential_id)


async def test_session_list_names_the_app_each_session_runs_through(
    integration_context: Context,
    seed_shared_vendor: None,
    seed_agent: None,
    clean_session_tables: None,
) -> None:
    """List rows take their display name from the credential's pinned
    registration — two same-slug registrations never borrow each other's
    name — and config sessions take the config entry's.
    """
    ctx = integration_context
    await _seed_active_registration(ctx, name="App A", display_name="Shared Dev A")
    second = await _seed_active_registration(ctx, name="App B", display_name="Shared Dev B")
    svc = ConnectSessionService(ctx)
    pinned = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
        oauth_app_registration_id=second.id,
    )
    config = await svc.create_session(
        vendor_key=_VENDOR_KEY,
        agent_id=_AGENT_ID,
        initiator_actor_id=_USER_ID,
        requested_scopes=["scope-a"],
    )

    page = await svc.list_all(identity=_USER_IDENTITY)

    names = {s.session_id: s.vendor_display_name for s in page.data}
    assert names == {
        pinned.session_id: "Shared Dev B",
        config.session_id: "Shared Auth Vendor",
    }
