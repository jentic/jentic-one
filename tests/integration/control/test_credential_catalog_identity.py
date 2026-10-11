"""A credential created from a catalog pick lands on the API imported from it.

A client picking a catalog entry derives the API name from the entry's id; the
importer names a ``domain/sub`` entry by its sub segment, and an API imported
by an older release keeps the name it was created with. When an imported API
carries the credential's ``catalog_api_id``, its registered name wins, so the
credential covers the API it was made for instead of failing every execute
with ``credential_identity_mismatch``.

Cross-DB by construction: the registry ``apis`` row is seeded through the
registry repo, the credential is created through the control service.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

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


async def _import(ctx: Context, *, vendor: str, name: str, catalog_api_id: str) -> None:
    async with ctx.registry_db.session() as session:
        await ApiRepository.upsert(
            session,
            vendor=vendor,
            name=name,
            version="1.1.4",
            created_by="usr_test",
            catalog_api_id=catalog_api_id,
        )
        await session.commit()


def _payload(api: APIReference, catalog_api_id: str | None) -> CredentialCreate:
    return CredentialCreate(
        type=CredentialType.BEARER_TOKEN,
        name="GitHub",
        api=api,
        catalog_api_id=catalog_api_id,
        token="ghp-test-token-value",  # pragma: allowlist secret
    )


async def test_catalog_pick_takes_the_imported_sub_segment_name(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    """The UI sends the whole-id slug; the importer registered the sub segment."""
    await _import(
        integration_context,
        vendor="github-com",
        name="api-github-com",
        catalog_api_id="github.com/api.github.com",
    )
    result = await svc.create(
        _payload(
            APIReference(vendor="github.com", name="github-com-api-github-com", version=""),
            "github.com/api.github.com",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert (result.api.vendor, result.api.name) == ("github-com", "api-github-com")
    # It covers the API it was made for, so the create carries no advisory.
    assert result.warnings is None


async def test_catalog_pick_takes_an_older_release_s_doubled_name(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    """An API imported before the sub-segment naming keeps its doubled name, and
    a credential made from the same catalog entry follows it."""
    await _import(
        integration_context,
        vendor="posthog-com",
        name="posthog-com-posthog-api",
        catalog_api_id="posthog.com/posthog-api",
    )
    result = await svc.create(
        _payload(
            APIReference(vendor="posthog.com", name="posthog-api", version=""),
            "posthog.com/posthog-api",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert result.api.name == "posthog-com-posthog-api"


async def test_vendor_wide_scope_and_other_vendor_are_left_as_given(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    await _import(
        integration_context,
        vendor="github-com",
        name="api-github-com",
        catalog_api_id="github.com/api.github.com",
    )
    vendor_wide = await svc.create(
        _payload(
            APIReference(vendor="github.com", name="", version=""), "github.com/api.github.com"
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert not vendor_wide.api.name
    other_vendor = await svc.create(
        _payload(
            APIReference(vendor="example.com", name="thing", version=""),
            "github.com/api.github.com",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert (other_vendor.api.vendor, other_vendor.api.name) == ("example-com", "thing")


async def test_not_yet_imported_entry_keeps_the_requested_name(
    integration_context: Context, svc: CredentialService, clean_tables: None
) -> None:
    result = await svc.create(
        _payload(
            APIReference(vendor="stripe.com", name="stripe-api", version=""),
            "stripe.com/stripe-api",
        ),
        identity=_ADMIN_IDENTITY,
    )
    assert result.api.name == "stripe-api"
