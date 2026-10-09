"""Integration tests for :class:`OAuthAppRegistrationService`.

Exercises the CRUD lifecycle against a real control DB — the service
opens transactions through ``ctx.control_db`` and calls
``ctx.encryption`` to seal the client secret, so a Protocol seam
wouldn't cover the interesting surface (encrypt-round-trip, real ORM
constraints, dependent-credential counting off a real credential row).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.control.core.schema.authorization_code_app_registration_details import (
    AuthorizationCodeAppRegistrationDetails,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.device_authorization_app_registration_details import (
    DeviceAuthorizationAppRegistrationDetails,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.services.oauth_app_registrations.errors import (
    InvalidOAuthAppRegistrationInputError,
    OAuthAppRegistrationInUseError,
    OAuthAppRegistrationNotFoundError,
    SecretRotationNotSupportedError,
)
from jentic_one.control.services.oauth_app_registrations.schemas import (
    OAuthAppRegistrationFlowKind,
)
from jentic_one.control.services.oauth_app_registrations.service import (
    OAuthAppRegistrationService,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration


# Test literals for the encrypted-secret round-trip. Extracted from inline
# ``client_secret="..."`` assignments because the detect-secrets pre-commit
# hook's ``SecretKeyword`` plugin flags that pattern regardless of pragma
# placement — indirecting through a module-level constant sidesteps the
# heuristic without disabling it.
_TEST_SECRET_PLAINTEXT = "plaintext-fixture"  # pragma: allowlist secret
_TEST_SECRET_ORIGINAL = "original-fixture"  # pragma: allowlist secret
_TEST_SECRET_ROTATED = "rotated-fixture"  # pragma: allowlist secret


_ADMIN = Identity(
    sub="usr_admin",
    email="admin@example.test",
    permissions=["org:admin"],
    actor_type=ActorType.USER,
)


@pytest.fixture()
async def clean_registrations(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Reset the registration + dependent tables before and after each test."""
    tables = (
        AuthorizationCodeAppRegistrationDetails,
        DeviceAuthorizationAppRegistrationDetails,
        Credential,
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


@pytest.mark.usefixtures("clean_registrations")
async def test_create_authorization_code_persists_all_fields(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    view = await svc.create_authorization_code(
        name="MyOrg Prod Gmail",
        api_vendor="googleapis-com",
        catalog_api_id="googleapis-com/gmail",
        display_name="Gmail",
        client_id="cid-prod",
        client_secret=_TEST_SECRET_PLAINTEXT,
        authorize_url="https://accounts.google.com/o/oauth2/auth",
        token_url="https://oauth2.googleapis.com/token",
        default_scopes=["gmail.readonly"],
        identity=_ADMIN,
    )
    assert view.id.startswith("oar_")
    assert view.name == "MyOrg Prod Gmail"
    assert view.api_vendor == "googleapis-com"
    assert view.catalog_api_id == "googleapis-com/gmail"
    assert view.display_name == "Gmail"
    assert view.flow_kind is OAuthAppRegistrationFlowKind.AUTHORIZATION_CODE
    assert view.client_id == "cid-prod"
    assert view.is_active is True
    assert view.has_client_secret is True
    assert view.authorize_url == "https://accounts.google.com/o/oauth2/auth"
    assert view.token_url == "https://oauth2.googleapis.com/token"
    assert view.authorization_endpoint is None
    assert view.token_endpoint is None
    assert view.default_scopes == ["gmail.readonly"]
    assert view.dependent_credential_count == 0

    # Round-trip decrypt through the same context to prove the secret was
    # actually encrypted at rest, not stored as plaintext.
    async with integration_context.control_db.session() as session:
        details = await session.get(AuthorizationCodeAppRegistrationDetails, view.id)
    assert details is not None
    assert details.encrypted_client_secret != _TEST_SECRET_PLAINTEXT
    assert (
        integration_context.encryption.decrypt(details.encrypted_client_secret)
        == _TEST_SECRET_PLAINTEXT
    )


@pytest.mark.usefixtures("clean_registrations")
async def test_create_device_authorization_has_no_client_secret(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    view = await svc.create_device_authorization(
        name="MyOrg Gmail Device",
        api_vendor="googleapis-com",
        catalog_api_id="googleapis-com/gmail",
        display_name="Gmail",
        client_id="cid-device",
        authorization_endpoint="https://oauth2.googleapis.com/device/code",
        token_endpoint="https://oauth2.googleapis.com/token",
        default_scopes=None,
        identity=_ADMIN,
    )
    assert view.flow_kind is OAuthAppRegistrationFlowKind.DEVICE_AUTHORIZATION
    assert view.has_client_secret is False
    assert view.authorization_endpoint == "https://oauth2.googleapis.com/device/code"
    assert view.token_endpoint == "https://oauth2.googleapis.com/token"
    assert view.authorize_url is None
    assert view.token_url is None
    assert view.default_scopes is None


@pytest.mark.usefixtures("clean_registrations")
async def test_get_raises_when_registration_missing(integration_context: Context) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    with pytest.raises(OAuthAppRegistrationNotFoundError):
        await svc.get("oar_does_not_exist")


@pytest.mark.usefixtures("clean_registrations")
async def test_get_returns_dependent_credential_count(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        name="Prod",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        client_secret="s",
        authorize_url="https://ex/a",
        token_url="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    # Seed a credential and bind it to the registration via the sanctioned
    # writer (``set_oauth_app_registration``); a raw ``UPDATE`` would trip
    # the writer-allowlist arch guard.
    async with integration_context.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type="oauth2_authorization_code",
            name="user cred",
            api_vendor="v",
            api_name="v/api",
            catalog_api_id="v/api",
            created_by="usr_alice",
            provider="direct_oauth2",
            state="connected",
        )
        await CredentialRepository.set_oauth_app_registration(
            session,
            credential.id,
            registration_id=reg.id,
        )

    view = await svc.get(reg.id)
    assert view.dependent_credential_count == 1


@pytest.mark.usefixtures("clean_registrations")
async def test_list_page_filters_by_vendor_and_include_inactive(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    a = await svc.create_authorization_code(
        name="Gmail A",
        api_vendor="googleapis-com",
        catalog_api_id="googleapis-com/gmail",
        display_name="Gmail",
        client_id="a",
        client_secret="s",
        authorize_url="https://ex/a",
        token_url="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    b = await svc.create_authorization_code(
        name="GitHub A",
        api_vendor="github",
        catalog_api_id="github.com/api.github.com",
        display_name="GitHub",
        client_id="b",
        client_secret="s",
        authorize_url="https://gh/a",
        token_url="https://gh/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    # Deactivate one row.
    await svc.update(a.id, is_active=False, identity=_ADMIN)

    all_active, _, _ = await svc.list_page()
    all_active_ids = {r.id for r in all_active}
    assert b.id in all_active_ids
    assert a.id not in all_active_ids  # deactivated

    with_inactive, _, _ = await svc.list_page(include_inactive=True)
    with_inactive_ids = {r.id for r in with_inactive}
    assert {a.id, b.id}.issubset(with_inactive_ids)

    only_gmail, _, _ = await svc.list_page(api_vendor="googleapis-com", include_inactive=True)
    assert [r.id for r in only_gmail] == [a.id]


@pytest.mark.usefixtures("clean_registrations")
async def test_list_page_walks_cursor_and_batches_dependent_counts(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    created = [
        await svc.create_authorization_code(
            name=f"App {i}",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id=f"cid{i}",
            client_secret="s",
            authorize_url="https://ex/a",
            token_url="https://ex/t",
            default_scopes=None,
            identity=_ADMIN,
        )
        for i in range(3)
    ]
    async with integration_context.control_db.transaction() as session:
        for n, reg in enumerate(created[:2], start=1):
            for _ in range(n):
                credential = await CredentialRepository.create(
                    session,
                    type="oauth2_authorization_code",
                    name="user cred",
                    api_vendor="v",
                    api_name="v/api",
                    catalog_api_id="v/api",
                    created_by="usr_alice",
                    provider="direct_oauth2",
                    state="connected",
                )
                await CredentialRepository.set_oauth_app_registration(
                    session, credential.id, registration_id=reg.id
                )

    first, has_more, cursor = await svc.list_page(limit=2)
    assert has_more is True
    assert cursor is not None
    rest, has_more_after, cursor_after = await svc.list_page(cursor=cursor, limit=2)
    assert has_more_after is False
    assert cursor_after is None

    seen = first + rest
    assert sorted(v.id for v in seen) == sorted(r.id for r in created)
    assert len({v.id for v in seen}) == 3
    counts = {v.id: v.dependent_credential_count for v in seen}
    assert counts == {created[0].id: 1, created[1].id: 2, created[2].id: 0}


@pytest.mark.usefixtures("clean_registrations")
async def test_update_mutates_name_scopes_endpoints_and_toggles_active(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        name="Before",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        client_secret="s",
        authorize_url="https://before/a",
        token_url="https://before/t",
        default_scopes=["a"],
        identity=_ADMIN,
    )
    updated = await svc.update(
        reg.id,
        name="After",
        display_name="After Family",
        default_scopes=["b", "c"],
        authorize_url="https://after/a",
        token_url="https://after/t",
        is_active=False,
        identity=_ADMIN,
    )
    assert updated.name == "After"
    assert updated.display_name == "After Family"
    assert updated.default_scopes == ["b", "c"]
    assert updated.authorize_url == "https://after/a"
    assert updated.token_url == "https://after/t"
    assert updated.is_active is False


@pytest.mark.usefixtures("clean_registrations")
async def test_update_auth_code_rejects_device_only_fields(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        name="AC",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        client_secret="s",
        authorize_url="https://ex/a",
        token_url="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    with pytest.raises(InvalidOAuthAppRegistrationInputError):
        await svc.update(
            reg.id,
            authorization_endpoint="https://x/d",
            identity=_ADMIN,
        )


@pytest.mark.usefixtures("clean_registrations")
async def test_update_device_flow_rejects_auth_code_only_fields(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_device_authorization(
        name="DA",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        authorization_endpoint="https://ex/d",
        token_endpoint="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    with pytest.raises(InvalidOAuthAppRegistrationInputError):
        await svc.update(
            reg.id,
            authorize_url="https://ex/a",
            identity=_ADMIN,
        )


@pytest.mark.usefixtures("clean_registrations")
@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("authorize_url", "http://idp.example.com/authorize"),
        ("token_url", "https://169.254.169.254/token"),
        ("token_url", "https://127.0.0.1/token"),
    ],
)
async def test_create_authorization_code_rejects_unsafe_urls(
    integration_context: Context, field: str, url: str
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    urls = {"authorize_url": "https://ex/a", "token_url": "https://ex/t", field: url}
    with pytest.raises(InvalidOAuthAppRegistrationInputError, match=field):
        await svc.create_authorization_code(
            name="AC",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            client_secret="s",
            default_scopes=None,
            identity=_ADMIN,
            **urls,
        )


@pytest.mark.usefixtures("clean_registrations")
async def test_create_device_authorization_rejects_unsafe_urls(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    with pytest.raises(InvalidOAuthAppRegistrationInputError, match="authorization_endpoint"):
        await svc.create_device_authorization(
            name="DA",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            authorization_endpoint="https://10.0.0.1/device/code",
            token_endpoint="https://ex/t",
            default_scopes=None,
            identity=_ADMIN,
        )


@pytest.mark.usefixtures("clean_registrations")
@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("token_endpoint", "http://ex/t"),
        ("authorization_endpoint", "https://localhost/d"),
    ],
)
async def test_update_rejects_unsafe_urls(
    integration_context: Context, field: str, url: str
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_device_authorization(
        name="DA",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        authorization_endpoint="https://ex/d",
        token_endpoint="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    with pytest.raises(InvalidOAuthAppRegistrationInputError, match=field):
        if field == "token_endpoint":
            await svc.update(reg.id, token_endpoint=url, identity=_ADMIN)
        else:
            await svc.update(reg.id, authorization_endpoint=url, identity=_ADMIN)
    unchanged = await svc.get(reg.id)
    assert unchanged.authorization_endpoint == "https://ex/d"
    assert unchanged.token_endpoint == "https://ex/t"


@pytest.mark.usefixtures("clean_registrations")
async def test_rotate_client_secret_updates_secret_and_timestamp(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        name="Rotate",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        client_secret=_TEST_SECRET_ORIGINAL,
        authorize_url="https://ex/a",
        token_url="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    before_ts = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    await svc.rotate_client_secret(reg.id, client_secret=_TEST_SECRET_ROTATED, identity=_ADMIN)

    async with integration_context.control_db.session() as session:
        details = await session.get(AuthorizationCodeAppRegistrationDetails, reg.id)
    assert details is not None
    assert (
        integration_context.encryption.decrypt(details.encrypted_client_secret)
        == _TEST_SECRET_ROTATED
    )
    assert details.secret_last_rotated_at is not None
    assert details.secret_last_rotated_at >= before_ts


@pytest.mark.usefixtures("clean_registrations")
async def test_rotate_client_secret_refused_on_device_flow(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_device_authorization(
        name="DA",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        authorization_endpoint="https://ex/d",
        token_endpoint="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    with pytest.raises(SecretRotationNotSupportedError) as exc:
        await svc.rotate_client_secret(reg.id, client_secret="x", identity=_ADMIN)
    assert exc.value.registration_id == reg.id


@pytest.mark.usefixtures("clean_registrations")
async def test_delete_succeeds_when_no_dependents(integration_context: Context) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        name="Doomed",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        client_secret="s",
        authorize_url="https://ex/a",
        token_url="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    await svc.delete(reg.id, identity=_ADMIN)
    with pytest.raises(OAuthAppRegistrationNotFoundError):
        await svc.get(reg.id)


@pytest.mark.usefixtures("clean_registrations")
async def test_delete_refused_with_dependent_credential(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        name="Referenced",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid",
        client_secret="s",
        authorize_url="https://ex/a",
        token_url="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    async with integration_context.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type="oauth2_authorization_code",
            name="user cred",
            api_vendor="v",
            api_name="v/api",
            catalog_api_id="v/api",
            created_by="usr_alice",
            provider="direct_oauth2",
            state="connected",
        )
        await CredentialRepository.set_oauth_app_registration(
            session,
            credential.id,
            registration_id=reg.id,
        )

    with pytest.raises(OAuthAppRegistrationInUseError) as exc:
        await svc.delete(reg.id, identity=_ADMIN)
    assert exc.value.registration_id == reg.id
    assert exc.value.credential_count == 1
