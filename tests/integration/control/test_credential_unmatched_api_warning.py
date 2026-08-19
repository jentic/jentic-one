"""Integration tests for the create-time unmatched-API advisory (#1020).

A credential whose canonical scope covers no imported registry API identity is
still created (importing the API later is a legitimate order of operations),
but the create response must carry an advisory warning — with a
nearest-identity hint when the vendor has other imported APIs (the #1020
vendor-doubled dead-end shape) — and a warning-severity
``credential.unmatched_api`` event must land in the admin events feed.

Cross-DB by construction: registry ``apis`` rows are seeded through the
registry repo/session, the credential is created through the control service,
and events are asserted in the admin DB.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.services.credentials.schemas.credentials import CredentialCreate
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.models.events import EventType

_ADMIN_IDENTITY = Identity(sub="admin_user", email="admin@test.com", permissions=["org:admin"])

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_tables(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with integration_context.control_db.session() as session:
            await session.execute(delete(TokenValueCredential))
            await session.execute(delete(Credential))
            await session.commit()
        async with integration_context.registry_db.session() as session:
            await session.execute(delete(Api))
            await session.commit()
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(Event))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


@pytest.fixture()
def svc(integration_context: Context) -> CredentialService:
    return CredentialService(integration_context)


async def _seed_api(ctx: Context, *, vendor: str, name: str, version: str) -> None:
    async with ctx.registry_db.session() as session:
        await ApiRepository.upsert(
            session, vendor=vendor, name=name, version=version, created_by="usr_test"
        )
        await session.commit()


def _payload(api: APIReference) -> CredentialCreate:
    return CredentialCreate(
        type=CredentialType.BEARER_TOKEN,
        name="test credential",
        api=api,
        token="sk-test-token-value",  # pragma: allowlist secret
    )


async def _unmatched_events(ctx: Context) -> list[Event]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(Event).where(Event.type == EventType.CREDENTIAL_UNMATCHED_API)
        )
        return list(result.scalars().all())


async def test_matching_scope_carries_no_warning(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    await _seed_api(integration_context, vendor="posthog-com", name="posthog-api", version="1.0")
    result = await svc.create(
        _payload(APIReference(vendor="posthog-com", name="posthog-api", version="1.0")),
        identity=_ADMIN_IDENTITY,
    )
    assert result.warnings is None
    assert await _unmatched_events(integration_context) == []


async def test_wildcard_scope_covering_an_api_carries_no_warning(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    await _seed_api(integration_context, vendor="posthog-com", name="posthog-api", version="1.0")
    result = await svc.create(
        _payload(APIReference(vendor="posthog-com", name="", version="")),
        identity=_ADMIN_IDENTITY,
    )
    assert result.warnings is None


async def test_unmatched_scope_warns_with_nearest_identity_hint(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    """The #1020 shape: the user binds the clean name while the workspace holds
    a different (e.g. vendor-doubled) identity — the hint names what exists."""
    await _seed_api(
        integration_context, vendor="posthog-com", name="posthog-com-posthog-api", version="1.0"
    )
    result = await svc.create(
        _payload(APIReference(vendor="posthog-com", name="posthog-api", version="")),
        identity=_ADMIN_IDENTITY,
    )
    assert result.warnings is not None
    [warning] = result.warnings
    assert "posthog-com/posthog-api" in warning
    assert "matches no imported API" in warning
    # Both remedies: import the API, or re-create the (immutable-scope) credential.
    assert "re-create" in warning
    assert "posthog-com/posthog-com-posthog-api (1.0)" in warning

    events = await _unmatched_events(integration_context)
    assert len(events) == 1
    assert events[0].severity == "warning"
    # Short, bounded summary; the remedy + sibling hint travel in detail.
    assert result.credential_id in events[0].summary
    assert "posthog-com/posthog-api" in events[0].summary
    assert "imported APIs for this vendor" not in events[0].summary
    assert events[0].detail == warning
    assert (events[0].data or {}).get("credential_id") == result.credential_id


async def test_version_only_wildcard_scope_covering_an_api_carries_no_warning(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    """A name-wildcard, version-pinned scope (vendor/*/1.0) that covers an
    imported API stays warning-free — the fourth wildcard combination."""
    await _seed_api(integration_context, vendor="posthog-com", name="posthog-api", version="1.0")
    result = await svc.create(
        _payload(APIReference(vendor="posthog-com", name="", version="1.0")),
        identity=_ADMIN_IDENTITY,
    )
    assert result.warnings is None


async def test_unmatched_vendor_warns_without_hint(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    result = await svc.create(
        _payload(APIReference(vendor="nowhere-example", name="", version="")),
        identity=_ADMIN_IDENTITY,
    )
    assert result.warnings is not None
    [warning] = result.warnings
    assert "nowhere-example" in warning
    assert "imported APIs for this vendor" not in warning
    # The create itself must have succeeded regardless of the warning.
    assert result.credential_id


async def test_version_scoped_mismatch_warns(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    await _seed_api(integration_context, vendor="posthog-com", name="posthog-api", version="1.0")
    result = await svc.create(
        _payload(APIReference(vendor="posthog-com", name="posthog-api", version="2.0")),
        identity=_ADMIN_IDENTITY,
    )
    assert result.warnings is not None
    [warning] = result.warnings
    assert "posthog-com/posthog-api/2.0" in warning
