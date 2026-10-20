"""Shared fixtures for registry service integration tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import delete

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.notes import Note
from jentic_one.shared.config import AppConfig, CatalogConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from tests.integration.registry.services.catalog_sources_support import (
    CtxFactory,
    wipe_catalog_sources,
)

_NOTE_API_VENDOR = "note-test.com"


@pytest.fixture()
async def clean_notes(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Empty the notes table (and the note test API) before and after each test."""

    async def _wipe() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(Note))
            await session.execute(delete(Api).where(Api.vendor == _NOTE_API_VENDOR))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


@pytest.fixture()
async def note_api(registry_db: DatabaseSession, clean_notes: None) -> Api:
    """A registry API that notes can attach to."""
    api = Api(vendor=_NOTE_API_VENDOR, name="note-api", version="v1")
    async with registry_db.session() as session:
        session.add(api)
        await session.commit()
    return api


@pytest.fixture()
async def clean_sources(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    await wipe_catalog_sources(registry_db)
    yield
    await wipe_catalog_sources(registry_db)


@pytest.fixture()
def catalog_ctx(integration_config: AppConfig, clean_sources: None) -> CtxFactory:
    """``catalog_ctx(**catalog)``: a ``Context`` whose ``catalog`` block is ``catalog``."""

    @asynccontextmanager
    async def _make(**catalog: Any) -> AsyncIterator[Context]:
        cfg = integration_config.model_copy(
            update={"catalog": CatalogConfig.model_validate(catalog)}
        )
        async with Context(cfg) as ctx:
            yield ctx

    return _make
