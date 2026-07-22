"""Integration tests for the broker ``CredentialService`` (§02b).

Seeds real credentials in the control DB and exercises the full resolve →
decrypt → inject path through ``CredentialService.inject`` against a connected
``Context`` (real encryption, no DB mocking). Also asserts the credential-error
mapping: missing → 424 (``prompt_human`` directive + ``provisioning_url`` +
``intent_id``), ambiguous → 409.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.broker.core.exceptions import (
    AmbiguousMatchError,
    CredentialNotProvisionedError,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_VENDOR = "stripe"
_API_NAME = "payments"
_API_VERSION = "v1"

_IDENTITY = Identity(
    sub="agent_42",
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)


@pytest.fixture()
async def clean_credentials(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(OAuthToken))
            await session.execute(delete(CustomerAPIKey))
            await session.execute(delete(Credential))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api_key(
    ctx: Context, *, cred_id: str, location: str, field_name: str, secret: str
) -> None:
    encrypted = ctx.encryption.encrypt(secret)
    async with ctx.control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.API_KEY,
                name=f"cred-{cred_id}",
                api_vendor=_VENDOR,
                api_name=_API_NAME,
                api_version=_API_VERSION,
            )
        )
        session.add(
            CustomerAPIKey(
                id=f"key-{cred_id}",
                credential_id=cred_id,
                encrypted_key=encrypted,
                location=location,
                field_name=field_name,
            )
        )
        await session.commit()


async def test_inject_api_key_header_end_to_end(
    integration_context: Context, clean_credentials: None
) -> None:
    """A header API key resolves + decrypts into ``InjectedAuth.headers``."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_hdr",
        location="header",
        field_name="X-Api-Key",
        secret="sk-live-123",  # pragma: allowlist secret
    )

    result = await CredentialService(integration_context).inject(
        api_vendor=_VENDOR, api_name=_API_NAME, api_version=_API_VERSION, identity=_IDENTITY
    )

    assert result.headers == {"X-Api-Key": "sk-live-123"}
    assert result.query_params == {}
    assert result.cookies == {}


async def test_inject_api_key_cookie_end_to_end(
    integration_context: Context, clean_credentials: None
) -> None:
    """A cookie API key lands in ``InjectedAuth.cookies`` (not headers)."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_cookie",
        location="cookie",
        field_name="session",
        secret="cookie-secret",  # pragma: allowlist secret
    )

    result = await CredentialService(integration_context).inject(
        api_vendor=_VENDOR, api_name=_API_NAME, api_version=_API_VERSION, identity=_IDENTITY
    )

    assert result.cookies == {"session": "cookie-secret"}
    assert result.headers == {}
    assert result.query_params == {}


async def test_missing_credential_maps_to_424_with_provisioning_url(
    integration_context: Context, clean_credentials: None
) -> None:
    """No provisioned credential → 424 with a ``prompt_human`` directive + URL + intent id."""
    base = integration_context.config.broker.account_linking_base_url
    integration_context.config.broker.account_linking_base_url = "https://app.example.com"
    try:
        with pytest.raises(CredentialNotProvisionedError) as exc:
            await CredentialService(integration_context).inject(
                api_vendor=_VENDOR,
                api_name=_API_NAME,
                api_version=_API_VERSION,
                identity=_IDENTITY,
            )
    finally:
        integration_context.config.broker.account_linking_base_url = base

    err = exc.value
    assert err.type == "credential_not_provisioned"
    assert err.directive is not None
    assert err.directive.strategy == "prompt_human"
    intent_id = err.directive.parameters["intent_id"]
    assert err.extra["intent_id"] == intent_id
    assert err.directive.parameters["provisioning_url"] == (
        f"https://app.example.com/connect/{_VENDOR}?actor=agent_42&intent={intent_id}"
    )


async def test_ambiguous_credential_maps_to_409(
    integration_context: Context, clean_credentials: None
) -> None:
    """Two active credentials for the same API tuple → 409 ambiguous."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        location="header",
        field_name="X-Api-Key",
        secret="a",
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        location="header",
        field_name="X-Api-Key",
        secret="b",
    )

    with pytest.raises(AmbiguousMatchError) as exc:
        await CredentialService(integration_context).inject(
            api_vendor=_VENDOR, api_name=_API_NAME, api_version=_API_VERSION, identity=_IDENTITY
        )
    assert exc.value.type == "ambiguous_credential"
    candidates = exc.value.extra["candidates"]
    assert {c["id"] for c in candidates} == {"cred_a", "cred_b"}
    assert {c["last4"] for c in candidates} == {"ed_a", "ed_b"}
    assert all("name" in c and "created_at" in c for c in candidates)


async def test_resolve_oauth2_credential_eager_loads_token(
    integration_context: Context, clean_credentials: None
) -> None:
    """OAuth2 credential resolution eagerly loads oauth_token (no MissingGreenlet).

    Regression test for #549: Credential.oauth_token previously used the default
    lazy='select' strategy which raises MissingGreenlet under AsyncSession.
    """
    encrypted_access = integration_context.encryption.encrypt("access-tok-123")
    encrypted_refresh = integration_context.encryption.encrypt("refresh-tok-456")
    expires = datetime.now(UTC) + timedelta(hours=1)

    async with integration_context.control_db.session() as session:
        session.add(
            Credential(
                id="cred_oauth",
                type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
                name="oauth-test",
                api_vendor=_VENDOR,
                api_name=_API_NAME,
                api_version=_API_VERSION,
                provider="static",
            )
        )
        session.add(
            OAuthToken(
                id="oat_test",
                credential_id="cred_oauth",
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                expires_at=expires,
            )
        )
        await session.commit()

    api = APIReference(vendor=_VENDOR, name=_API_NAME, version=_API_VERSION)
    resolved = await CredentialResolver(integration_context).resolve(api=api, caller=_IDENTITY.sub)

    assert resolved.encrypted_access_token == encrypted_access
    assert resolved.encrypted_refresh_token == encrypted_refresh
    assert resolved.token_expires_at == expires
