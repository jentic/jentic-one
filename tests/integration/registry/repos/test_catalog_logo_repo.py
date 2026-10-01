"""Integration tests for CatalogLogoRepository against real PostgreSQL.

Verifies the ``catalog_logos`` model round-trips (KSUID id, bytes column, unique
``source_url``), that ``upsert`` replaces a row in place, and that ``mark`` records a
fetch outcome without touching the cached bytes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.registry.repos.catalog_logo_repo import CatalogLogoRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_URL = "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi/x/logo.png"
_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


async def test_get_missing_returns_none(registry_db: DatabaseSession, clean_registry: None) -> None:
    async with registry_db.session() as session:
        assert await CatalogLogoRepository.get(session, _URL) is None


async def test_upsert_inserts_then_replaces_in_place(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    now = datetime.now(UTC)
    async with registry_db.session() as session:
        await CatalogLogoRepository.upsert(
            session,
            source_url=_URL,
            status="ok",
            fetched_at=now,
            content=_PNG,
            content_type="image/png",
            digest="d1",
            upstream_etag='"v1"',
        )
        await session.commit()

    async with registry_db.session() as session:
        row = await CatalogLogoRepository.get(session, _URL)
        assert row is not None
        row_id = row.id
        assert row_id.startswith("clg_")
        assert (row.status, row.content, row.content_type, row.digest, row.upstream_etag) == (
            "ok",
            _PNG,
            "image/png",
            "d1",
            '"v1"',
        )
        assert row.created_at is not None

    async with registry_db.session() as session:
        await CatalogLogoRepository.upsert(
            session, source_url=_URL, status="unsupported", fetched_at=now + timedelta(seconds=1)
        )
        await session.commit()

    async with registry_db.session() as session:
        row = await CatalogLogoRepository.get(session, _URL)
        assert row is not None
        assert row.id == row_id  # same row: the unique source_url resolves the conflict
        assert (row.status, row.content, row.digest, row.upstream_etag) == (
            "unsupported",
            None,
            None,
            None,
        )


async def test_mark_keeps_cached_bytes(registry_db: DatabaseSession, clean_registry: None) -> None:
    then = datetime.now(UTC) - timedelta(days=8)
    later = datetime.now(UTC)
    async with registry_db.session() as session:
        await CatalogLogoRepository.upsert(
            session,
            source_url=_URL,
            status="ok",
            fetched_at=then,
            content=_PNG,
            content_type="image/png",
            digest="d1",
            upstream_etag='"v1"',
        )
        await CatalogLogoRepository.mark(session, source_url=_URL, status="error", fetched_at=later)
        await session.commit()

    async with registry_db.session() as session:
        row = await CatalogLogoRepository.get(session, _URL)
        assert row is not None
        assert (row.status, row.content, row.upstream_etag) == ("error", _PNG, '"v1"')
        assert row.fetched_at == later

    async with registry_db.session() as session:
        await CatalogLogoRepository.mark(
            session, source_url=_URL, status="ok", fetched_at=later, upstream_etag='"v2"'
        )
        await session.commit()

    async with registry_db.session() as session:
        row = await CatalogLogoRepository.get(session, _URL)
        assert row is not None
        assert (row.status, row.content, row.upstream_etag) == ("ok", _PNG, '"v2"')
