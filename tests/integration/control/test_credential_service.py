"""Integration tests for the CredentialService — full CRUD lifecycle with real DB."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.control.core.schema.basic_credentials import BasicCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
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
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseDataError
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.credentials import CredentialType

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
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(Credential))
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(delete(OAuthToken))
        await session.execute(delete(TokenValueCredential))
        await session.execute(delete(BasicCredential))
        await session.execute(delete(OAuthClientCredential))
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(Credential))
        await session.commit()


@pytest.fixture()
def svc(integration_context: Context) -> CredentialService:
    """CredentialService wired to the integration context."""
    return CredentialService(integration_context)


def _api() -> APIReference:
    return APIReference(vendor="test-vendor", name="test-api", version="v1")


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
    assert updated.updated_at is not None


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
