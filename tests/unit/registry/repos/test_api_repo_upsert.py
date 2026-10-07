"""``ApiRepository.upsert`` semantics for the persisted catalog identity (#910).

The catalog slug (`domain[/sub-api]`) is display provenance, not resolution
identity — these tests pin the update rules that keep it trustworthy:

- a catalog import records it on create,
- a catalog re-import refreshes (or backfills) it,
- a manual re-import of the same triple (``None``) never clears one already
  recorded (e.g. overlay materialization re-ingests inline without the slug).

Run against a real in-memory SQLite registry DB (no DB mocking —
``tests/arch/test_no_db_mocking.py``); the session fixture lives in the shared
``tests/unit/registry/conftest.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.repos.api_repo import ApiRepository, CatalogApiIdConflictError

_TRIPLE = {"vendor": "nytimes.com", "name": "nytimes-com-article-search", "version": "1.0.0"}


@pytest.mark.asyncio
async def test_catalog_import_records_slug_on_create(apis_sqlite_session: AsyncSession) -> None:
    api = await ApiRepository.upsert(
        apis_sqlite_session,
        **_TRIPLE,
        created_by="usr_test",
        catalog_api_id="nytimes.com/article_search",
    )
    assert api.catalog_api_id == "nytimes.com/article_search"


@pytest.mark.asyncio
async def test_manual_import_leaves_slug_null(apis_sqlite_session: AsyncSession) -> None:
    api = await ApiRepository.upsert(apis_sqlite_session, **_TRIPLE, created_by="usr_test")
    assert api.catalog_api_id is None


@pytest.mark.asyncio
async def test_catalog_reimport_backfills_existing_row(apis_sqlite_session: AsyncSession) -> None:
    """A pre-#910 row (or manual first import) picks up the slug on its next
    catalog import of the same triple."""
    first = await ApiRepository.upsert(apis_sqlite_session, **_TRIPLE, created_by="usr_test")
    assert first.catalog_api_id is None

    again = await ApiRepository.upsert(
        apis_sqlite_session,
        **_TRIPLE,
        created_by="usr_test",
        catalog_api_id="nytimes.com/article_search",
    )
    assert again.id == first.id
    assert again.catalog_api_id == "nytimes.com/article_search"


@pytest.mark.asyncio
async def test_slugless_reimport_never_clears_recorded_slug(
    apis_sqlite_session: AsyncSession,
) -> None:
    """Overlay materialization and manual re-imports pass ``None`` — that must
    not erase the identity a catalog import already recorded."""
    await ApiRepository.upsert(
        apis_sqlite_session,
        **_TRIPLE,
        created_by="usr_test",
        catalog_api_id="nytimes.com/article_search",
    )
    again = await ApiRepository.upsert(apis_sqlite_session, **_TRIPLE, created_by="usr_test")
    assert again.catalog_api_id == "nytimes.com/article_search"


@pytest.mark.asyncio
async def test_catalog_import_onto_a_foreign_catalog_row_is_refused(
    apis_sqlite_session: AsyncSession,
) -> None:
    """The guard lives in the (locked) upsert, so every caller gets it: an
    identity already imported from a different catalog entry is never
    re-attributed."""
    await ApiRepository.upsert(
        apis_sqlite_session,
        **_TRIPLE,
        created_by="usr_test",
        catalog_api_id="nytimes.com/article_search",
    )
    with pytest.raises(CatalogApiIdConflictError) as exc:
        await ApiRepository.upsert(
            apis_sqlite_session,
            **_TRIPLE,
            created_by="usr_test",
            catalog_api_id="api.nytimes.com/article_search",
        )
    assert exc.value.stored_id == "nytimes.com/article_search"
    stored = await ApiRepository.get_by_identifier(apis_sqlite_session, **_TRIPLE)
    assert stored is not None
    assert stored.catalog_api_id == "nytimes.com/article_search"


@pytest.mark.asyncio
async def test_identity_for_catalog_api_id(apis_sqlite_session: AsyncSession) -> None:
    await ApiRepository.upsert(
        apis_sqlite_session,
        **_TRIPLE,
        created_by="usr_test",
        catalog_api_id="nytimes.com/article_search",
    )
    assert await ApiRepository.identity_for_catalog_api_id(
        apis_sqlite_session, "nytimes.com/article_search"
    ) == ("nytimes.com", "nytimes-com-article-search")
    assert (
        await ApiRepository.identity_for_catalog_api_id(apis_sqlite_session, "nope.com/x") is None
    )
