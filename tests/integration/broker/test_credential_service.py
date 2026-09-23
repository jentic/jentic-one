"""Integration tests for the broker ``CredentialService`` (§02b).

Seeds real credentials in the control DB and exercises the full resolve →
decrypt → inject path through ``CredentialService.inject`` against a connected
``Context`` (real encryption, no DB mocking). Also asserts the credential-error
mapping: missing → 424 (``prompt_human`` directive + ``provisioning_url`` +
``intent_id``), ambiguous → 409 — and the toolkit injection boundary: only
credentials bound to the execution's toolkit are ever candidates.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.broker.core.exceptions import (
    AmbiguousMatchError,
    CredentialNotProvisionedError,
    InvalidCredentialNameError,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.protocols import InjectedAuth
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_VENDOR = "stripe"
_API_NAME = "payments"
_API_VERSION = "v1"
_TOOLKIT = "tk_mine"
_OTHER_TOOLKIT = "tk_other"

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
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(OAuthToken))
            await session.execute(delete(CustomerAPIKey))
            await session.execute(delete(Credential))
            await session.execute(delete(Toolkit).where(Toolkit.id.in_([_TOOLKIT, _OTHER_TOOLKIT])))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _bind(ctx: Context, *, toolkit_id: str, cred_id: str) -> None:
    """Bind a credential to a toolkit, creating the toolkit on first use."""
    async with ctx.control_db.session() as session:
        if await session.get(Toolkit, toolkit_id) is None:
            session.add(Toolkit(id=toolkit_id, name=f"toolkit-{toolkit_id}"))
            await session.flush()
        session.add(ToolkitCredentialBinding(toolkit_id=toolkit_id, credential_id=cred_id))
        await session.commit()


async def _seed_api_key(
    ctx: Context,
    *,
    cred_id: str,
    location: str,
    field_name: str,
    secret: str,
    toolkit_id: str | None = _TOOLKIT,
    active: bool = True,
) -> None:
    """Seed an API-key credential, bound to ``toolkit_id`` (``None`` → unbound)."""
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
                active=active,
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
    if toolkit_id is not None:
        await _bind(ctx, toolkit_id=toolkit_id, cred_id=cred_id)


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
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        toolkit_id=_TOOLKIT,
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
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        toolkit_id=_TOOLKIT,
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
                toolkit_id=_TOOLKIT,
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
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            identity=_IDENTITY,
            toolkit_id=_TOOLKIT,
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

    Regression test for #549: the default
    lazy='select' strategy would raise MissingGreenlet under AsyncSession.
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
    await _bind(integration_context, toolkit_id=_TOOLKIT, cred_id="cred_oauth")

    api = APIReference(vendor=_VENDOR, name=_API_NAME, version=_API_VERSION)
    resolved = await CredentialResolver(integration_context).resolve(
        api=api, caller=_IDENTITY.sub, toolkit_id=_TOOLKIT
    )

    assert resolved.encrypted_access_token == encrypted_access
    assert resolved.encrypted_refresh_token == encrypted_refresh
    assert resolved.token_expires_at == expires


# --- Toolkit injection boundary -----------------------------------------------
#
# Scenario shared by the tests below: "mine" is bound to the execution's
# toolkit; "theirs" covers the same API but is bound only to another toolkit
# (another user's, or another of the same owner's agents).


async def _seed_mine_and_theirs(ctx: Context, *, mine_active: bool = True) -> None:
    await _seed_api_key(
        ctx,
        cred_id="cred_mine",
        location="header",
        field_name="X-Api-Key",
        secret="SECRET-MINE",  # pragma: allowlist secret
        toolkit_id=_TOOLKIT,
        active=mine_active,
    )
    await _seed_api_key(
        ctx,
        cred_id="cred_theirs",
        location="header",
        field_name="X-Api-Key",
        secret="SECRET-THEIRS",  # pragma: allowlist secret
        toolkit_id=_OTHER_TOOLKIT,
    )


async def _inject_mine(ctx: Context, *, credential_name: str | None = None) -> InjectedAuth:
    return await CredentialService(ctx).inject(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        toolkit_id=_TOOLKIT,
        credential_name=credential_name,
    )


async def test_other_toolkits_credential_is_not_a_candidate(
    integration_context: Context, clean_credentials: None
) -> None:
    """Both active: the toolkit's own credential is injected — no 409, no leak."""
    await _seed_mine_and_theirs(integration_context)

    result = await _inject_mine(integration_context)

    assert result.headers == {"X-Api-Key": "SECRET-MINE"}
    assert result.credential_id == "cred_mine"


async def test_disabled_own_credential_never_falls_back_to_another_toolkit(
    integration_context: Context, clean_credentials: None
) -> None:
    """Own credential disabled → 424, never the other toolkit's active secret."""
    await _seed_mine_and_theirs(integration_context, mine_active=False)

    with pytest.raises(CredentialNotProvisionedError) as exc:
        await _inject_mine(integration_context)
    assert exc.value.type == "credential_not_provisioned"


async def test_credential_name_cannot_select_outside_the_toolkit(
    integration_context: Context, clean_credentials: None
) -> None:
    """Naming another toolkit's credential → 400; candidates list only the own one."""
    await _seed_mine_and_theirs(integration_context)

    with pytest.raises(InvalidCredentialNameError) as exc:
        await _inject_mine(integration_context, credential_name="cred-cred_theirs")
    assert exc.value.type == "credential_name_not_found"
    assert {c["id"] for c in exc.value.extra["candidates"]} == {"cred_mine"}


async def test_unbound_credential_is_not_a_candidate(
    integration_context: Context, clean_credentials: None
) -> None:
    """A covering credential bound to no toolkit is never injected."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_loose",
        location="header",
        field_name="X-Api-Key",
        secret="SECRET-LOOSE",  # pragma: allowlist secret
        toolkit_id=None,
    )

    with pytest.raises(CredentialNotProvisionedError):
        await _inject_mine(integration_context)


async def test_ambiguity_candidates_are_scoped_to_the_toolkit(
    integration_context: Context, clean_credentials: None
) -> None:
    """A 409 inside the toolkit lists only its own credentials, never another's."""
    await _seed_mine_and_theirs(integration_context)
    await _seed_api_key(
        integration_context,
        cred_id="cred_mine2",
        location="header",
        field_name="X-Api-Key",
        secret="SECRET-MINE-2",  # pragma: allowlist secret
        toolkit_id=_TOOLKIT,
    )

    with pytest.raises(AmbiguousMatchError) as exc:
        await _inject_mine(integration_context)
    assert {c["id"] for c in exc.value.extra["candidates"]} == {"cred_mine", "cred_mine2"}
