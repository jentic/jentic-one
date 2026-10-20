"""Integration tests for the CredentialService — full CRUD lifecycle with real DB."""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import delete

from jentic_one.control.core.schema.basic_credentials import BasicCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.repos import CredentialRepository, OAuthClientCredentialRepository
from jentic_one.control.services.credentials.errors import (
    CredentialNotFoundError,
    ImmutableFieldError,
    InvalidCredentialInputError,
)
from jentic_one.control.services.credentials.providers.direct_oauth2 import DirectOAuth2Provider
from jentic_one.control.services.credentials.providers.oauth2 import TokenExchangeError
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
from jentic_one.control.services.credentials.schemas.provision import (
    APIReference,
    OAuthTokenView,
)
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    AppConfig,
    ControlSurfaceConfig,
    DirectOAuth2ProviderConfig,
    EgressConfig,
)
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
        await session.execute(delete(Sigv4Credential))
        await session.execute(delete(Credential))
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(delete(OAuthToken))
        await session.execute(delete(TokenValueCredential))
        await session.execute(delete(BasicCredential))
        await session.execute(delete(OAuthClientCredential))
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


# --- control-plane egress allowlist (issue #1704) ----------------------------
#
# An operator allowlists an in-network token endpoint; the OAuth2 credential
# create/update validation and the token round-trip (pre-flight check *and* the
# connect-time DNS pin) must honour it, under ``control.egress`` or — when that
# is unset — the inherited ``broker.egress``. DNS answers for the internal names
# and the token endpoint's HTTP response are the faked out-of-process seams.

_INTERNAL_HOST = "idp.corp.internal"
_INTERNAL_IP = "10.50.0.7"
_INTERNAL_TOKEN_URL = f"https://{_INTERNAL_HOST}/oauth/token"
_ALLOWLIST: dict[str, Any] = {
    "allowed_private_subnets": ["10.50.0.0/16"],
    "allowed_internal_domains": [".corp.internal"],
}
_STRICT: dict[str, Any] = {"allowed_private_subnets": [], "allowed_internal_domains": []}
# Module-level so the detect-secrets keyword heuristic doesn't flag the literal.
_ROTATED_SECRET = "rotated-fixture"  # pragma: allowlist secret


def _egress_config(
    base: AppConfig,
    *,
    broker: dict[str, Any] | None = None,
    control: dict[str, Any] | None = None,
) -> AppConfig:
    """``base`` with ``broker.egress`` / ``control.egress`` replaced as given."""
    update: dict[str, Any] = {}
    if broker is not None:
        update["broker"] = base.broker.model_copy(
            update={"egress": EgressConfig.model_validate(broker)}
        )
    if control is not None:
        update["control"] = ControlSurfaceConfig.model_validate({"egress": control})
    return base.model_copy(update=update)


@asynccontextmanager
async def _context(config: AppConfig) -> AsyncIterator[Context]:
    async with Context(config) as ctx:
        yield ctx


def _addrinfo(ip: str) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]


@contextmanager
def _internal_dns(*answers: str) -> Iterator[list[str]]:
    """Resolve ``_INTERNAL_HOST`` to *answers* in turn (the last one repeats).

    Yields the list of answers handed out so far. Every other name goes to the
    real resolver, so the DB connection is unaffected.
    """
    real = socket.getaddrinfo
    queue = list(answers)
    served: list[str] = []

    def _fake(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host == _INTERNAL_HOST:
            ip = queue.pop(0) if len(queue) > 1 else queue[0]
            served.append(ip)
            return _addrinfo(ip)
        return real(host, *args, **kwargs)

    with patch.object(socket, "getaddrinfo", side_effect=_fake):
        yield served


@contextmanager
def _token_endpoint() -> Iterator[list[httpx.Request]]:
    """Answer every outbound request with a token response; record what was sent.

    Only the socket-level transport is replaced — the DNS-pinning wrapper still
    resolves, validates, and pins each request before it gets here.
    """
    sent: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"access_token": "at-fresh", "expires_in": 3600})

    with patch.object(
        httpx, "AsyncHTTPTransport", side_effect=lambda *a, **k: httpx.MockTransport(_handler)
    ):
        yield sent


def _oauth2_create(token_url: str) -> CredentialCreate:
    return CredentialCreate(
        type=CredentialType.OAUTH2,
        name="In-network OAuth2",
        api=_api(),
        grant_type="client_credentials",
        token_url=token_url,
        client_id="client-123",
        client_secret="secret-456",
    )


async def _seed_oauth2_row(ctx: Context, *, token_url: str) -> str:
    """Write an OAuth2 credential row directly (no create-time URL validation)."""
    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type=CredentialType.OAUTH2,
            name="Seeded OAuth2",
            api_vendor="test-vendor",
            api_name="test-api",
            api_version="v1",
            provider="direct_oauth2",
            created_by=_ADMIN_IDENTITY.sub,
        )
        await OAuthClientCredentialRepository.create(
            session,
            credential_id=credential.id,
            token_url=token_url,
            client_id="client-123",
            encrypted_client_secret=ctx.encryption.encrypt("secret-456"),
            created_by=_ADMIN_IDENTITY.sub,
        )
        return credential.id


async def _refresh(ctx: Context, credential_id: str) -> Any:
    async def _decrypt() -> str:
        return "rt-current"

    token = OAuthTokenView(credential_id=credential_id, provider="direct_oauth2", decrypt=_decrypt)
    return await DirectOAuth2Provider(DirectOAuth2ProviderConfig()).refresh(ctx, token=token)


# (broker.egress, control.egress) pairs under which the internal token URL is allowed.
_ALLOWED = [
    pytest.param(_ALLOWLIST, None, id="inherited-from-broker"),
    pytest.param(None, _ALLOWLIST, id="control-override"),
]
_BLOCKED = "blocked address range"
_NO_DOMAIN = "matches no allowed internal domain suffix"
# ... and under which it is still refused, with the reason the refusal names:
# an address in an allowed subnet whose name matches no allowed suffix is the
# domain gap, anything else a blocked address.
_REFUSED = [
    pytest.param(None, None, _BLOCKED, id="no-allowlist"),
    pytest.param(_ALLOWLIST, _STRICT, _BLOCKED, id="control-override-stricter-than-broker"),
    pytest.param(
        None,
        {**_ALLOWLIST, "allowed_internal_domains": [".other.internal"]},
        _NO_DOMAIN,
        id="host-not-allowlisted",
    ),
    pytest.param(
        None,
        {**_ALLOWLIST, "allowed_private_subnets": ["10.60.0.0/16"]},
        _BLOCKED,
        id="subnet-not-allowlisted",
    ),
]


@pytest.mark.parametrize(("broker", "control"), _ALLOWED)
async def test_create_oauth2_accepts_allowlisted_internal_token_url(
    integration_config: AppConfig,
    clean_credentials: None,
    broker: dict[str, Any] | None,
    control: dict[str, Any] | None,
) -> None:
    config = _egress_config(integration_config, broker=broker, control=control)
    async with _context(config) as ctx:
        svc = CredentialService(ctx)
        with _internal_dns(_INTERNAL_IP):
            created = await svc.create(
                _oauth2_create(_INTERNAL_TOKEN_URL), identity=_ADMIN_IDENTITY
            )
        redacted = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    assert isinstance(redacted.details, OAuth2Redacted)
    assert redacted.details.token_url == _INTERNAL_TOKEN_URL


@pytest.mark.parametrize(("broker", "control", "reason"), _REFUSED)
async def test_create_oauth2_refuses_internal_token_url_outside_allowlist(
    integration_config: AppConfig,
    clean_credentials: None,
    broker: dict[str, Any] | None,
    control: dict[str, Any] | None,
    reason: str,
) -> None:
    config = _egress_config(integration_config, broker=broker, control=control)
    async with _context(config) as ctx:
        with (
            _internal_dns(_INTERNAL_IP),
            pytest.raises(InvalidCredentialInputError, match=reason),
        ):
            await CredentialService(ctx).create(
                _oauth2_create(_INTERNAL_TOKEN_URL), identity=_ADMIN_IDENTITY
            )


async def test_control_override_leaves_broker_egress_untouched(
    integration_config: AppConfig, clean_credentials: None
) -> None:
    """A stricter ``control.egress`` refuses the URL while ``broker.egress`` still allows it."""
    config = _egress_config(integration_config, broker=_ALLOWLIST, control=_STRICT)
    assert config.broker.egress.allowed_private_subnets == ["10.50.0.0/16"]
    async with _context(config) as ctx:
        with _internal_dns(_INTERNAL_IP), pytest.raises(InvalidCredentialInputError):
            await CredentialService(ctx).create(
                _oauth2_create(_INTERNAL_TOKEN_URL), identity=_ADMIN_IDENTITY
            )
    assert ctx.config.broker.egress.allowed_private_subnets == ["10.50.0.0/16"]
    assert ctx.config.broker.egress.allowed_internal_domains == [".corp.internal"]


@pytest.mark.parametrize(
    ("allowlisted", "error"),
    [(True, None), (False, InvalidCredentialInputError)],
    ids=["allowlisted", "no-allowlist"],
)
async def test_update_oauth2_token_url_honours_control_egress(
    integration_config: AppConfig,
    clean_credentials: None,
    allowlisted: bool,
    error: type[Exception] | None,
) -> None:
    config = _egress_config(integration_config, control=_ALLOWLIST if allowlisted else _STRICT)
    async with _context(config) as ctx:
        svc = CredentialService(ctx)
        created = await svc.create(
            _oauth2_create("https://93.184.216.34/oauth/token"), identity=_ADMIN_IDENTITY
        )
        update = CredentialUpdate(
            type=CredentialType.OAUTH2, client_secret=_ROTATED_SECRET, token_url=_INTERNAL_TOKEN_URL
        )
        with _internal_dns(_INTERNAL_IP):
            if error is None:
                await svc.update(created.credential_id, update, identity=_ADMIN_IDENTITY)
                redacted = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
                assert isinstance(redacted.details, OAuth2Redacted)
                assert redacted.details.token_url == _INTERNAL_TOKEN_URL
            else:
                with pytest.raises(error, match="blocked address range"):
                    await svc.update(created.credential_id, update, identity=_ADMIN_IDENTITY)


@pytest.mark.parametrize(("broker", "control"), _ALLOWED)
async def test_refresh_posts_to_allowlisted_internal_token_url(
    integration_config: AppConfig,
    clean_credentials: None,
    broker: dict[str, Any] | None,
    control: dict[str, Any] | None,
) -> None:
    config = _egress_config(integration_config, broker=broker, control=control)
    async with _context(config) as ctx:
        credential_id = await _seed_oauth2_row(ctx, token_url=_INTERNAL_TOKEN_URL)
        with _internal_dns(_INTERNAL_IP), _token_endpoint() as sent:
            result = await _refresh(ctx, credential_id)
    assert result.access_token == "at-fresh"
    # The connection is pinned to the IP the allowlist approved, with the
    # original name kept for the Host header and TLS SNI.
    assert len(sent) == 1
    assert sent[0].url.host == _INTERNAL_IP
    assert sent[0].headers["host"] == _INTERNAL_HOST
    assert sent[0].extensions["sni_hostname"] == _INTERNAL_HOST


@pytest.mark.parametrize(("broker", "control", "reason"), _REFUSED)
async def test_refresh_refuses_internal_token_url_outside_allowlist(
    integration_config: AppConfig,
    clean_credentials: None,
    broker: dict[str, Any] | None,
    control: dict[str, Any] | None,
    reason: str,
) -> None:
    config = _egress_config(integration_config, broker=broker, control=control)
    async with _context(config) as ctx:
        credential_id = await _seed_oauth2_row(ctx, token_url=_INTERNAL_TOKEN_URL)
        with (
            _internal_dns(_INTERNAL_IP),
            _token_endpoint() as sent,
            pytest.raises(TokenExchangeError) as exc_info,
        ):
            await _refresh(ctx, credential_id)
    assert exc_info.value.status == 0
    assert "unsafe upstream URL" in exc_info.value.body
    assert reason in exc_info.value.body
    assert sent == []


@pytest.mark.usefixtures("uncached_dns")
async def test_refresh_refuses_rebind_outside_allowlist(
    integration_config: AppConfig, clean_credentials: None
) -> None:
    """The connect-time pin re-validates against the same allowlist.

    Pre-flight sees an allowlisted address; the connection-time resolution
    answers with a private address outside ``allowed_private_subnets``.
    """
    config = _egress_config(integration_config, control=_ALLOWLIST)
    async with _context(config) as ctx:
        credential_id = await _seed_oauth2_row(ctx, token_url=_INTERNAL_TOKEN_URL)
        with (
            _internal_dns(_INTERNAL_IP, "10.60.0.9") as served,
            _token_endpoint() as sent,
            pytest.raises(TokenExchangeError) as exc_info,
        ):
            await _refresh(ctx, credential_id)
    # Pre-flight accepted the first answer; the pin refused the second.
    assert served == [_INTERNAL_IP, "10.60.0.9"]
    assert exc_info.value.status == 0
    assert "blocked address range" in exc_info.value.body
    assert sent == []


@contextmanager
def _lookup_threads() -> Iterator[list[int]]:
    """Record the thread every ``_INTERNAL_HOST`` lookup runs on (answer: ``_INTERNAL_IP``)."""
    real = socket.getaddrinfo
    threads: list[int] = []

    def _fake(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host == _INTERNAL_HOST:
            threads.append(threading.get_ident())
            return _addrinfo(_INTERNAL_IP)
        return real(host, *args, **kwargs)

    with patch.object(socket, "getaddrinfo", side_effect=_fake):
        yield threads


@pytest.mark.parametrize(
    ("control", "allowed"),
    [pytest.param(_ALLOWLIST, True, id="allowlisted"), pytest.param(_STRICT, False, id="strict")],
)
async def test_create_oauth2_applies_control_egress_off_the_event_loop(
    integration_config: AppConfig, clean_credentials: None, control: dict[str, Any], allowed: bool
) -> None:
    """Create-time ``token_url`` validation resolves off the loop under ``control.egress``."""
    config = _egress_config(integration_config, control=control)
    loop_thread = threading.get_ident()
    async with _context(config) as ctx:
        svc = CredentialService(ctx)
        with _lookup_threads() as threads:
            if allowed:
                await svc.create(_oauth2_create(_INTERNAL_TOKEN_URL), identity=_ADMIN_IDENTITY)
            else:
                with pytest.raises(InvalidCredentialInputError, match="blocked address range"):
                    await svc.create(_oauth2_create(_INTERNAL_TOKEN_URL), identity=_ADMIN_IDENTITY)
    assert threads
    assert loop_thread not in threads


@pytest.mark.parametrize(
    ("control", "allowed"),
    [pytest.param(_ALLOWLIST, True, id="allowlisted"), pytest.param(_STRICT, False, id="strict")],
)
async def test_refresh_applies_control_egress_off_the_event_loop(
    integration_config: AppConfig, clean_credentials: None, control: dict[str, Any], allowed: bool
) -> None:
    """Refresh-time validation and pin resolve off the loop under ``control.egress``."""
    config = _egress_config(integration_config, control=control)
    loop_thread = threading.get_ident()
    async with _context(config) as ctx:
        credential_id = await _seed_oauth2_row(ctx, token_url=_INTERNAL_TOKEN_URL)
        with _lookup_threads() as threads, _token_endpoint() as sent:
            if allowed:
                result = await _refresh(ctx, credential_id)
                assert result.access_token == "at-fresh"
            else:
                with pytest.raises(TokenExchangeError) as exc_info:
                    await _refresh(ctx, credential_id)
                assert "blocked address range" in exc_info.value.body
    assert len(sent) == (1 if allowed else 0)
    assert threads
    assert loop_thread not in threads


def _track_open_transactions(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the ``ctx.control_db.transaction()`` blocks open at each moment.

    Wraps the real method (the transaction still runs against the database);
    the returned one-item list holds the live count.
    """
    real = ctx.control_db.transaction
    open_count = [0]

    @asynccontextmanager
    async def _tracked(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async with real(*args, **kwargs) as session:
            open_count[0] += 1
            try:
                yield session
            finally:
                open_count[0] -= 1

    monkeypatch.setattr(ctx.control_db, "transaction", _tracked)
    return open_count


@contextmanager
def _lookups_seeing(open_count: list[int]) -> Iterator[list[tuple[int, int]]]:
    """Record ``(thread, open transactions)`` at every ``_INTERNAL_HOST`` lookup."""
    real = socket.getaddrinfo
    seen: list[tuple[int, int]] = []

    def _fake(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host == _INTERNAL_HOST:
            seen.append((threading.get_ident(), open_count[0]))
            return _addrinfo(_INTERNAL_IP)
        return real(host, *args, **kwargs)

    with patch.object(socket, "getaddrinfo", side_effect=_fake):
        yield seen


@pytest.mark.usefixtures("uncached_dns")
async def test_create_oauth2_resolves_urls_outside_the_write_transaction(
    integration_config: AppConfig, clean_credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``token_url`` / ``authorize_url`` resolve before ``transaction()`` opens.

    On SQLite the transaction holds the process-wide writer lock, so a slow
    resolver inside it would fail concurrent writers on ``busy_timeout``.
    Uncached so both URLs (same host) reach the resolver and are observed.
    """
    config = _egress_config(integration_config, control=_ALLOWLIST)
    loop_thread = threading.get_ident()
    payload = _oauth2_create(_INTERNAL_TOKEN_URL).model_copy(
        update={"authorize_url": f"https://{_INTERNAL_HOST}/oauth/authorize"}
    )
    async with _context(config) as ctx:
        open_count = _track_open_transactions(ctx, monkeypatch)
        with _lookups_seeing(open_count) as seen:
            await CredentialService(ctx).create(payload, identity=_ADMIN_IDENTITY)
    assert len(seen) == 2
    assert all(thread != loop_thread and opened == 0 for thread, opened in seen)


@pytest.mark.parametrize(
    ("control", "allowed"),
    [pytest.param(_ALLOWLIST, True, id="allowlisted"), pytest.param(_STRICT, False, id="strict")],
)
async def test_update_oauth2_resolves_token_url_off_the_loop_outside_the_transaction(
    integration_config: AppConfig,
    clean_credentials: None,
    monkeypatch: pytest.MonkeyPatch,
    control: dict[str, Any],
    allowed: bool,
) -> None:
    """Update-time ``token_url`` validation: ``control.egress``, off the loop, pre-transaction."""
    config = _egress_config(integration_config, control=control)
    loop_thread = threading.get_ident()
    update = CredentialUpdate(
        type=CredentialType.OAUTH2, client_secret=_ROTATED_SECRET, token_url=_INTERNAL_TOKEN_URL
    )
    async with _context(config) as ctx:
        svc = CredentialService(ctx)
        created = await svc.create(
            _oauth2_create("https://93.184.216.34/oauth/token"), identity=_ADMIN_IDENTITY
        )
        open_count = _track_open_transactions(ctx, monkeypatch)
        with _lookups_seeing(open_count) as seen:
            if allowed:
                await svc.update(created.credential_id, update, identity=_ADMIN_IDENTITY)
            else:
                with pytest.raises(InvalidCredentialInputError, match="blocked address range"):
                    await svc.update(created.credential_id, update, identity=_ADMIN_IDENTITY)
        redacted = await svc.get(created.credential_id, identity=_ADMIN_IDENTITY)
    assert len(seen) == 1
    assert seen[0][0] != loop_thread
    assert seen[0][1] == 0
    assert isinstance(redacted.details, OAuth2Redacted)
    expected = _INTERNAL_TOKEN_URL if allowed else "https://93.184.216.34/oauth/token"
    assert redacted.details.token_url == expected


@pytest.mark.parametrize("target", ["missing", "bearer"])
async def test_update_oauth2_reports_not_found_or_type_before_token_url(
    integration_config: AppConfig, clean_credentials: None, target: str
) -> None:
    """A missing or non-OAuth2 credential fails as such, before ``token_url`` resolves.

    The ``strict`` case above pins the other order: on an existing OAuth2
    credential the blocked ``token_url`` is what fails.
    """
    config = _egress_config(integration_config, control=_STRICT)
    update = CredentialUpdate(
        type=CredentialType.OAUTH2, client_secret=_ROTATED_SECRET, token_url=_INTERNAL_TOKEN_URL
    )
    async with _context(config) as ctx:
        svc = CredentialService(ctx)
        credential_id = "cred_does_not_exist"
        expected: type[Exception] = CredentialNotFoundError
        if target == "bearer":
            created = await svc.create(
                CredentialCreate(
                    type=CredentialType.BEARER_TOKEN, name="Bearer", api=_api(), token="tok"
                ),
                identity=_ADMIN_IDENTITY,
            )
            credential_id = created.credential_id
            expected = ImmutableFieldError
        with _lookups_seeing([0]) as seen, pytest.raises(expected):
            await svc.update(credential_id, update, identity=_ADMIN_IDENTITY)
    assert seen == []
