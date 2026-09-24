"""Integration tests for connect sessions against a shared OAuth app registration.

Covers the branching that only shows up end-to-end against real ORM rows:

* Initiate + confirm against a DB registration writes
  ``credentials.oauth_app_registration_id`` + ``credentials.owner_user_id``
  and does NOT create an ``oauth_client_credentials`` aux row (the shared
  registration is the client-material source of truth for every credential
  minted through it).
* ``DirectOAuth2Provider.refresh`` refuses to mint through an
  ``is_active=False`` registration.
* Legacy embedded path (config-source vendor with no DB registration) still
  writes ``oauth_client_credentials`` untouched.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
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
_USER_IDENTITY = Identity(sub=_USER_ID, permissions=["credentials:write"])

_VENDOR_KEY = "shareddev"
_VENDOR_API_ID = "shareddev.example/api.shareddev.example"


@pytest.fixture()
async def clean_session_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Reset every table this suite writes to, before and after."""
    tables = (
        AuthorizationCodeAppRegistrationDetails,
        OAuthAppRegistration,
        ConnectSession,
        OAuthToken,
        OAuthClientCredential,
        DeviceAuthorizationCredential,
        Credential,
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

    The DB registration overrides the operator-config OAuth-app material,
    but the config catalog is still consulted for scopes, identity_probe,
    and catalog api_id — so we need both here (a config entry AND an
    active DB registration).
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


async def _seed_active_registration(ctx: Context) -> OAuthAppRegistration:
    """Insert an active auth-code registration and return the created row."""
    async with ctx.control_db.transaction() as session:
        registration = await OAuthAppRegistrationRepository.create_authorization_code(
            session,
            name="Org GitHub App",
            api_vendor=_VENDOR_KEY,
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
    - ``owner_user_id`` is stamped from the user initiator,
    - the authorize URL uses the registration's ``client_id`` (not the
      operator-config one), proving DB-first resolution won.
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)

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
    # DB registration's client_id wins over the operator-config one.
    assert q["client_id"] == ["shared-registration-client"]
    # PKCE (RFC 7636) rides every new auth-code flow.
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and q["code_challenge"][0]

    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
        assert credential is not None
        # FK stamped, owner captured, legacy aux row skipped.
        assert credential.oauth_app_registration_id == registration.id
        assert credential.owner_user_id == _USER_ID
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
            owner_user_id=_USER_ID,
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
    ``app_registration_id`` + ``issued_to_user``.
    """
    ctx = integration_context
    registration = await _seed_active_registration(ctx)

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
        # Token row stamped with registration provenance + issued_to_user.
        stmt = text(
            "SELECT app_registration_id, issued_to_user FROM oauth_tokens "
            "WHERE credential_id = :cid"
        )
        result_rs = await session.execute(stmt, {"cid": row.credential_id})
        stamp = result_rs.first()
        assert stamp is not None
        assert stamp[0] == registration.id
        assert stamp[1] == _USER_ID
