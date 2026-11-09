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

from jentic_one.broker.core.errors import (
    CredentialNotProvisionedError,
    CredentialServerVariableMismatchError,
)
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
    """Only a ``region=us`` credential exists: an ``/eu/`` request gets none.

    The scope mismatch raises the narrower subclass; catching the base still
    works, which is what keeps every existing 424 caller valid.
    """
    await _seed_api_key(
        integration_context,
        cred_id="cred_us",
        secret="us-secret",  # pragma: allowlist secret
        server_variables={"region": "us"},
    )

    with pytest.raises(CredentialServerVariableMismatchError):
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

    # Fail-closed, but reported as its own cause: the credential exists and the
    # URL simply does not say which values it targets.
    with pytest.raises(CredentialServerVariableMismatchError) as fail_closed:
        await _inject(integration_context, None, unresolved=True)
    assert fail_closed.value.type == "credential_server_variables_unresolved"

    await _seed_api_key(
        integration_context,
        cred_id="cred_any",
        secret="any-secret",  # pragma: allowlist secret
        server_variables=None,
    )

    assert await _inject(integration_context, None, unresolved=True) == "any-secret"


async def test_scoped_mismatch_is_distinguishable_from_an_absent_credential(
    integration_context: Context, clean_credentials: None
) -> None:
    """A region mismatch and a bare API are different 424s (#2089).

    Dropping a credential on ``server_variables`` and never having one are
    different problems with different fixes, so each gets its own problem
    ``type``. The mismatch body names the requested values, the variables that
    disagree and the candidates' own scopes, and its directive must not send
    the agent to connect a credential that already exists.
    """
    await _seed_api_key(
        integration_context,
        cred_id="cred_eu",
        secret="eu-secret",  # pragma: allowlist secret
        server_variables={"region": "eu-west-1"},
    )

    with pytest.raises(CredentialServerVariableMismatchError) as mismatch:
        await _inject(integration_context, {"region": "us-east-1"})

    async with integration_context.control_db.session() as session:
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(Credential))
        await session.commit()

    with pytest.raises(CredentialNotProvisionedError) as absent:
        await _inject(integration_context, {"region": "us-east-1"})

    # Two causes, two wire identities — and the absent case keeps the old one.
    assert mismatch.value.type == "credential_server_variable_mismatch"
    assert absent.value.type == "credential_not_provisioned"
    assert mismatch.value.detail != absent.value.detail

    # The requested value is named; the detail is the one-line summary a denied
    # queued execution persists, so it carries no candidate scopes.
    assert "us-east-1" in mismatch.value.detail
    assert "eu-west-1" not in mismatch.value.detail

    # The candidates — the caller's own bound credentials — carry the scope that
    # caused the refusal, so an operator can see it without guessing.
    assert mismatch.value.extra["server_variables"] == {"region": "us-east-1"}
    assert mismatch.value.extra["conflicting_variables"] == ["region"]
    candidates = mismatch.value.extra["candidates"]
    assert [c["id"] for c in candidates] == ["cred_eu"]
    assert candidates[0]["server_variables"] == {"region": "eu-west-1"}

    # The directive must not propose connecting what is already connected.
    assert mismatch.value.directive is not None
    directive = str(mismatch.value.directive)
    assert "jentic connect" not in directive
    assert "eu-west-1" in directive
    assert mismatch.value.directive.strategy == "prompt_human"

    # The absent case still gets the connect-flow directive — that one can help.
    assert absent.value.directive is not None
    assert "jentic connect" in str(absent.value.directive)
