"""Integration tests for the CredentialService — full CRUD lifecycle with real DB."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, text

from jentic_one.admin.repos import AuditRepository
from jentic_one.control.core.schema.basic_credentials import BasicCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.device_authorization_app_registration_details import (
    DeviceAuthorizationAppRegistrationDetails,
)
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.repos import (
    CredentialRepository,
    OAuthAppRegistrationRepository,
    OAuthTokenRepository,
)
from jentic_one.control.repos.device_authorization_credential_repo import (
    DeviceAuthorizationCredentialRepository,
)
from jentic_one.control.services.credentials.errors import (
    CredentialNotFoundError,
    ImmutableFieldError,
    InvalidCredentialInputError,
)
from jentic_one.control.services.credentials.schemas.credentials import (
    ApiKeyFull,
    ApiKeyRedacted,
    BasicAuthFull,
    BasicAuthRedacted,
    BearerTokenFull,
    BearerTokenRedacted,
    CredentialCreate,
    CredentialUpdate,
    OAuth2Full,
    OAuth2Redacted,
    Sigv4Full,
    Sigv4Redacted,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseDataError
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.url_validation import validate_upstream_url_async

_ADMIN_IDENTITY = Identity(sub="admin_user", email="admin@test.com", permissions=["org:admin"])

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_credentials(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Ensure credential tables are empty before and after each test."""
    async with control_db.session() as session:
        await session.execute(delete(OAuthToken))
        await session.execute(delete(TokenValueCredential))
        await session.execute(delete(BasicCredential))
        await session.execute(delete(OAuthClientCredential))
        await session.execute(delete(DeviceAuthorizationCredential))
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(Sigv4Credential))
        await session.execute(delete(Credential))
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(delete(OAuthToken))
        await session.execute(delete(TokenValueCredential))
        await session.execute(delete(BasicCredential))
        await session.execute(delete(OAuthClientCredential))
        await session.execute(delete(DeviceAuthorizationCredential))
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(Sigv4Credential))
        await session.execute(delete(Credential))
        await session.commit()


@pytest.fixture()
def svc(integration_context: Context) -> CredentialService:
    """CredentialService wired to the integration context."""
    return CredentialService(integration_context)


def _api() -> APIReference:
    return APIReference(vendor="test-vendor", name="test-api", version="v1")


async def test_create_normalizes_dotted_vendor_and_name(
    svc: CredentialService, clean_credentials: None
) -> None:
    """A dotted vendor/name is stored in the registry's slug form (issue #656).

    The registry normalizes vendor/name on import (``httpbin.org`` ->
    ``httpbin-org``); the broker joins the credential's stored ``api_vendor``
    against that discovered, normalized vendor. If ``POST /credentials`` stored
    the raw domain the join would silently return zero rows and default-deny, so
    the service must store the same slug form.
    """
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="Dotted Vendor",
            api=APIReference(vendor="httpbin.org", name="httpbin.org", version="1.0.0"),
            token="sk-secret-token-value123",
        ),
        identity=_ADMIN_IDENTITY,
    )

    stored = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert stored.api.vendor == "httpbin-org"
    assert stored.api.name == "httpbin-org"
    assert stored.api.version == "1.0.0"


async def test_create_stores_empty_name_and_version_as_null(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """A missing api name/version is stored as NULL, not "".

    APIReferenceRequest defaults name/version to "" (not None). NULL is the
    "covers all names/versions" wildcard both the bind-time resolver and the
    broker's credential_binding_resolver rely on; an empty string matches
    NEITHER NULL nor a concrete value, so a versionless credential would serve
    nothing at execute time (issue #775). The service must coerce "" -> NULL.
    """
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.NO_AUTH,
            name="No-auth country",
            api=APIReference(vendor="country-is", name="", version=""),
        ),
        identity=_ADMIN_IDENTITY,
    )

    async with control_db.session() as session:
        row = await session.get(Credential, result.credential_id)
        assert row is not None
        assert row.api_vendor == "country-is"
        assert row.api_name is None
        assert row.api_version is None


async def test_create_bearer_token(svc: CredentialService, clean_credentials: None) -> None:
    """Create bearer_token: echoes secret once, stores encrypted with preview."""
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="My Token",
            api=_api(),
            token="sk-secret-token-value123",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert result.credential_id.startswith("cred_")
    assert result.type == CredentialType.BEARER_TOKEN
    assert isinstance(result.secret, BearerTokenFull)
    assert result.secret.token == "sk-secret-token-value123"
    assert result.active is True

    redacted = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert isinstance(redacted.details, BearerTokenRedacted)
    assert redacted.details.token_preview == "…123"
    assert redacted.type == CredentialType.BEARER_TOKEN


async def test_create_normalizes_api_vendor_and_name(
    svc: CredentialService, clean_credentials: None
) -> None:
    """api_vendor/api_name are slugified on create to match the registry form.

    Regression for #656: the registry slugifies vendor/name on import, so a
    credential stored with raw client casing/format would never match on the
    resolver's exact string comparison and would silently default-deny.
    """
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="Mixed Case Cred",
            api=APIReference(vendor="Vendor.Com", name="Some_Name", version="v1"),
            token="sk-secret-token-value123",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert result.api.vendor == "vendor-com"
    assert result.api.name == "some-name"
    assert result.api.version == "v1"

    redacted = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert redacted.api.vendor == "vendor-com"
    assert redacted.api.name == "some-name"


async def test_create_with_unknown_provider_raises_invalid_input(
    svc: CredentialService, clean_credentials: None
) -> None:
    """An unconfigured provider surfaces as a 400-mapped InvalidCredentialInputError.

    Regression guard: UnknownProviderError must be translated into a
    CredentialServiceError subclass so the global problem+json handler emits a
    spec-compliant 400 (rather than an ad-hoc JSONResponse / 500).
    """
    with pytest.raises(InvalidCredentialInputError):
        await svc.create(
            CredentialCreate(
                type=CredentialType.BEARER_TOKEN,
                name="Bad provider",
                api=_api(),
                token="sk-secret-token-value123",
                provider="does-not-exist",
            ),
            identity=_ADMIN_IDENTITY,
        )


async def test_create_api_key(svc: CredentialService, clean_credentials: None) -> None:
    """Create api_key: echoes key once, stores encrypted with preview."""
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.API_KEY,
            name="My API Key",
            api=_api(),
            key="key-abcdefg12345",
            location="header",
            field_name="X-Api-Key",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(result.secret, ApiKeyFull)
    assert result.secret.key == "key-abcdefg12345"
    assert result.secret.location == "header"
    assert result.secret.field_name == "X-Api-Key"

    redacted = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert isinstance(redacted.details, ApiKeyRedacted)
    assert redacted.details.key_preview == "…345"
    # Non-registration-backed credentials serialise both provenance fields
    # as ``None`` — the ``CredentialCard`` gates the "Shared:" badge on the
    # name being non-null, so a null-safety regression here would surface
    # the badge for every legacy credential.
    assert redacted.oauth_app_registration_id is None
    assert redacted.oauth_app_registration_name is None


async def test_create_basic_auth(svc: CredentialService, clean_credentials: None) -> None:
    """Create basic: echoes username+password once, redacted shows only username."""
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.BASIC,
            name="My Basic Cred",
            api=_api(),
            username="admin",
            password="super-secret-pw",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(result.secret, BasicAuthFull)
    assert result.secret.username == "admin"
    assert result.secret.password == "super-secret-pw"

    redacted = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert isinstance(redacted.details, BasicAuthRedacted)
    assert redacted.details.username == "admin"


async def test_create_sigv4(svc: CredentialService, clean_credentials: None) -> None:
    """Create sigv4: echoes the secret once; redacted shows only preview + scope."""
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.SIGV4,
            name="OpenSearch prod",
            api=_api(),
            access_key_id="AKIAEXAMPLE12345",
            secret_access_key="wJalrXUtnFEMI-super-secret",
            aws_region="us-east-1",
            aws_service="aoss",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(result.secret, Sigv4Full)
    assert result.secret.access_key_id == "AKIAEXAMPLE12345"
    assert result.secret.secret_access_key == "wJalrXUtnFEMI-super-secret"
    assert result.secret.aws_region == "us-east-1"
    assert result.secret.aws_service == "aoss"

    redacted = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert isinstance(redacted.details, Sigv4Redacted)
    assert redacted.details.access_key_id == "AKIAEXAMPLE12345"
    assert redacted.details.aws_region == "us-east-1"
    assert redacted.details.aws_service == "aoss"
    assert redacted.details.has_session_token is False
    # The secret preview must never expose the full secret.
    assert redacted.details.secret_preview is not None
    assert redacted.details.secret_preview != "wJalrXUtnFEMI-super-secret"


async def test_create_sigv4_with_session_token(
    svc: CredentialService, clean_credentials: None
) -> None:
    """A session token is stored and surfaced as has_session_token, never echoed redacted."""
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.SIGV4,
            name="Temp creds",
            api=_api(),
            access_key_id="ASIAEXAMPLE12345",
            secret_access_key="temp-secret",
            session_token="FQoGZXIvEXAMPLE",
            aws_region="eu-west-1",
            aws_service="execute-api",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(result.secret, Sigv4Full)
    assert result.secret.session_token == "FQoGZXIvEXAMPLE"

    redacted = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert isinstance(redacted.details, Sigv4Redacted)
    assert redacted.details.has_session_token is True


async def test_create_sigv4_missing_region_rejected(
    svc: CredentialService, clean_credentials: None
) -> None:
    with pytest.raises(InvalidCredentialInputError):
        await svc.create(
            CredentialCreate(
                type=CredentialType.SIGV4,
                name="No region",
                api=_api(),
                access_key_id="AKIAEXAMPLE12345",
                secret_access_key="secret",
                aws_service="aoss",
            ),
            identity=_ADMIN_IDENTITY,
        )


async def test_update_sigv4_rotates_keypair(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Rotating both key halves updates the stored secret preview."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.SIGV4,
            name="Rotate me",
            api=_api(),
            access_key_id="AKIAOLDKEY000000",
            secret_access_key="old-secret-aaa",
            aws_region="us-east-1",
            aws_service="aoss",
        ),
        identity=_ADMIN_IDENTITY,
    )
    before = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    assert isinstance(before.details, Sigv4Redacted)
    old_preview = before.details.secret_preview
    old_updated_at = before.updated_at

    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(
            type=CredentialType.SIGV4,
            access_key_id="AKIANEWKEY000000",
            secret_access_key="brand-new-secret-zzz",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(updated.details, Sigv4Redacted)
    assert updated.details.access_key_id == "AKIANEWKEY000000"
    assert updated.details.secret_preview != old_preview
    # A key rotation is a real mutation: updated_at must advance (honest signal,
    # #881) and it must leave an audit trail — both are gated on ``changed``.
    assert old_updated_at is not None
    assert updated.updated_at is not None
    assert updated.updated_at > old_updated_at


async def test_update_sigv4_half_keypair_rejected(
    svc: CredentialService, clean_credentials: None
) -> None:
    """access_key_id without its secret (or vice versa) is a 400, not a partial write."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.SIGV4,
            name="Half rotate",
            api=_api(),
            access_key_id="AKIAKEY000000000",
            secret_access_key="secret",
            aws_region="us-east-1",
            aws_service="aoss",
        ),
        identity=_ADMIN_IDENTITY,
    )
    with pytest.raises(InvalidCredentialInputError):
        await svc.update(
            created.credential_id,
            CredentialUpdate(type=CredentialType.SIGV4, access_key_id="AKIANEWKEY000000"),
            identity=_ADMIN_IDENTITY,
        )


async def test_create_basic_auth_long_snapshot_version(
    svc: CredentialService, clean_credentials: None
) -> None:
    """A SNAPSHOT version longer than 50 chars is stored, not a 500 (#690).

    The registry does not length-cap versions; a SNAPSHOT build carries a
    commit-hash suffix that overflowed the old VARCHAR(50) and raised a
    StringDataRightTruncationError on insert. api_version is now VARCHAR(100).
    """
    long_version = "1001.0.0-SNAPSHOT-636312f2dc6e26921216979d4ae12655beeff255"
    assert len(long_version) > 50
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.BASIC,
            name="Jira Basic Cred",
            api=APIReference(
                vendor="atlassian-com", name="atlassian-com-jira", version=long_version
            ),
            username="admin",
            password="super-secret-pw",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert result.api.version == long_version

    redacted = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert redacted.api.version == long_version


@pytest.mark.skipif(
    os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower() == "sqlite",
    reason="VARCHAR length is only enforced on Postgres",
)
async def test_create_oversized_api_version_is_clean_client_error(
    svc: CredentialService, clean_credentials: None
) -> None:
    """A value beyond the column width surfaces as DatabaseDataError, not a 500.

    DatabaseDataError is mapped to a 400 by the control web layer (safe detail,
    no raw SQL leaked), so an over-limit field is reported to the caller as a
    client error rather than an unhandled server fault (#690).
    """
    with pytest.raises(DatabaseDataError):
        await svc.create(
            CredentialCreate(
                type=CredentialType.BASIC,
                name="Oversized Version",
                api=APIReference(vendor="acme", name="acme-api", version="v" * 200),
                username="admin",
                password="pw",  # pragma: allowlist secret
            ),
            identity=_ADMIN_IDENTITY,
        )


async def test_create_oauth2(svc: CredentialService, clean_credentials: None) -> None:
    """Create oauth2: stores client config, no oauth_tokens yet (unconnected)."""
    result = await svc.create(
        CredentialCreate(
            type=CredentialType.OAUTH2,
            name="My OAuth2",
            api=_api(),
            grant_type="client_credentials",
            token_url="https://auth.example.com/token",
            client_id="client-123",
            client_secret="secret-456",
            scopes=["read", "write"],
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(result.secret, OAuth2Full)
    assert result.secret.client_id == "client-123"
    assert result.secret.client_secret == "secret-456"
    assert result.secret.grant_type == "client_credentials"

    redacted = await svc.get(result.credential_id, identity=_ADMIN_IDENTITY)
    assert isinstance(redacted.details, OAuth2Redacted)
    assert redacted.details.client_id == "client-123"
    assert redacted.details.token_url == "https://auth.example.com/token"
    assert redacted.details.scopes == ["read", "write"]


async def test_list_credentials(svc: CredentialService, clean_credentials: None) -> None:
    """List returns all credentials with pagination."""
    await svc.create(
        CredentialCreate(type=CredentialType.BEARER_TOKEN, name="Token 1", api=_api(), token="t1"),
        identity=_ADMIN_IDENTITY,
    )
    await svc.create(
        CredentialCreate(type=CredentialType.BEARER_TOKEN, name="Token 2", api=_api(), token="t2"),
        identity=_ADMIN_IDENTITY,
    )

    page = await svc.list_all(identity=_ADMIN_IDENTITY, limit=10)
    assert len(page.data) == 2
    assert page.has_more is False


async def test_list_pagination(svc: CredentialService, clean_credentials: None) -> None:
    """List supports cursor-based pagination."""
    for i in range(3):
        await svc.create(
            CredentialCreate(
                type=CredentialType.BEARER_TOKEN, name=f"Token {i}", api=_api(), token=f"tok{i}"
            ),
            identity=_ADMIN_IDENTITY,
        )

    page1 = await svc.list_all(identity=_ADMIN_IDENTITY, limit=2)
    assert len(page1.data) == 2
    assert page1.has_more is True
    assert page1.next_cursor is not None

    page2 = await svc.list_all(identity=_ADMIN_IDENTITY, cursor=page1.next_cursor, limit=2)
    assert len(page2.data) == 1
    assert page2.has_more is False


async def test_update_rotates_bearer_token(svc: CredentialService, clean_credentials: None) -> None:
    """Update with new token rotates the encrypted value and updates preview."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN, name="Rotate Me", api=_api(), token="old-token-123"
        ),
        identity=_ADMIN_IDENTITY,
    )

    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(type=CredentialType.BEARER_TOKEN, token="new-token-xyz"),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(updated.details, BearerTokenRedacted)
    assert updated.details.token_preview == "…xyz"
    # A persisted change must advance updated_at past created_at (#739) — both on
    # the update return value and on a fresh read.
    assert updated.updated_at is not None
    assert updated.updated_at > created.created_at
    reread = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    assert reread.updated_at is not None
    assert reread.updated_at > reread.created_at


async def test_update_name(svc: CredentialService, clean_credentials: None) -> None:
    """Update can change the credential name."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN, name="Original", api=_api(), token="tok"
        ),
        identity=_ADMIN_IDENTITY,
    )
    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(type=CredentialType.BEARER_TOKEN, name="Renamed"),
        identity=_ADMIN_IDENTITY,
    )
    assert updated.name == "Renamed"


async def test_update_rejects_type_change(svc: CredentialService, clean_credentials: None) -> None:
    """Update rejects changes to the type field."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN, name="Immutable", api=_api(), token="tok"
        ),
        identity=_ADMIN_IDENTITY,
    )
    with pytest.raises(ImmutableFieldError):
        await svc.update(
            created.credential_id,
            CredentialUpdate(type=CredentialType.API_KEY),
            identity=_ADMIN_IDENTITY,
        )


async def test_update_rotates_api_key_advances_updated_at(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Rotating an api_key secret bumps updated_at (#739)."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.API_KEY,
            name="Rotate Key",
            api=_api(),
            key="old-key-value",
            location="query",
            field_name="appid",
        ),
        identity=_ADMIN_IDENTITY,
    )
    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(type=CredentialType.API_KEY, key="new-key-value"),
        identity=_ADMIN_IDENTITY,
    )
    assert updated.updated_at is not None
    assert updated.updated_at > created.created_at


async def test_update_noop_does_not_bump_updated_at(
    svc: CredentialService, clean_credentials: None
) -> None:
    """A PATCH that persists nothing must leave updated_at frozen (#739).

    The SPA echoes the stored field_name/location on every api_key PATCH; a
    request that carries only those (matching) echoes and no new secret changes
    nothing, so updated_at must not move — otherwise it is a lying signal.
    """
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.API_KEY,
            name="No-op",
            api=_api(),
            key="key-value-123",
            location="query",
            field_name="appid",
        ),
        identity=_ADMIN_IDENTITY,
    )
    before = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)

    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(type=CredentialType.API_KEY, field_name="appid", location="query"),
        identity=_ADMIN_IDENTITY,
    )
    assert updated.updated_at == before.updated_at

    reread = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    assert reread.updated_at == before.updated_at


async def test_update_rejects_field_name_change(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Changing the api_key field_name is rejected as immutable (#589)."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.API_KEY,
            name="Locked Binding",
            api=_api(),
            key="key-value-123",
            location="query",
            field_name="appid",
        ),
        identity=_ADMIN_IDENTITY,
    )
    with pytest.raises(ImmutableFieldError):
        await svc.update(
            created.credential_id,
            CredentialUpdate(type=CredentialType.API_KEY, field_name="Default"),
            identity=_ADMIN_IDENTITY,
        )


async def test_update_rejects_location_change(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Changing the api_key location is rejected as immutable (#589)."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.API_KEY,
            name="Locked Location",
            api=_api(),
            key="key-value-123",
            location="query",
            field_name="appid",
        ),
        identity=_ADMIN_IDENTITY,
    )
    with pytest.raises(ImmutableFieldError):
        await svc.update(
            created.credential_id,
            CredentialUpdate(type=CredentialType.API_KEY, location="header"),
            identity=_ADMIN_IDENTITY,
        )


async def test_update_key_with_echoed_binding_succeeds(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Rotating the key while echoing the stored binding is allowed (#589).

    The SPA always resends the seeded field_name/location; a matching echo
    alongside a new key must rotate the secret and bump updated_at, not 409.
    """
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.API_KEY,
            name="Echo Binding",
            api=_api(),
            key="old-key-value",
            location="query",
            field_name="appid",
        ),
        identity=_ADMIN_IDENTITY,
    )
    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(
            type=CredentialType.API_KEY,
            key="new-key-value",
            field_name="appid",
            location="query",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(updated.details, ApiKeyRedacted)
    assert updated.details.field_name == "appid"
    assert updated.updated_at is not None
    assert updated.updated_at > created.created_at


async def test_delete_cascade(svc: CredentialService, clean_credentials: None) -> None:
    """Delete removes the credential and its sibling row via cascade."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN, name="Delete Me", api=_api(), token="tok"
        ),
        identity=_ADMIN_IDENTITY,
    )
    await svc.delete(created.credential_id, identity=_ADMIN_IDENTITY)

    with pytest.raises(CredentialNotFoundError):
        await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)


async def test_delete_nonexistent_raises(svc: CredentialService, clean_credentials: None) -> None:
    """Delete raises CredentialNotFoundError for unknown IDs."""
    with pytest.raises(CredentialNotFoundError):
        await svc.delete("cred_nonexistent", identity=_ADMIN_IDENTITY)


async def test_server_variables_create_and_read(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Create with server_variables persists them and returns on get."""
    server_vars = {"your-domain": "acme", "region": "us-east"}
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="With Server Vars",
            api=_api(),
            token="tok",
            server_variables=server_vars,
        ),
        identity=_ADMIN_IDENTITY,
    )

    redacted = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    assert redacted.server_variables == server_vars


async def test_server_variables_update(svc: CredentialService, clean_credentials: None) -> None:
    """Update can modify server_variables."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="Update Vars",
            api=_api(),
            token="tok",
            server_variables={"region": "us"},
        ),
        identity=_ADMIN_IDENTITY,
    )

    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(
            type=CredentialType.BEARER_TOKEN,
            server_variables={"region": "eu", "domain": "new"},
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert updated.server_variables == {"region": "eu", "domain": "new"}


async def test_server_variables_none_by_default(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Credentials created without server_variables return None."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="No Vars",
            api=_api(),
            token="tok",
        ),
        identity=_ADMIN_IDENTITY,
    )
    redacted = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    assert redacted.server_variables is None


@pytest.mark.parametrize(
    "fields",
    [
        {"type": CredentialType.BEARER_TOKEN, "name": ""},
        {"type": CredentialType.BEARER_TOKEN, "token": ""},
        {"type": CredentialType.API_KEY, "key": ""},
        {"type": CredentialType.BASIC, "username": ""},
        {"type": CredentialType.BASIC, "password": ""},
        {"type": CredentialType.OAUTH2, "client_secret": ""},
        {"type": CredentialType.OAUTH2, "token_url": ""},
        {"type": CredentialType.SIGV4, "access_key_id": "", "secret_access_key": ""},
        {"type": CredentialType.SIGV4, "session_token": ""},
        {"type": CredentialType.SIGV4, "aws_region": ""},
        {"type": CredentialType.SIGV4, "aws_service": ""},
        # Whitespace-only is as empty as "" once injected into a header.
        {"type": CredentialType.BEARER_TOKEN, "token": "  "},
        {"type": CredentialType.API_KEY, "key": " "},
        {"type": CredentialType.BASIC, "password": "\t"},
        {"type": CredentialType.BEARER_TOKEN, "name": " "},
        # Scope items are single RFC 6749 scope-tokens.
        {"type": CredentialType.OAUTH2, "scopes": [""]},
        {"type": CredentialType.OAUTH2, "scopes": ["read", " "]},
        {"type": CredentialType.OAUTH2, "scopes": ["a b"]},
    ],
)
async def test_update_rejects_empty_secret_fields(
    svc: CredentialService, clean_credentials: None, fields: dict[str, object]
) -> None:
    """Pins #1425: a blank secret/signing field or a malformed scope item is
    rejected below the web layer. Validation runs before the stored type is
    checked, so one bearer credential serves every variant."""
    created = await svc.create(
        CredentialCreate(type=CredentialType.BEARER_TOKEN, name="E", api=_api(), token="tok"),
        identity=_ADMIN_IDENTITY,
    )
    before = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    with pytest.raises(InvalidCredentialInputError):
        await svc.update(
            created.credential_id,
            CredentialUpdate.model_validate(fields),
            identity=_ADMIN_IDENTITY,
        )
    redacted = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    assert redacted.updated_at == before.updated_at


async def test_update_oauth2_token_url_and_scopes_apply_without_secret(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """Pins #1425: token_url/scopes persist alone; the stored secret is untouched."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.OAUTH2,
            name="OAuth edit",
            api=_api(),
            grant_type="client_credentials",
            token_url="https://auth.example.com/token",
            client_id="client-123",
            client_secret="secret-456",
            scopes=["read"],
        ),
        identity=_ADMIN_IDENTITY,
    )
    async with control_db.session() as session:
        row = await session.get(OAuthClientCredential, created.credential_id)
        assert row is not None
        secret_before = row.encrypted_client_secret

    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(
            type=CredentialType.OAUTH2,
            token_url="https://auth.example.com/v2/token",
            scopes=["c"],
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(updated.details, OAuth2Redacted)
    assert updated.details.token_url == "https://auth.example.com/v2/token"
    assert updated.details.scopes == ["c"]
    assert updated.updated_at is not None
    assert updated.updated_at > created.created_at
    async with control_db.session() as session:
        row = await session.get(OAuthClientCredential, created.credential_id)
        assert row is not None
        assert row.encrypted_client_secret == secret_before


def _device_code_create(name: str) -> CredentialCreate:
    return CredentialCreate(
        type=CredentialType.OAUTH2,
        name=name,
        api=_api(),
        grant_type="device_code",
        token_url="https://auth.example.com/token",
        authorize_url="https://auth.example.com/device",
        client_id="device-client",
        scopes=["read"],
    )


async def test_update_device_code_applies_token_url_and_scopes(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """Device-code edits land on their own table and are not masked by the
    scopes stamped by an earlier connect; ``[]`` clears to None; a
    client_secret is a 400 (public client)."""
    created = await svc.create(_device_code_create("Device edit"), identity=_ADMIN_IDENTITY)
    # Simulate an earlier connect, which stamps granted_scopes on the row.
    async with control_db.transaction() as session:
        await DeviceAuthorizationCredentialRepository.update_fields(
            session, created.credential_id, granted_scopes=["old"]
        )

    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(
            type=CredentialType.OAUTH2,
            token_url="https://auth.example.com/v2/token",
            scopes=["read", "write"],
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(updated.details, OAuth2Redacted)
    assert updated.details.token_url == "https://auth.example.com/v2/token"
    assert updated.details.scopes == ["read", "write"]

    cleared = await svc.update(
        created.credential_id,
        CredentialUpdate(type=CredentialType.OAUTH2, scopes=[]),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(cleared.details, OAuth2Redacted)
    assert cleared.details.scopes is None

    with pytest.raises(InvalidCredentialInputError):
        await svc.update(
            created.credential_id,
            CredentialUpdate(
                type=CredentialType.OAUTH2,
                client_secret="nope",  # pragma: allowlist secret
            ),
            identity=_ADMIN_IDENTITY,
        )


async def test_update_shared_registration_client_fields_rejected(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """A credential bound to a shared app registration resolves its client
    config from the registration at runtime, so per-credential token_url /
    scopes edits are a 400, not a 200 that writes a dead column."""
    created = await svc.create(_device_code_create("Shared device"), identity=_ADMIN_IDENTITY)
    async with control_db.transaction() as session:
        registration = await OAuthAppRegistrationRepository.create_device_authorization(
            session,
            name="Shared device app",
            api_vendor="test-vendor",
            catalog_api_id="test-vendor",
            display_name="Shared",
            client_id="device-client",
            authorization_endpoint="https://auth.example.com/device",
            token_endpoint="https://auth.example.com/token",
            created_by=_ADMIN_IDENTITY.sub,
        )
        registration_id = registration.id
        await CredentialRepository.set_oauth_app_registration(
            session, created.credential_id, registration_id=registration_id
        )
    try:
        for edit in (
            CredentialUpdate(type=CredentialType.OAUTH2, token_url="https://x.example.com/t"),
            CredentialUpdate(type=CredentialType.OAUTH2, scopes=["write"]),
        ):
            with pytest.raises(InvalidCredentialInputError):
                await svc.update(created.credential_id, edit, identity=_ADMIN_IDENTITY)
        after = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
        assert isinstance(after.details, OAuth2Redacted)
        assert after.details.token_url == "https://auth.example.com/token"
        assert after.details.scopes == ["read"]
        # Header edits stay allowed on shared-registration credentials.
        renamed = await svc.update(
            created.credential_id,
            CredentialUpdate(type=CredentialType.OAUTH2, name="Renamed shared"),
            identity=_ADMIN_IDENTITY,
        )
        assert renamed.name == "Renamed shared"
    finally:
        async with control_db.transaction() as session:
            await session.execute(delete(DeviceAuthorizationCredential))
            await session.execute(delete(Credential).where(Credential.id == created.credential_id))
            await session.execute(
                delete(DeviceAuthorizationAppRegistrationDetails).where(
                    DeviceAuthorizationAppRegistrationDetails.id == registration_id
                )
            )
            await session.execute(
                delete(OAuthAppRegistration).where(OAuthAppRegistration.id == registration_id)
            )


async def test_update_no_auth_name_and_active(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Pins #1425: a no_auth credential accepts header edits."""
    created = await svc.create(
        CredentialCreate(type=CredentialType.NO_AUTH, name="Open", api=_api()),
        identity=_ADMIN_IDENTITY,
    )
    updated = await svc.update(
        created.credential_id,
        CredentialUpdate(type=CredentialType.NO_AUTH, name="Renamed", active=False),
        identity=_ADMIN_IDENTITY,
    )
    assert updated.name == "Renamed"
    assert updated.active is False
    assert updated.updated_at is not None
    assert updated.updated_at > created.created_at


def _auth_code_create(name: str) -> CredentialCreate:
    return CredentialCreate(
        type=CredentialType.OAUTH2,
        name=name,
        api=_api(),
        grant_type="authorization_code",
        token_url="https://auth.example.com/token",
        authorize_url="https://auth.example.com/authorize",
        client_id="ac-client",
        client_secret="ac-secret-original",  # pragma: allowlist secret
        scopes=["read"],
    )


async def _connect(control_db: DatabaseSession, credential_id: str) -> None:
    """Stand in for a completed sign-in: a live token row with a refresh token."""
    async with control_db.transaction() as session:
        await OAuthTokenRepository.create(
            session,
            credential_id=credential_id,
            encrypted_access_token="enc-access",
            encrypted_refresh_token="enc-refresh",
            created_by=_ADMIN_IDENTITY.sub,
        )


async def _has_token(control_db: DatabaseSession, credential_id: str) -> bool:
    async with control_db.session() as session:
        return await OAuthTokenRepository.get_by_credential(session, credential_id) is not None


async def test_update_auth_code_cross_origin_token_url_requires_secret(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """A token_url on another origin would receive the stored client secret and
    refresh token, neither of which the caller can read. It needs client_secret
    in the same PATCH and drops the live token; same-origin path edits don't."""
    created = await svc.create(_auth_code_create("Auth code"), identity=_ADMIN_IDENTITY)
    cid = created.credential_id
    await _connect(control_db, cid)

    with pytest.raises(InvalidCredentialInputError, match="client_secret"):
        await svc.update(
            cid,
            CredentialUpdate(type=CredentialType.OAUTH2, token_url="https://evil.example.net/t"),
            identity=_ADMIN_IDENTITY,
        )
    current = await svc.get(cid, identity=_ADMIN_IDENTITY)
    assert isinstance(current.details, OAuth2Redacted)
    assert current.details.token_url == "https://auth.example.com/token"
    assert await _has_token(control_db, cid)

    # Explicit default port and a new path keep the origin.
    same_origin = await svc.update(
        cid,
        CredentialUpdate(
            type=CredentialType.OAUTH2, token_url="https://AUTH.example.com:443/oauth/token"
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(same_origin.details, OAuth2Redacted)
    assert same_origin.details.connected is True
    assert await _has_token(control_db, cid)

    async with control_db.session() as session:
        row = await session.get(OAuthClientCredential, cid)
        assert row is not None
        secret_before = row.encrypted_client_secret
    moved = await svc.update(
        cid,
        CredentialUpdate(
            type=CredentialType.OAUTH2,
            token_url="https://login.other.example.org/token",
            client_secret="ac-secret-new",  # pragma: allowlist secret
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(moved.details, OAuth2Redacted)
    assert moved.details.token_url == "https://login.other.example.org/token"
    assert moved.details.connected is False
    assert not await _has_token(control_db, cid)
    async with control_db.session() as session:
        row = await session.get(OAuthClientCredential, cid)
        assert row is not None
        assert row.encrypted_client_secret != secret_before


async def test_update_device_code_cross_origin_token_url_rejected(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """Device flow has no secret to re-enter, so a token_url on another origin
    is refused and the live token stays where it is."""
    created = await svc.create(_device_code_create("Device move"), identity=_ADMIN_IDENTITY)
    cid = created.credential_id
    await _connect(control_db, cid)

    with pytest.raises(InvalidCredentialInputError, match="origin"):
        await svc.update(
            cid,
            CredentialUpdate(type=CredentialType.OAUTH2, token_url="https://evil.example.net/t"),
            identity=_ADMIN_IDENTITY,
        )
    current = await svc.get(cid, identity=_ADMIN_IDENTITY)
    assert isinstance(current.details, OAuth2Redacted)
    assert current.details.token_url == "https://auth.example.com/token"
    assert current.details.connected is True
    assert await _has_token(control_db, cid)


async def test_update_device_code_frozen_while_flow_pending(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """A pending device flow polls the stored token_url and stamps the confirmed
    scopes on success, so neither is editable until the flow ends."""
    created = await svc.create(_device_code_create("Device pending"), identity=_ADMIN_IDENTITY)
    cid = created.credential_id
    async with control_db.transaction() as session:
        await DeviceAuthorizationCredentialRepository.update_fields(
            session,
            cid,
            encrypted_device_code="enc-device-code",
            user_code="ABCD-1234",
            granted_scopes=["read"],
        )

    for edit in (
        CredentialUpdate(type=CredentialType.OAUTH2, scopes=["read", "write"]),
        CredentialUpdate(type=CredentialType.OAUTH2, token_url="https://auth.example.com/v2/token"),
    ):
        with pytest.raises(InvalidCredentialInputError, match="in progress"):
            await svc.update(cid, edit, identity=_ADMIN_IDENTITY)
    async with control_db.session() as session:
        dfc = await DeviceAuthorizationCredentialRepository.get_by_credential(session, cid)
        assert dfc is not None
        assert dfc.granted_scopes == ["read"]
        assert dfc.token_url == "https://auth.example.com/token"

    # Header edits don't touch the flow and stay allowed.
    renamed = await svc.update(
        cid,
        CredentialUpdate(type=CredentialType.OAUTH2, name="Device renamed"),
        identity=_ADMIN_IDENTITY,
    )
    assert renamed.name == "Device renamed"


async def test_update_device_code_expired_pending_flow_does_not_block(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """A flow past its device-code expiry can no longer complete, so it does
    not block a scopes or token_url edit even before the poll scanner
    retires it."""
    created = await svc.create(_device_code_create("Device expired"), identity=_ADMIN_IDENTITY)
    cid = created.credential_id
    async with control_db.transaction() as session:
        await DeviceAuthorizationCredentialRepository.update_fields(
            session,
            cid,
            encrypted_device_code="enc-device-code",
            user_code="ABCD-1234",
            device_code_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )

    updated = await svc.update(
        cid,
        CredentialUpdate(
            type=CredentialType.OAUTH2,
            token_url="https://auth.example.com/v2/token",
            scopes=["read", "write"],
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert isinstance(updated.details, OAuth2Redacted)
    assert updated.details.token_url == "https://auth.example.com/v2/token"
    assert updated.details.scopes == ["read", "write"]

    # A flow still inside its expiry keeps the edit blocked.
    async with control_db.transaction() as session:
        await DeviceAuthorizationCredentialRepository.update_fields(
            session, cid, device_code_expires_at=datetime.now(UTC) + timedelta(minutes=5)
        )
    with pytest.raises(InvalidCredentialInputError, match="in progress"):
        await svc.update(
            cid,
            CredentialUpdate(type=CredentialType.OAUTH2, scopes=["read"]),
            identity=_ADMIN_IDENTITY,
        )


_OWNER_A = Identity(sub="usr_owner_a_1425", permissions=["credentials:write"])
_OTHER_B = Identity(sub="usr_other_b_1425", permissions=["credentials:write"])


@pytest.mark.parametrize(
    "token_url", ["http://169.254.169.254/latest/meta-data", "https://auth.example.com/v2/token"]
)
@pytest.mark.parametrize("target", ["missing", "other-users"])
async def test_update_token_url_on_invisible_credential_is_not_found(
    svc: CredentialService,
    clean_credentials: None,
    monkeypatch: pytest.MonkeyPatch,
    token_url: str,
    target: str,
) -> None:
    """Pins #1425: a token_url edit on a credential the caller cannot see (one
    that does not exist, or one another user owns) answers 404 whether or not
    the URL would pass validation, and resolves no DNS for it: the access
    check runs before the SSRF validator."""
    if target == "missing":
        credential_id, caller = "cred_does_not_exist", _ADMIN_IDENTITY
    else:
        created = await svc.create(_auth_code_create("Owned by A"), identity=_OWNER_A)
        credential_id, caller = created.credential_id, _OTHER_B
    validator = AsyncMock(side_effect=AssertionError("token_url validated before access check"))
    monkeypatch.setattr(
        "jentic_one.control.services.credentials.service.validate_upstream_url_async", validator
    )

    with pytest.raises(CredentialNotFoundError):
        await svc.update(
            credential_id,
            CredentialUpdate(type=CredentialType.OAUTH2, token_url=token_url),
            identity=caller,
        )
    validator.assert_not_called()


_IS_SQLITE = os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower() == "sqlite"
_MOVED_TOKEN_URL = "https://login.other.example.org/token"
_PATH_EDIT_TOKEN_URL = "https://auth.example.com/v2/token"
_MOVE_SECRET = "ac-secret-for-other-origin"  # pragma: allowlist secret


async def _raises(awaitable: Awaitable[object]) -> BaseException | None:
    """Await *awaitable* and return the exception it raised (None if none)."""
    try:
        await awaitable
    except Exception as exc:
        return exc
    return None


async def _race_path_edit_behind_cross_origin_move(
    svc: CredentialService,
    control_db: DatabaseSession,
    integration_context: Context,
    monkeypatch: pytest.MonkeyPatch,
    *,
    wait_until_parked: Callable[[], Awaitable[None]],
) -> None:
    """PATCH A moves token_url to another origin with a new client_secret and
    holds the refresh lock; PATCH B, a same-origin path edit with no secret,
    queues behind it. Once A commits, B must compare against A's URL, so it
    is a cross-origin edit without client_secret and is refused: A's secret
    stays paired with the origin it was entered for."""
    created = await svc.create(_auth_code_create("Raced"), identity=_ADMIN_IDENTITY)
    cid = created.credential_id
    await _connect(control_db, cid)

    real_lock = OAuthTokenRepository.acquire_refresh_lock
    real_get_updatable = CredentialService._get_updatable
    move_locked = asyncio.Event()
    release_move = asyncio.Event()
    lock_calls = 0
    # Per task: the order of its credential reads and its lock acquisition.
    steps: dict[asyncio.Task[Any] | None, list[str]] = {}

    async def lock_holding_the_first_caller(session: Any, credential_id: str) -> None:
        nonlocal lock_calls
        lock_calls += 1
        caller = lock_calls
        await real_lock(session, credential_id)
        steps.setdefault(asyncio.current_task(), []).append("lock")
        if caller == 1:
            move_locked.set()
            await release_move.wait()

    async def recording_get_updatable(*args: Any) -> Credential:
        steps.setdefault(asyncio.current_task(), []).append("read")
        return await real_get_updatable(*args)

    monkeypatch.setattr(
        OAuthTokenRepository, "acquire_refresh_lock", staticmethod(lock_holding_the_first_caller)
    )
    monkeypatch.setattr(CredentialService, "_get_updatable", staticmethod(recording_get_updatable))
    path_edit_validated = asyncio.Event()

    async def validate(url: str) -> str:
        result = await validate_upstream_url_async(url)
        if url == _PATH_EDIT_TOKEN_URL:
            path_edit_validated.set()
        return result

    monkeypatch.setattr(
        "jentic_one.control.services.credentials.service.validate_upstream_url_async", validate
    )

    async with asyncio.timeout(30), asyncio.TaskGroup() as tg:
        move = tg.create_task(
            svc.update(
                cid,
                CredentialUpdate(
                    type=CredentialType.OAUTH2,
                    token_url=_MOVED_TOKEN_URL,
                    client_secret=_MOVE_SECRET,
                ),
                identity=_ADMIN_IDENTITY,
            )
        )
        await move_locked.wait()
        path_edit = tg.create_task(
            _raises(
                svc.update(
                    cid,
                    CredentialUpdate(type=CredentialType.OAUTH2, token_url=_PATH_EDIT_TOKEN_URL),
                    identity=_ADMIN_IDENTITY,
                )
            )
        )
        await path_edit_validated.wait()
        await wait_until_parked()
        release_move.set()

    moved = move.result()
    assert isinstance(moved.details, OAuth2Redacted)
    assert moved.details.token_url == _MOVED_TOKEN_URL
    assert moved.details.connected is False
    refused = path_edit.result()
    assert isinstance(refused, InvalidCredentialInputError)
    assert "client_secret" in str(refused)
    assert lock_calls == 2
    # The read-only access check, then the lock, then the in-transaction
    # read the origin comparison uses. On SQLite ``BEGIN IMMEDIATE`` already
    # orders that read after the move's commit; this pins the order the
    # Postgres advisory lock relies on.
    assert steps[path_edit] == ["read", "lock", "read"]
    assert steps[move] == ["read", "lock", "read"]
    assert not await _has_token(control_db, cid)
    async with control_db.session() as session:
        row = await session.get(OAuthClientCredential, cid)
        assert row is not None
        assert row.token_url == _MOVED_TOKEN_URL
        assert integration_context.encryption.decrypt(row.encrypted_client_secret) == _MOVE_SECRET


@pytest.mark.skipif(not _IS_SQLITE, reason="SQLite variant; Postgres has its own below")
async def test_update_path_edit_queued_behind_cross_origin_move_rechecks_origin_sqlite(
    svc: CredentialService,
    clean_credentials: None,
    control_db: DatabaseSession,
    integration_context: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins #1425 on SQLite: the path edit's ``BEGIN IMMEDIATE`` waits for the
    move's write transaction, so it reads the moved row whenever it starts
    (the short pause only gives it time to queue). The recorded step order
    still pins the lock as the transaction's first statement."""

    async def queued() -> None:
        await asyncio.sleep(0.1)

    await _race_path_edit_behind_cross_origin_move(
        svc, control_db, integration_context, monkeypatch, wait_until_parked=queued
    )


@pytest.mark.skipif(_IS_SQLITE, reason="advisory locks are Postgres-only")
async def test_update_path_edit_queued_behind_cross_origin_move_rechecks_origin_postgres(
    svc: CredentialService,
    clean_credentials: None,
    control_db: DatabaseSession,
    integration_context: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins #1425 on Postgres: the path edit is released only once it is
    parked on the advisory lock the move holds. Its credential and client
    rows must be read after that lock is granted; read before it, they would
    still carry the old origin, the path edit would pass as same-origin
    without a secret, and the move's secret would end up paired with the old
    origin."""

    async def parked_on_advisory_lock() -> None:
        while True:
            async with control_db.session() as session:
                waiting = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM pg_locks"
                            " WHERE locktype = 'advisory' AND NOT granted"
                        )
                    )
                ).scalar_one()
            if waiting:
                return
            await asyncio.sleep(0.01)

    await _race_path_edit_behind_cross_origin_move(
        svc,
        control_db,
        integration_context,
        monkeypatch,
        wait_until_parked=parked_on_advisory_lock,
    )


async def test_update_oauth2_without_client_config_rejected(
    svc: CredentialService, clean_credentials: None, control_db: DatabaseSession
) -> None:
    """An OAuth2 credential with no client row (a managed provider) answers a
    client-field edit with 400, and the header edit in the same PATCH rolls back."""
    created = await svc.create(_auth_code_create("No client row"), identity=_ADMIN_IDENTITY)
    cid = created.credential_id
    async with control_db.transaction() as session:
        await session.execute(delete(OAuthClientCredential).where(OAuthClientCredential.id == cid))

    with pytest.raises(InvalidCredentialInputError, match="no client configuration"):
        await svc.update(
            cid,
            CredentialUpdate(type=CredentialType.OAUTH2, name="Renamed", scopes=["write"]),
            identity=_ADMIN_IDENTITY,
        )
    current = await svc.get(cid, identity=_ADMIN_IDENTITY)
    assert current.name == "No client row"


async def test_update_oauth2_audit_diff_records_config_not_secrets(
    svc: CredentialService, clean_credentials: None, integration_context: Context
) -> None:
    """The audit diff of a config edit names what changed (token_url, scopes)
    and flags the secret rotation, without ever carrying a secret value."""
    created = await svc.create(_auth_code_create("Audited"), identity=_ADMIN_IDENTITY)
    cid = created.credential_id
    await svc.update(
        cid,
        CredentialUpdate(
            type=CredentialType.OAUTH2,
            token_url="https://login.other.example.org/token",
            client_secret="ac-secret-rotated",  # pragma: allowlist secret
            scopes=["read", "write"],
        ),
        identity=_ADMIN_IDENTITY,
    )
    async with integration_context.admin_db.session() as session:
        entries = await AuditRepository.list_by_target(session, AuditTargetType.CREDENTIAL, cid)
    updates = [e for e in entries if e.action == AuditAction.UPDATE]
    assert len(updates) == 1
    entry = updates[0]
    assert entry.diff is not None
    assert entry.diff["modified"]["token_url"] == {
        "old": "https://auth.example.com/token",
        "new": "https://login.other.example.org/token",
    }
    assert entry.diff["modified"]["scopes"] == {"old": ["read"], "new": ["read", "write"]}
    assert entry.diff["added"]["client_secret_rotated"] is True
    recorded = repr((entry.before, entry.after, entry.diff))
    assert "ac-secret" not in recorded


async def test_create_rejects_malformed_scopes(
    svc: CredentialService, clean_credentials: None
) -> None:
    """Create applies the same scope-token rule as update."""
    with pytest.raises(InvalidCredentialInputError, match="scope"):
        await svc.create(
            CredentialCreate(
                type=CredentialType.OAUTH2,
                name="Bad scopes",
                api=_api(),
                grant_type="client_credentials",
                token_url="https://auth.example.com/token",
                client_id="client-123",
                client_secret="secret-456",  # pragma: allowlist secret
                scopes=["read write"],
            ),
            identity=_ADMIN_IDENTITY,
        )
