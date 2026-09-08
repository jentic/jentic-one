"""Shared fixtures for registry repository integration tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.registry.core.schema.notes import Note
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_registry(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Truncate all registry tables before and after each test."""

    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(Note))
            await session.execute(delete(CatalogUpdateCheck))
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(SecuritySchemeFlow))
            await session.execute(delete(SecurityScheme))
            await session.execute(delete(ServerVariable))
            await session.execute(delete(Server))
            await session.execute(delete(Operation))
            await session.execute(delete(SpecFile))
            await session.execute(delete(Overlay))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


@pytest.fixture()
async def sample_api(registry_db: DatabaseSession, clean_registry: None) -> Api:
    """Create a prerequisite Api for tests that need an api_id."""
    api = Api(vendor="test.com", name="sample-api", version="v1")
    async with registry_db.session() as session:
        session.add(api)
        await session.commit()
    return api


@pytest.fixture()
async def sample_revision(registry_db: DatabaseSession, sample_api: Api) -> tuple[Api, ApiRevision]:
    """Create a prerequisite Api + draft ApiRevision."""
    revision = ApiRevision(
        api_id=sample_api.id,
        state="draft",
        spec_digest="sha256:abc123",
        source_type="url",
    )
    async with registry_db.session() as session:
        session.add(revision)
        await session.commit()
    return sample_api, revision


@pytest.fixture()
def new_api_id() -> uuid.UUID:
    """Generate a fresh UUID for tests that create their own Api."""
    return uuid.uuid4()
