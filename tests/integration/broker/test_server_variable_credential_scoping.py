"""Integration tests: credentials are matched on the request's server variables.

Seeds real credentials carrying ``server_variables`` in the control DB and runs
``CredentialService.inject`` (real encryption, no DB mocking) with the
server-variable values discovery resolved from the request URL. A credential
scoped to ``region=us`` covers ``/us/…`` requests only; a request for
``/eu/…`` must not receive it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.broker.core.exceptions import CredentialNotProvisionedError
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType

pytestmark = pytest.mark.integration

_VENDOR = "widgets-example-com"
_API_NAME = "widgets"
_API_VERSION = "1.0.0"

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
    ctx: Context, *, cred_id: str, secret: str, server_variables: dict[str, str] | None
) -> None:
    async with ctx.control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.API_KEY,
                name=f"cred-{cred_id}",
                api_vendor=_VENDOR,
                api_name=_API_NAME,
                api_version=_API_VERSION,
                server_variables=server_variables,
            )
        )
        session.add(
            CustomerAPIKey(
                id=f"key-{cred_id}",
                credential_id=cred_id,
                encrypted_key=ctx.encryption.encrypt(secret),
                location="header",
                field_name="X-Api-Key",
            )
        )
        await session.commit()


async def _inject(
    ctx: Context,
    request_server_variables: dict[str, str] | None,
    *,
    unresolved: bool = False,
) -> str:
    result = await CredentialService(ctx).inject(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        request_server_variables=request_server_variables,
        server_variables_unresolved=unresolved,
    )
    return result.headers["X-Api-Key"]


async def test_region_scoped_credential_not_injected_for_other_region(
    integration_context: Context, clean_credentials: None
) -> None:
    """Only a ``region=us`` credential exists: an ``/eu/`` request gets none."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_us",
        secret="us-secret",  # pragma: allowlist secret
        server_variables={"region": "us"},
    )

    with pytest.raises(CredentialNotProvisionedError):
        await _inject(integration_context, {"region": "eu"})


async def test_region_scoped_credential_injected_for_its_region(
    integration_context: Context, clean_credentials: None
) -> None:
    await _seed_api_key(
        integration_context,
        cred_id="cred_us",
        secret="us-secret",  # pragma: allowlist secret
        server_variables={"region": "us"},
    )

    assert await _inject(integration_context, {"region": "us"}) == "us-secret"


async def test_each_region_selects_its_own_credential(
    integration_context: Context, clean_credentials: None
) -> None:
    """Two region-scoped credentials for one API: no ambiguity, each region gets its own."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_us",
        secret="us-secret",  # pragma: allowlist secret
        server_variables={"region": "us"},
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_eu",
        secret="eu-secret",  # pragma: allowlist secret
        server_variables={"region": "eu"},
    )

    assert await _inject(integration_context, {"region": "us"}) == "us-secret"
    assert await _inject(integration_context, {"region": "eu"}) == "eu-secret"


async def test_unresolved_request_variable_keeps_scoped_credential_eligible(
    integration_context: Context, clean_credentials: None
) -> None:
    """A templated ``/{region}/`` request resolves no value: the credential supplies it."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_us",
        secret="us-secret",  # pragma: allowlist secret
        server_variables={"region": "us"},
    )

    assert await _inject(integration_context, {}) == "us-secret"
    assert await _inject(integration_context, None) == "us-secret"


async def test_unscoped_credential_matches_any_region(
    integration_context: Context, clean_credentials: None
) -> None:
    await _seed_api_key(
        integration_context,
        cred_id="cred_any",
        secret="any-secret",  # pragma: allowlist secret
        server_variables=None,
    )

    assert await _inject(integration_context, {"region": "eu"}) == "any-secret"


async def test_unresolvable_request_variables_fail_closed_for_scoped_credentials(
    integration_context: Context, clean_credentials: None
) -> None:
    """Discovery could not determine the URL's values: only unscoped credentials qualify."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_us",
        secret="us-secret",  # pragma: allowlist secret
        server_variables={"region": "us"},
    )

    with pytest.raises(CredentialNotProvisionedError):
        await _inject(integration_context, None, unresolved=True)

    await _seed_api_key(
        integration_context,
        cred_id="cred_any",
        secret="any-secret",  # pragma: allowlist secret
        server_variables=None,
    )

    assert await _inject(integration_context, None, unresolved=True) == "any-secret"
