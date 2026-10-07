"""Integration tests for the direct-binding injection boundary (theme-5 Q-02).

Exercises ``CredentialResolver.resolve`` / ``CredentialService.select`` /
``CredentialService.inject`` with ``allowed_credential_ids`` and the
``Jentic-Credential-Id`` tie-breaker against real control-DB rows. The
headline invariant: **an unbound-but-covering credential is never selected or
injected** when the binding boundary is active.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.broker.core.exceptions import (
    AmbiguousMatchError,
    CredentialNotProvisionedError,
    InvalidCredentialNameError,
)
from jentic_one.broker.services.credentials.errors import (
    CredentialIdNotFoundError,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_VENDOR = "stripe"
_API_NAME = "payments"
_API_VERSION = "v1"
_API = APIReference(vendor=_VENDOR, name=_API_NAME, version=_API_VERSION)

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
            await session.execute(delete(CustomerAPIKey))
            await session.execute(delete(Credential))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api_key(ctx: Context, *, cred_id: str, name: str, secret: str) -> None:
    encrypted = ctx.encryption.encrypt(secret)
    async with ctx.control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.API_KEY,
                name=name,
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
                location="header",
                field_name="X-Api-Key",
            )
        )
        await session.commit()


async def test_unbound_but_covering_credential_never_injected(
    integration_context: Context, clean_credentials: None
) -> None:
    """Q-02: a covering credential outside the allowed set must not resolve.

    ``cred_other`` covers the API and would resolve on the legacy unfiltered
    path — but the caller's binding set only contains ``cred_bound``, so the
    boundary must never let ``cred_other`` through, and an empty allowed set
    must deny everything (not act as a wildcard).
    """
    await _seed_api_key(
        integration_context,
        cred_id="cred_bound",
        name="bound",
        secret="sk-bound",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_other",
        name="other",
        secret="sk-other",  # pragma: allowlist secret
    )

    service = CredentialService(integration_context)

    # Only the bound credential resolves — never the covering sibling.
    injected = await service.inject(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        allowed_credential_ids=["cred_bound"],
    )
    assert injected.credential_id == "cred_bound"
    assert injected.headers == {"X-Api-Key": "sk-bound"}

    # An empty allowed set is a real deny-all filter, not a wildcard.
    with pytest.raises(CredentialNotProvisionedError):
        await service.inject(
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            identity=_IDENTITY,
            allowed_credential_ids=[],
        )


async def test_two_bound_credentials_ambiguity_names_binding_type(
    integration_context: Context, clean_credentials: None
) -> None:
    """Same-specificity tie within the allowed set → ambiguous_credential_binding 409."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="account-b",
        secret="sk-b",  # pragma: allowlist secret
    )

    with pytest.raises(AmbiguousMatchError) as exc:
        await CredentialService(integration_context).select(
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            identity=_IDENTITY,
            allowed_credential_ids=["cred_a", "cred_b"],
        )
    assert exc.value.type == "ambiguous_credential_binding"
    candidate_ids = {c["id"] for c in exc.value.extra["candidates"]}
    assert candidate_ids == {"cred_a", "cred_b"}
    assert exc.value.directive is not None
    assert exc.value.directive.parameters["headers"] == {"Jentic-Credential-Id": "cred_a"}


async def test_credential_id_header_breaks_tie(
    integration_context: Context, clean_credentials: None
) -> None:
    """Jentic-Credential-Id is the authoritative tie-breaker within the allowed set."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="account-b",
        secret="sk-b",  # pragma: allowlist secret
    )

    selected = await CredentialService(integration_context).select(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        credential_id="cred_b",
        allowed_credential_ids=["cred_a", "cred_b"],
    )
    assert selected is not None
    assert selected.credential_id == "cred_b"


async def test_credential_name_header_breaks_tie(
    integration_context: Context, clean_credentials: None
) -> None:
    """Jentic-Credential-Name still disambiguates on the direct path."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="account-b",
        secret="sk-b",  # pragma: allowlist secret
    )

    selected = await CredentialService(integration_context).select(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        credential_name="account-a",
        allowed_credential_ids=["cred_a", "cred_b"],
    )
    assert selected is not None
    assert selected.credential_id == "cred_a"


async def test_unknown_credential_id_maps_to_domain_error_with_candidates(
    integration_context: Context, clean_credentials: None
) -> None:
    """An id outside the covering candidates → credential_id_not_found + candidates."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )

    with pytest.raises(InvalidCredentialNameError) as exc:
        await CredentialService(integration_context).select(
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            identity=_IDENTITY,
            credential_id="cred_missing",
            allowed_credential_ids=["cred_a"],
        )
    assert exc.value.type == "credential_id_not_found"
    assert [c["id"] for c in exc.value.extra["candidates"]] == ["cred_a"]


async def test_resolver_raw_id_filter(
    integration_context: Context, clean_credentials: None
) -> None:
    """Resolver-level: the id filter is exact and raises with candidates on a miss."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    resolver = CredentialResolver(integration_context)

    resolved = await resolver.resolve(api=_API, caller="agent_42", credential_id="cred_a")
    assert resolved.credential_id == "cred_a"

    with pytest.raises(CredentialIdNotFoundError):
        await resolver.resolve(api=_API, caller="agent_42", credential_id="cred_nope")


async def test_inject_with_preresolved_skips_reresolution(
    integration_context: Context, clean_credentials: None
) -> None:
    """``preresolved`` injects the selected credential without a second resolve."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    service = CredentialService(integration_context)
    selected = await service.select(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        allowed_credential_ids=["cred_a"],
    )
    assert selected is not None

    injected = await service.inject(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        preresolved=selected,
    )
    assert injected.credential_id == "cred_a"
    assert injected.headers == {"X-Api-Key": "sk-a"}
