"""Integration tests for the e6f7a8b9c0d1 URL-index repair migration (#1085).

Seeds ``operation_url_indexes`` rows exactly as the pre-fix ingest code wrote
them (trailing slash baked into ``path_template`` and ``path_regex``), runs the
migration's ``repair_url_index`` body against the real database, and asserts
the rows become canonical — including the unique-constraint collision path and
idempotency.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from jentic_one.migrations.registry.versions import (
    e6f7a8b9c0d1_normalize_url_index_path_templates as repair_migration,
)
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.url_index import build_host_regex
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

HOST = "fantasy.premierleague.com"
HOST_REGEX = build_host_regex(HOST).pattern


def _run_repair(sync_session: Session) -> None:
    repair_migration.repair_url_index(sync_session.connection())


async def _seed_stale_row(
    registry_db: DatabaseSession,
    rev: ApiRevision,
    *,
    path: str,
    path_template: str,
    path_regex: str,
    param_names: list[str],
    segment_count: int,
    method: str = "GET",
) -> str:
    """Insert a URL-index row exactly as the pre-#1085 ingest code wrote it."""
    async with registry_db.session() as session:
        op_ids = await OperationRepository.bulk_create(
            session, rev.id, [OperationInput(path=path, method=method)], created_by="usr_test"
        )
        session.add(
            OperationURLIndex(
                operation_id=op_ids[0],
                revision_id=rev.id,
                method=method,
                host=HOST,
                host_regex=HOST_REGEX,
                path_template=path_template,
                path_regex=path_regex,
                param_names=param_names,
                segment_count=segment_count,
                created_by="usr_test",
            )
        )
        await session.commit()
    return op_ids[0]


async def test_repair_normalizes_stale_trailing_slash_row(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """A pre-fix trailing-slash row becomes canonical and resolvable.

    The seeded values are the verbatim broken state from #1085: the stored
    regex demands a trailing slash the lookup's normalization has removed.
    """
    _, rev = sample_revision
    op_id = await _seed_stale_row(
        registry_db,
        rev,
        path="/api/bootstrap-static/",
        path_template="/api/bootstrap-static/",
        path_regex=r"^/api/bootstrap\-static/$",
        param_names=[],
        segment_count=2,
    )

    # Pre-condition: the stale row is unresolvable (the bug).
    async with registry_db.session() as session:
        assert (
            await URLLookupService(session).resolve(
                method="GET", url=f"https://{HOST}/api/bootstrap-static/", revision_id=rev.id
            )
            is None
        )

    async with registry_db.session() as session:
        await session.run_sync(_run_repair)
        await session.commit()

    async with registry_db.session() as session:
        row = (await session.execute(select(OperationURLIndex))).scalar_one()
        assert row.path_template == "/api/bootstrap-static"
        assert row.path_regex == r"^/api/bootstrap\-static$"
        assert row.segment_count == 2

        for request_path in ("/api/bootstrap-static/", "/api/bootstrap-static"):
            result = await URLLookupService(session).resolve(
                method="GET", url=f"https://{HOST}{request_path}", revision_id=rev.id
            )
            assert result is not None
            assert result.operation_id == op_id


async def test_repair_normalizes_stale_parameterized_row(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """Parameter tokens survive the repair; path params still extract."""
    _, rev = sample_revision
    op_id = await _seed_stale_row(
        registry_db,
        rev,
        path="/element-summary/{elementId}/",
        path_template="/api/element-summary/{elementId}/",
        path_regex=r"^/api/element\-summary/(?P<elementId>[^/]+)/$",
        param_names=["elementId"],
        segment_count=3,
    )

    async with registry_db.session() as session:
        await session.run_sync(_run_repair)
        await session.commit()

    async with registry_db.session() as session:
        row = (await session.execute(select(OperationURLIndex))).scalar_one()
        assert row.path_template == "/api/element-summary/{elementId}"
        assert row.param_names == ["elementId"]

        result = await URLLookupService(session).resolve(
            method="GET", url=f"https://{HOST}/api/element-summary/42/", revision_id=rev.id
        )
        assert result is not None
        assert result.operation_id == op_id
        assert result.path_params == {"elementId": "42"}


async def test_repair_is_idempotent_and_skips_canonical_rows(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """Running the repair twice converges; already-canonical rows are untouched."""
    _, rev = sample_revision
    await _seed_stale_row(
        registry_db,
        rev,
        path="/api/bootstrap-static/",
        path_template="/api/bootstrap-static/",
        path_regex=r"^/api/bootstrap\-static/$",
        param_names=[],
        segment_count=2,
    )
    await _seed_stale_row(
        registry_db,
        rev,
        path="/api/fixtures",
        path_template="/api/fixtures",
        path_regex=r"^/api/fixtures$",
        param_names=[],
        segment_count=2,
        method="POST",
    )

    async def _snapshot() -> list[tuple[str, str, str, int]]:
        async with registry_db.session() as session:
            rows = (await session.execute(select(OperationURLIndex))).scalars().all()
            return sorted((r.method, r.path_template, r.path_regex, r.segment_count) for r in rows)

    async with registry_db.session() as session:
        await session.run_sync(_run_repair)
        await session.commit()
    first = await _snapshot()

    async with registry_db.session() as session:
        await session.run_sync(_run_repair)
        await session.commit()
    second = await _snapshot()

    assert first == second
    assert ("POST", "/api/fixtures", r"^/api/fixtures$", 2) in first
    assert ("GET", "/api/bootstrap-static", r"^/api/bootstrap\-static$", 2) in first


async def test_repair_deletes_stale_row_on_canonical_collision(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """When a canonical row already occupies the target unique key, the stale
    row is deleted rather than violating the constraint.

    The unique key is global — ``(method, host, host_regex, path_template)``
    without revision — so a slash-less row (e.g. written after a manual
    re-import workaround) can already own the key a stale row normalizes onto.
    """
    _, rev = sample_revision
    await _seed_stale_row(
        registry_db,
        rev,
        path="/api/bootstrap-static/",
        path_template="/api/bootstrap-static/",
        path_regex=r"^/api/bootstrap\-static/$",
        param_names=[],
        segment_count=2,
    )
    canonical_op_id = await _seed_stale_row(
        registry_db,
        rev,
        path="/api/bootstrap-static",
        path_template="/api/bootstrap-static",
        path_regex=r"^/api/bootstrap\-static$",
        param_names=[],
        segment_count=2,
    )

    async with registry_db.session() as session:
        await session.run_sync(_run_repair)
        await session.commit()

    async with registry_db.session() as session:
        row = (await session.execute(select(OperationURLIndex))).scalar_one()
        assert row.operation_id == canonical_op_id
        assert row.path_template == "/api/bootstrap-static"
