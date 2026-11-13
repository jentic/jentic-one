"""Integration tests for :class:`OAuthAppRegistrationService`.

Exercises the CRUD lifecycle against a real control DB — the service
opens transactions through ``ctx.control_db`` and calls
``ctx.encryption`` to seal the client secret, so a Protocol seam
wouldn't cover the interesting surface (encrypt-round-trip, real ORM
constraints, dependent-credential counting off a real credential row).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import delete

from jentic_one.control.core.schema.authorization_code_app_registration_details import (
    AuthorizationCodeAppRegistrationDetails,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.device_authorization_app_registration_details import (
    DeviceAuthorizationAppRegistrationDetails,
)
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import CredentialRepository, OAuthTokenRepository
from jentic_one.control.repos.device_authorization_credential_repo import (
    DeviceAuthorizationCredentialRepository,
)
from jentic_one.control.services.credentials.providers.device_authorization import (
    DeviceAuthorizationConnectProvider,
)
from jentic_one.control.services.credentials.providers.direct_oauth2 import DirectOAuth2Provider
from jentic_one.control.services.credentials.schemas.provision import OAuthTokenView
from jentic_one.control.services.oauth_app_registrations.errors import (
    InvalidOAuthAppRegistrationInputError,
    OAuthAppRegistrationInUseError,
    OAuthAppRegistrationNotFoundError,
    SecretRotationNotSupportedError,
)
from jentic_one.control.services.oauth_app_registrations.schemas import (
    AuthorizationCodeRegistrationCreate,
    DeviceAuthorizationRegistrationCreate,
    OAuthAppRegistrationFlowKind,
    OAuthAppRegistrationQuery,
    OAuthAppRegistrationUpdate,
    OAuthAppRegistrationView,
)
from jentic_one.control.services.oauth_app_registrations.service import (
    OAuthAppRegistrationService,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    AppConfig,
    ControlSurfaceConfig,
    DirectOAuth2ProviderConfig,
)
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
        OAuthToken,
        DeviceAuthorizationCredential,
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
    payload = AuthorizationCodeRegistrationCreate(
        name="MyOrg Prod Gmail",
        api_vendor="googleapis-com",
        catalog_api_id="googleapis-com/gmail",
        display_name="Gmail",
        client_id="cid-prod",
        client_secret=SecretStr(_TEST_SECRET_PLAINTEXT),
        authorize_url="https://accounts.google.com/o/oauth2/auth",
        token_url="https://oauth2.googleapis.com/token",
        default_scopes=["gmail.readonly"],
    )
    assert _TEST_SECRET_PLAINTEXT not in repr(payload)
    assert _TEST_SECRET_PLAINTEXT not in str(payload)
    view = await svc.create_authorization_code(
        payload,
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
        DeviceAuthorizationRegistrationCreate(
            name="MyOrg Gmail Device",
            api_vendor="googleapis-com",
            catalog_api_id="googleapis-com/gmail",
            display_name="Gmail",
            client_id="cid-device",
            authorization_endpoint="https://oauth2.googleapis.com/device/code",
            token_endpoint="https://oauth2.googleapis.com/token",
            default_scopes=None,
        ),
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
        AuthorizationCodeRegistrationCreate(
            name="Prod",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            client_secret=SecretStr("s"),
            authorize_url="https://ex/a",
            token_url="https://ex/t",
            default_scopes=None,
        ),
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
        AuthorizationCodeRegistrationCreate(
            name="Gmail A",
            api_vendor="googleapis-com",
            catalog_api_id="googleapis-com/gmail",
            display_name="Gmail",
            client_id="a",
            client_secret=SecretStr("s"),
            authorize_url="https://ex/a",
            token_url="https://ex/t",
            default_scopes=None,
        ),
        identity=_ADMIN,
    )
    b = await svc.create_authorization_code(
        AuthorizationCodeRegistrationCreate(
            name="GitHub A",
            api_vendor="github",
            catalog_api_id="github.com/api.github.com",
            display_name="GitHub",
            client_id="b",
            client_secret=SecretStr("s"),
            authorize_url="https://gh/a",
            token_url="https://gh/t",
            default_scopes=None,
        ),
        identity=_ADMIN,
    )
    # Deactivate one row.
    await svc.update(a.id, OAuthAppRegistrationUpdate(is_active=False), identity=_ADMIN)

    all_active = await svc.list_page(OAuthAppRegistrationQuery())
    all_active_ids = {r.id for r in all_active.data}
    assert b.id in all_active_ids
    assert a.id not in all_active_ids  # deactivated

    with_inactive = await svc.list_page(OAuthAppRegistrationQuery(include_inactive=True))
    with_inactive_ids = {r.id for r in with_inactive.data}
    assert {a.id, b.id}.issubset(with_inactive_ids)

    only_gmail = await svc.list_page(
        OAuthAppRegistrationQuery(api_vendor="googleapis-com", include_inactive=True)
    )
    assert [r.id for r in only_gmail.data] == [a.id]


@pytest.mark.usefixtures("clean_registrations")
async def test_list_page_walks_cursor_and_batches_dependent_counts(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    created = [
        await svc.create_authorization_code(
            AuthorizationCodeRegistrationCreate(
                name=f"App {i}",
                api_vendor="v",
                catalog_api_id="v/api",
                display_name="V",
                client_id=f"cid{i}",
                client_secret=SecretStr("s"),
                authorize_url="https://ex/a",
                token_url="https://ex/t",
                default_scopes=None,
            ),
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

    first_page = await svc.list_page(OAuthAppRegistrationQuery(limit=2))
    assert first_page.has_more is True
    assert first_page.next_cursor is not None
    rest_page = await svc.list_page(
        OAuthAppRegistrationQuery(cursor=first_page.next_cursor, limit=2)
    )
    assert rest_page.has_more is False
    assert rest_page.next_cursor is None

    seen = first_page.data + rest_page.data
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
        AuthorizationCodeRegistrationCreate(
            name="Before",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            client_secret=SecretStr("s"),
            authorize_url="https://before/a",
            token_url="https://before/t",
            default_scopes=["a"],
        ),
        identity=_ADMIN,
    )
    updated = await svc.update(
        reg.id,
        OAuthAppRegistrationUpdate(
            name="After",
            display_name="After Family",
            default_scopes=["b", "c"],
            authorize_url="https://after/a",
            token_url="https://before/v2/t",
            is_active=False,
        ),
        identity=_ADMIN,
    )
    assert updated.name == "After"
    assert updated.display_name == "After Family"
    assert updated.default_scopes == ["b", "c"]
    assert updated.authorize_url == "https://after/a"
    assert updated.token_url == "https://before/v2/t"
    assert updated.is_active is False


@pytest.mark.usefixtures("clean_registrations")
async def test_update_leaves_existing_fields_unchanged_for_empty_and_explicit_none(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        AuthorizationCodeRegistrationCreate(
            name="Original",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="Orig Family",
            client_id="cid",
            client_secret=SecretStr("s"),
            authorize_url="https://orig/a",
            token_url="https://orig/t",
            default_scopes=["a"],
        ),
        identity=_ADMIN,
    )

    empty_updated = await svc.update(reg.id, OAuthAppRegistrationUpdate(), identity=_ADMIN)
    assert empty_updated.name == "Original"
    assert empty_updated.display_name == "Orig Family"
    assert empty_updated.default_scopes == ["a"]
    assert empty_updated.authorize_url == "https://orig/a"
    assert empty_updated.token_url == "https://orig/t"
    assert empty_updated.is_active is True

    none_updated = await svc.update(
        reg.id,
        OAuthAppRegistrationUpdate(
            name=None,
            display_name=None,
            default_scopes=None,
            authorize_url=None,
            token_url=None,
            is_active=None,
        ),
        identity=_ADMIN,
    )
    assert none_updated.name == "Original"
    assert none_updated.display_name == "Orig Family"
    assert none_updated.default_scopes == ["a"]
    assert none_updated.authorize_url == "https://orig/a"
    assert none_updated.token_url == "https://orig/t"
    assert none_updated.is_active is True

    empty_scopes = await svc.update(
        reg.id,
        OAuthAppRegistrationUpdate(default_scopes=[]),
        identity=_ADMIN,
    )
    assert empty_scopes.default_scopes == []
    assert empty_scopes.name == "Original"


@pytest.mark.usefixtures("clean_registrations")
async def test_update_auth_code_rejects_device_only_fields(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        AuthorizationCodeRegistrationCreate(
            name="AC",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            client_secret=SecretStr("s"),
            authorize_url="https://ex/a",
            token_url="https://ex/t",
            default_scopes=None,
        ),
        identity=_ADMIN,
    )
    with pytest.raises(InvalidOAuthAppRegistrationInputError):
        await svc.update(
            reg.id,
            OAuthAppRegistrationUpdate(authorization_endpoint="https://x/d"),
            identity=_ADMIN,
        )


@pytest.mark.usefixtures("clean_registrations")
async def test_update_device_flow_rejects_auth_code_only_fields(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_device_authorization(
        DeviceAuthorizationRegistrationCreate(
            name="DA",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            authorization_endpoint="https://ex/d",
            token_endpoint="https://ex/t",
            default_scopes=None,
        ),
        identity=_ADMIN,
    )
    with pytest.raises(InvalidOAuthAppRegistrationInputError):
        await svc.update(
            reg.id,
            OAuthAppRegistrationUpdate(authorize_url="https://ex/a"),
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
            AuthorizationCodeRegistrationCreate(
                name="AC",
                api_vendor="v",
                catalog_api_id="v/api",
                display_name="V",
                client_id="cid",
                client_secret=SecretStr("s"),
                default_scopes=None,
                **urls,
            ),
            identity=_ADMIN,
        )


@pytest.mark.usefixtures("clean_registrations")
@pytest.mark.parametrize(
    ("token_url", "accepted"),
    [
        ("https://10.50.0.7/token", True),
        ("https://10.60.0.7/token", False),
        # Cloud metadata stays a hard deny even inside an allowlisted range.
        ("https://169.254.169.254/token", False),
    ],
)
async def test_create_authorization_code_honours_control_egress_allowlist(
    integration_config: AppConfig, token_url: str, accepted: bool
) -> None:
    """An in-network token endpoint allowlisted under ``control.egress`` is accepted (#1704)."""
    config = integration_config.model_copy(
        update={
            "control": ControlSurfaceConfig.model_validate(
                {"egress": {"allowed_private_subnets": ["10.50.0.0/16", "169.254.0.0/16"]}}
            )
        }
    )
    async with Context(config) as ctx:
        svc = OAuthAppRegistrationService(ctx)

        async def _create() -> OAuthAppRegistrationView:
            return await svc.create_authorization_code(
                AuthorizationCodeRegistrationCreate(
                    name="In-network AC",
                    api_vendor="v",
                    catalog_api_id="v/api",
                    display_name="V",
                    client_id="cid",
                    client_secret=SecretStr(_TEST_SECRET_PLAINTEXT),
                    authorize_url="https://ex/a",
                    token_url=token_url,
                    default_scopes=None,
                ),
                identity=_ADMIN,
            )

        if accepted:
            assert (await _create()).token_url == token_url
        else:
            with pytest.raises(InvalidOAuthAppRegistrationInputError, match="token_url"):
                await _create()


def _corp_egress_config(base: AppConfig) -> AppConfig:
    return base.model_copy(
        update={
            "control": ControlSurfaceConfig.model_validate(
                {"egress": {"allowed_private_subnets": ["10.50.0.0/16"]}}
            )
        }
    )


@pytest.mark.usefixtures("clean_registrations")
@pytest.mark.parametrize(
    ("endpoint", "accepted"),
    [("https://10.50.0.7/token", True), ("https://10.60.0.7/token", False)],
    ids=["allowlisted", "not-allowlisted"],
)
async def test_create_device_authorization_honours_control_egress_allowlist(
    integration_config: AppConfig, endpoint: str, accepted: bool
) -> None:
    async with Context(_corp_egress_config(integration_config)) as ctx:
        svc = OAuthAppRegistrationService(ctx)

        async def _create() -> OAuthAppRegistrationView:
            return await svc.create_device_authorization(
                DeviceAuthorizationRegistrationCreate(
                    name="In-network DA",
                    api_vendor="v",
                    catalog_api_id="v/api",
                    display_name="V",
                    client_id="cid",
                    authorization_endpoint="https://ex/d",
                    token_endpoint=endpoint,
                    default_scopes=None,
                ),
                identity=_ADMIN,
            )

        if accepted:
            assert (await _create()).token_endpoint == endpoint
        else:
            with pytest.raises(InvalidOAuthAppRegistrationInputError, match="token_endpoint"):
                await _create()


@pytest.mark.usefixtures("clean_registrations")
@pytest.mark.parametrize(
    ("token_url", "accepted"),
    [("https://10.50.0.7/token", True), ("https://10.60.0.7/token", False)],
    ids=["allowlisted", "not-allowlisted"],
)
async def test_update_authorization_code_honours_control_egress_allowlist(
    integration_config: AppConfig, token_url: str, accepted: bool
) -> None:
    async with Context(_corp_egress_config(integration_config)) as ctx:
        svc = OAuthAppRegistrationService(ctx)
        reg = await svc.create_authorization_code(
            AuthorizationCodeRegistrationCreate(
                name="AC",
                api_vendor="v",
                catalog_api_id="v/api",
                display_name="V",
                client_id="cid",
                client_secret=SecretStr(_TEST_SECRET_PLAINTEXT),
                authorize_url="https://ex/a",
                token_url="https://10.50.0.7/t",
                default_scopes=None,
            ),
            identity=_ADMIN,
        )
        if accepted:
            updated = await svc.update(
                reg.id, OAuthAppRegistrationUpdate(token_url=token_url), identity=_ADMIN
            )
            assert updated.token_url == token_url
        else:
            with pytest.raises(InvalidOAuthAppRegistrationInputError, match="token_url"):
                await svc.update(
                    reg.id, OAuthAppRegistrationUpdate(token_url=token_url), identity=_ADMIN
                )
            assert (await svc.get(reg.id)).token_url == "https://10.50.0.7/t"


@pytest.mark.usefixtures("clean_registrations")
async def test_create_device_authorization_rejects_unsafe_urls(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    with pytest.raises(InvalidOAuthAppRegistrationInputError, match="authorization_endpoint"):
        await svc.create_device_authorization(
            DeviceAuthorizationRegistrationCreate(
                name="DA",
                api_vendor="v",
                catalog_api_id="v/api",
                display_name="V",
                client_id="cid",
                authorization_endpoint="https://10.0.0.1/device/code",
                token_endpoint="https://ex/t",
                default_scopes=None,
            ),
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
        DeviceAuthorizationRegistrationCreate(
            name="DA",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            authorization_endpoint="https://ex/d",
            token_endpoint="https://ex/t",
            default_scopes=None,
        ),
        identity=_ADMIN,
    )
    with pytest.raises(InvalidOAuthAppRegistrationInputError, match=field):
        if field == "token_endpoint":
            await svc.update(
                reg.id, OAuthAppRegistrationUpdate(token_endpoint=url), identity=_ADMIN
            )
        else:
            await svc.update(
                reg.id, OAuthAppRegistrationUpdate(authorization_endpoint=url), identity=_ADMIN
            )
    unchanged = await svc.get(reg.id)
    assert unchanged.authorization_endpoint == "https://ex/d"
    assert unchanged.token_endpoint == "https://ex/t"


@pytest.mark.usefixtures("clean_registrations")
async def test_rotate_client_secret_updates_secret_and_timestamp(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        AuthorizationCodeRegistrationCreate(
            name="Rotate",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            client_secret=SecretStr(_TEST_SECRET_ORIGINAL),
            authorize_url="https://ex/a",
            token_url="https://ex/t",
            default_scopes=None,
        ),
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
        DeviceAuthorizationRegistrationCreate(
            name="DA",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            authorization_endpoint="https://ex/d",
            token_endpoint="https://ex/t",
            default_scopes=None,
        ),
        identity=_ADMIN,
    )
    with pytest.raises(SecretRotationNotSupportedError) as exc:
        await svc.rotate_client_secret(reg.id, client_secret="x", identity=_ADMIN)
    assert exc.value.registration_id == reg.id


@pytest.mark.usefixtures("clean_registrations")
async def test_delete_succeeds_when_no_dependents(integration_context: Context) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        AuthorizationCodeRegistrationCreate(
            name="Doomed",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            client_secret=SecretStr("s"),
            authorize_url="https://ex/a",
            token_url="https://ex/t",
            default_scopes=None,
        ),
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
        AuthorizationCodeRegistrationCreate(
            name="Referenced",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid",
            client_secret=SecretStr("s"),
            authorize_url="https://ex/a",
            token_url="https://ex/t",
            default_scopes=None,
        ),
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


@pytest.mark.usefixtures("clean_registrations")
async def test_update_auth_code_refuses_cross_origin_token_url_and_protects_live_grant(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        AuthorizationCodeRegistrationCreate(
            name="Shared AC",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid-ac",
            client_secret=SecretStr(_TEST_SECRET_ORIGINAL),
            authorize_url="https://idp.example.com/authorize",
            token_url="https://idp.example.com/v1/token",
            default_scopes=["read"],
        ),
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
            session, credential.id, registration_id=reg.id
        )
        await OAuthTokenRepository.create(
            session,
            credential_id=credential.id,
            encrypted_access_token=integration_context.encryption.encrypt("at-live"),
            encrypted_refresh_token=integration_context.encryption.encrypt("rt-live"),
            created_by="usr_alice",
        )

    posted_urls: list[str] = []

    async def fake_decrypt() -> str:
        return "rt-live"

    token_view = OAuthTokenView(
        credential_id=credential.id,
        provider="direct_oauth2",
        expires_at=dt.datetime.now(dt.UTC),
        decrypt=fake_decrypt,
    )
    provider = DirectOAuth2Provider(DirectOAuth2ProviderConfig())

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def _record_post(url: str, **_kwargs: object) -> httpx.Response:
            posted_urls.append(url)
            return httpx.Response(200, json={"access_token": "at-refreshed"})

        mock_client.post = AsyncMock(side_effect=_record_post)
        mock_client_cls.return_value = mock_client

        with pytest.raises(InvalidOAuthAppRegistrationInputError, match="origin"):
            await asyncio.gather(
                svc.update(
                    reg.id,
                    OAuthAppRegistrationUpdate(token_url="https://attacker.example.com/token"),
                    identity=_ADMIN,
                ),
                provider.refresh(integration_context, token=token_view),
            )

        # Same-origin path update succeeds and keeps the grant working.
        updated = await svc.update(
            reg.id,
            OAuthAppRegistrationUpdate(token_url="https://idp.example.com/v2/token"),
            identity=_ADMIN,
        )
        assert updated.token_url == "https://idp.example.com/v2/token"
        await provider.refresh(integration_context, token=token_view)

    assert "https://attacker.example.com/token" not in posted_urls
    assert "https://idp.example.com/v2/token" in posted_urls


@pytest.mark.usefixtures("clean_registrations")
async def test_update_device_flow_refuses_cross_origin_token_endpoint_and_protects_live_grant(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_device_authorization(
        DeviceAuthorizationRegistrationCreate(
            name="Shared DA",
            api_vendor="v",
            catalog_api_id="v/api",
            display_name="V",
            client_id="cid-da",
            authorization_endpoint="https://idp.example.com/device/code",
            token_endpoint="https://idp.example.com/v1/token",
            default_scopes=["read"],
        ),
        identity=_ADMIN,
    )
    async with integration_context.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type="oauth2_device_code",
            name="user device cred",
            api_vendor="v",
            api_name="v/api",
            catalog_api_id="v/api",
            created_by="usr_alice",
            provider="device_authorization",
            state="connected",
        )
        await CredentialRepository.set_oauth_app_registration(
            session, credential.id, registration_id=reg.id
        )
        await DeviceAuthorizationCredentialRepository.create(
            session,
            credential_id=credential.id,
            client_id="cid-da",
            token_url="https://idp.example.com/v1/token",
            authorization_endpoint="https://idp.example.com/device/code",
            requested_scopes=["read"],
            created_by="usr_alice",
        )
        await OAuthTokenRepository.create(
            session,
            credential_id=credential.id,
            encrypted_access_token=integration_context.encryption.encrypt("at-dev-live"),
            encrypted_refresh_token=integration_context.encryption.encrypt("rt-dev-live"),
            created_by="usr_alice",
        )

    posted_urls: list[str] = []

    async def fake_decrypt() -> str:
        return "rt-dev-live"

    token_view = OAuthTokenView(
        credential_id=credential.id,
        provider="device_authorization",
        expires_at=dt.datetime.now(dt.UTC),
        decrypt=fake_decrypt,
    )
    provider = DeviceAuthorizationConnectProvider()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def _record_post(url: str, **_kwargs: object) -> httpx.Response:
            posted_urls.append(url)
            return httpx.Response(200, json={"access_token": "at-dev-refreshed"})

        mock_client.post = AsyncMock(side_effect=_record_post)
        mock_client_cls.return_value = mock_client

        with pytest.raises(InvalidOAuthAppRegistrationInputError, match="origin"):
            await asyncio.gather(
                svc.update(
                    reg.id,
                    OAuthAppRegistrationUpdate(token_endpoint="https://attacker.example.com/token"),
                    identity=_ADMIN,
                ),
                provider.refresh(integration_context, token=token_view),
            )

        with pytest.raises(InvalidOAuthAppRegistrationInputError, match="origin"):
            await svc.update(
                reg.id,
                OAuthAppRegistrationUpdate(
                    authorization_endpoint="https://attacker.example.com/device/code"
                ),
                identity=_ADMIN,
            )

        # Same-origin path update succeeds and keeps the grant working.
        updated = await svc.update(
            reg.id,
            OAuthAppRegistrationUpdate(token_endpoint="https://idp.example.com/v2/token"),
            identity=_ADMIN,
        )
        assert updated.token_endpoint == "https://idp.example.com/v2/token"
        await provider.refresh(integration_context, token=token_view)

    assert "https://attacker.example.com/token" not in posted_urls
    assert "https://idp.example.com/v2/token" in posted_urls
