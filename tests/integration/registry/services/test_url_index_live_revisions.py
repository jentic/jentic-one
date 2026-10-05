"""The URL index serves only each API's live revision (#1086).

End-to-end through the real ingest pipeline and ``RevisionService`` against a
real registry database: every revision keeps its own URL-index rows, unpinned
lookups only consult ``apis.current_revision_id``, and a host served by one
vendor's live API cannot go live under another vendor.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import AsyncGenerator, Sequence
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text, update

from jentic_one.migrations.registry.versions import (
    e7f8a9b0c1d2_scope_url_index_key_to_revision as scope_migration,
)
from jentic_one.migrations.registry.versions import (
    e8f9a0b1c2d3_rebuild_displaced_url_index_rows as rebuild_migration,
)
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.core.url_index import URL_INDEX_FORMAT_MARKER
from jentic_one.registry.ingest.exc import IngestPipelineError
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.repos import ApiRepository, UrlIndexRepository
from jentic_one.registry.services.errors import HostOwnedByOtherVendorError
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ORIGIN_CATALOG, ApiRevisionSourceType, ApiRevisionState

pytestmark = pytest.mark.integration

_IDENTITY = Identity(sub="usr_test", email="test@example.com")
HOST = "api.widgets.example.com"
URL = f"https://{HOST}/widgets"
VENDOR = "widgets.example.com"
NAME = "widgets"
VERSION = "1.0.0"
OTHER_VENDOR = "other.example.net"


@pytest.fixture()
async def clean_registry(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Truncate the registry tables ingest writes, before and after each test."""

    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(SecuritySchemeFlow))
            await session.execute(delete(SecurityScheme))
            await session.execute(delete(ServerVariable))
            await session.execute(delete(Server))
            await session.execute(delete(Operation))
            await session.execute(delete(SpecFile))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


def _content(summary: str, *, host: str = HOST, extra_path: str | None = None) -> dict[str, Any]:
    paths: dict[str, Any] = {
        "/widgets": {
            "get": {
                "operationId": "listWidgets",
                "summary": summary,
                "responses": {"200": {"description": "ok"}},
            }
        }
    }
    if extra_path is not None:
        paths[extra_path] = copy.deepcopy(paths["/widgets"])
        paths[extra_path]["get"]["operationId"] = "extra"
    return {
        "openapi": "3.1.0",
        "info": {"title": "Widgets", "version": VERSION},
        "servers": [{"url": f"https://{host}"}],
        "paths": paths,
    }


def _spec(
    content: dict[str, Any],
    *,
    sha: str,
    vendor: str = VENDOR,
    name: str = NAME,
    origin: str | None = None,
) -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor=vendor, name=name, version=VERSION),
        sha=sha,
        content=content,
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="openapi.json",
        submitted_by="usr_test",
        origin=origin,
    )


async def _ingest(ctx: Context, spec: IngestSpecification) -> uuid.UUID:
    result = await Ingestor(ctx).ingest(spec, created_by="usr_test")
    return result.revision_id


async def _promote(ctx: Context, revision_id: uuid.UUID, *, vendor: str = VENDOR) -> None:
    await RevisionService(ctx).promote(vendor, NAME, VERSION, str(revision_id), identity=_IDENTITY)


async def _resolved_revision(registry_db: DatabaseSession, url: str = URL) -> uuid.UUID | None:
    """Revision whose operation an unpinned lookup resolves ``GET url`` to."""
    async with registry_db.session() as session:
        result = await URLLookupService(session).resolve(method="GET", url=url)
        if result is None:
            return None
        return (
            await session.execute(
                select(Operation.revision_id).where(Operation.id == result.operation_id)
            )
        ).scalar_one()


async def _row_count(registry_db: DatabaseSession, revision_id: uuid.UUID) -> int:
    async with registry_db.session() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(OperationURLIndex)
                .where(OperationURLIndex.revision_id == revision_id)
            )
        ).scalar_one()


async def _live_with_draft(ctx: Context) -> tuple[uuid.UUID, uuid.UUID]:
    """v1 imported and promoted; v2 (same URL, changed summary) imported as a draft."""
    v1 = await _ingest(ctx, _spec(_content("List widgets"), sha="sha-v1"))
    await _promote(ctx, v1)
    v2 = await _ingest(ctx, _spec(_content("List widgets (v2)"), sha="sha-v2"))
    return v1, v2


async def test_draft_import_does_not_reroute_live_url(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    v1, v2 = await _live_with_draft(integration_context)

    assert await _resolved_revision(registry_db) == v1
    # Both revisions keep their own rows; the draft displaced nothing.
    assert await _row_count(registry_db, v1) == 1
    assert await _row_count(registry_db, v2) == 1


async def test_draft_only_url_is_not_served_unpinned(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    await _ingest(integration_context, _spec(_content("List widgets"), sha="sha-v1"))

    assert await _resolved_revision(registry_db) is None


async def test_promote_serves_the_promoted_revision(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    v1, v2 = await _live_with_draft(integration_context)

    await _promote(integration_context, v2)

    assert await _resolved_revision(registry_db) == v2
    # The superseded revision is archived but keeps its rows.
    assert await _row_count(registry_db, v1) == 1


async def test_archive_and_delete_draft_leave_live_serving(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    v1, v2 = await _live_with_draft(integration_context)
    svc = RevisionService(integration_context)

    await svc.archive(VENDOR, NAME, VERSION, str(v2), identity=_IDENTITY)
    assert await _resolved_revision(registry_db) == v1

    await svc.delete(VENDOR, NAME, VERSION, str(v2), identity=_IDENTITY)
    assert await _resolved_revision(registry_db) == v1
    assert await _row_count(registry_db, v1) == 1
    assert await _row_count(registry_db, v2) == 0


async def test_identical_reimport_keeps_live_serving(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    v1, v2 = await _live_with_draft(integration_context)

    # Re-importing the live spec is still refused as a duplicate, but nothing
    # needs repairing: the live revision never lost its rows.
    with pytest.raises(IngestPipelineError):
        await _ingest(integration_context, _spec(_content("List widgets"), sha="sha-v1"))
    assert await _resolved_revision(registry_db) == v1

    # Re-importing the identical draft replaces it; live serving is unaffected.
    v2_again = await _ingest(
        integration_context, _spec(_content("List widgets (v2)"), sha="sha-v2")
    )
    assert v2_again != v2
    assert await _resolved_revision(registry_db) == v1
    assert await _row_count(registry_db, v2_again) == 1


async def test_other_vendor_cannot_promote_onto_a_served_host(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    owner = await _ingest(integration_context, _spec(_content("List widgets"), sha="sha-owner"))
    await _promote(integration_context, owner)

    other_vendor = "other.example.net"
    draft = await _ingest(
        integration_context,
        _spec(_content("Other widgets"), sha="sha-other", vendor=other_vendor),
    )
    # The other vendor's draft neither serves nor displaces anything...
    assert await _resolved_revision(registry_db) == owner

    # ...and cannot be promoted while the owner's live revision serves the host.
    with pytest.raises(HostOwnedByOtherVendorError) as exc_info:
        await _promote(integration_context, draft, vendor=other_vendor)
    assert HOST in str(exc_info.value)
    assert VENDOR in str(exc_info.value)

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == other_vendor))).scalar_one()
        assert api.current_revision_id is None
        rev = await session.get(ApiRevision, draft)
        assert rev is not None
        assert rev.state == ApiRevisionState.DRAFT
    assert await _resolved_revision(registry_db) == owner


async def test_other_vendor_can_promote_once_the_host_is_released(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    owner = await _ingest(
        integration_context,
        _spec(_content("List widgets"), sha="sha-owner", origin=ORIGIN_CATALOG),
    )
    other_vendor = "other.example.net"
    draft = await _ingest(
        integration_context,
        _spec(_content("Other widgets"), sha="sha-other", vendor=other_vendor),
    )

    # The owner's live revision is an auto-live import; archiving it frees the host.
    await RevisionService(integration_context).archive(
        VENDOR, NAME, VERSION, str(owner), identity=_IDENTITY
    )
    await _promote(integration_context, draft, vendor=other_vendor)

    assert await _resolved_revision(registry_db) == draft


async def test_other_vendor_auto_live_import_onto_a_served_host_is_refused(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    owner = await _ingest(integration_context, _spec(_content("List widgets"), sha="sha-owner"))
    await _promote(integration_context, owner)

    with pytest.raises(IngestPipelineError, match="served by one vendor at a time"):
        await _ingest(
            integration_context,
            _spec(
                _content("Other widgets"),
                sha="sha-other",
                vendor="other.example.net",
                origin=ORIGIN_CATALOG,
            ),
        )

    async with registry_db.session() as session:
        vendors = (await session.execute(select(Api.vendor))).scalars().all()
    # The whole import rolled back.
    assert vendors == [VENDOR]
    assert await _resolved_revision(registry_db) == owner


async def test_same_vendor_apis_may_share_a_host(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    first = await _ingest(integration_context, _spec(_content("List widgets"), sha="sha-a"))
    await _promote(integration_context, first)
    second = await _ingest(
        integration_context,
        _spec(_content("Widgets admin", extra_path="/admin"), sha="sha-b", name="widgets-admin"),
    )
    await RevisionService(integration_context).promote(
        VENDOR, "widgets-admin", VERSION, str(second), identity=_IDENTITY
    )

    assert await _resolved_revision(registry_db, f"https://{HOST}/admin") == second
    # Both live revisions serve /widgets; the most recently promoted one wins.
    assert await _resolved_revision(registry_db) == second


async def test_templated_hosts_do_not_claim_ownership(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    templated = _content("Regional widgets", host="{region}.widgets.example.com")
    owner = await _ingest(integration_context, _spec(templated, sha="sha-owner"))
    await _promote(integration_context, owner)

    other_vendor = "other.example.net"
    draft = await _ingest(
        integration_context,
        _spec(templated, sha="sha-other", vendor=other_vendor),
    )
    # Only concrete hosts are owned; a shared unresolved template is not a claim.
    await _promote(integration_context, draft, vendor=other_vendor)


async def test_trailing_dot_spelling_is_the_same_host(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    owner = await _ingest(integration_context, _spec(_content("List widgets"), sha="sha-owner"))
    await _promote(integration_context, owner)

    draft = await _ingest(
        integration_context,
        _spec(_content("Other widgets", host=f"{HOST}."), sha="sha-other", vendor=OTHER_VENDOR),
    )
    # ``api.example.com.`` names the same host as ``api.example.com``.
    with pytest.raises(HostOwnedByOtherVendorError):
        await _promote(integration_context, draft, vendor=OTHER_VENDOR)


async def test_concurrent_go_live_onto_one_host_is_serialized(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    """Two vendors going live on one host at once: the second waits, then is refused.

    Holds vendor A's go-live transaction open right after its ownership check
    (which takes the host lock) and starts vendor B's promote. Without the lock
    B would pass its check against A's uncommitted state and both would go live.
    """
    if registry_db.backend.dialect_name == "sqlite":
        pytest.skip("advisory locks are Postgres-only; SQLite serializes writers itself")

    first = await _ingest(integration_context, _spec(_content("List widgets"), sha="sha-a"))
    second = await _ingest(
        integration_context,
        _spec(_content("Other widgets"), sha="sha-b", vendor=OTHER_VENDOR),
    )

    async with registry_db.transaction() as session:
        owners = await UrlIndexRepository.find_live_hosts_of_other_vendors(
            session, revision_id=first, vendor=VENDOR
        )
        assert owners == []
        api_id = (await session.execute(select(Api.id).where(Api.vendor == VENDOR))).scalar_one()
        await ApiRepository.set_current_revision(session, api_id, first)

        racing = asyncio.create_task(_promote(integration_context, second, vendor=OTHER_VENDOR))
        done, _pending = await asyncio.wait({racing}, timeout=1.0)
        assert not done, "the second go-live must wait for the host lock"

    with pytest.raises(HostOwnedByOtherVendorError):
        await racing
    assert await _resolved_revision(registry_db) == first


async def test_widened_key_keeps_the_previous_constraint_name(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """Previous-release pods upsert with ``ON CONFLICT ON CONSTRAINT <this name>``."""
    if registry_db.backend.dialect_name == "sqlite":
        pytest.skip("inspects the Postgres catalog")
    async with registry_db.session() as session:
        definition = (
            await session.execute(
                text(
                    "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE c.conname = 'uq_operation_url_index_lookup' "
                    "AND t.relname = 'operation_url_indexes'"
                )
            )
        ).scalar_one()
    assert definition == (
        "UNIQUE NULLS NOT DISTINCT (host, method, host_regex, path_template, revision_id)"
    )


def _as_legacy_set(rows: Sequence[tuple[Any, ...]]) -> set[tuple[Any, ...]]:
    """Hashable row set (``param_names`` is a list column), with ``path_regex`` in
    the format rows had before the format marker."""
    return {
        (*row[:6], row[6].removeprefix(URL_INDEX_FORMAT_MARKER), tuple(row[7]), row[8])
        for row in rows
    }


async def test_rebuild_migration_restores_displaced_rows(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    """The e8f9a0b1c2d3 rebuild re-creates the rows ingest writes, in the legacy
    (pre-format-marker) row format that the lookup re-derives server variables for."""
    v1, v2 = await _live_with_draft(integration_context)
    columns = (
        OperationURLIndex.revision_id,
        OperationURLIndex.operation_id,
        OperationURLIndex.method,
        OperationURLIndex.host,
        OperationURLIndex.host_regex,
        OperationURLIndex.path_template,
        OperationURLIndex.path_regex,
        OperationURLIndex.param_names,
        OperationURLIndex.segment_count,
    )
    async with registry_db.session() as session:
        ingested = _as_legacy_set((await session.execute(select(*columns))).tuples().all())
        # Simulate the pre-change displacement: the live revision lost its rows.
        await session.execute(delete(OperationURLIndex).where(OperationURLIndex.revision_id == v1))
        await session.commit()
    assert await _resolved_revision(registry_db) is None

    async with registry_db.session() as session:
        inserted = await session.run_sync(
            lambda sync: rebuild_migration.rebuild_url_index(sync.connection())
        )
        await session.commit()
    assert inserted == 1

    async with registry_db.session() as session:
        rebuilt = _as_legacy_set((await session.execute(select(*columns))).tuples().all())
        again = await session.run_sync(
            lambda sync: rebuild_migration.rebuild_url_index(sync.connection())
        )
    assert rebuilt == ingested
    assert again == 0
    assert await _resolved_revision(registry_db) == v1
    assert await _row_count(registry_db, v2) == 1


async def test_scope_migration_downgrade_keeps_the_live_row_per_url(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    """Before the global key is restored, duplicates collapse onto the live row."""
    v1, v2 = await _live_with_draft(integration_context)

    async with registry_db.session() as session:
        await session.run_sync(
            lambda sync: scope_migration.collapse_to_global_key(sync.connection())
        )
        await session.commit()

    assert await _row_count(registry_db, v1) == 1
    assert await _row_count(registry_db, v2) == 0


async def _delete_rows(registry_db: DatabaseSession, *revision_ids: uuid.UUID) -> None:
    async with registry_db.session() as session:
        await session.execute(
            delete(OperationURLIndex).where(OperationURLIndex.revision_id.in_(revision_ids))
        )
        await session.commit()


async def _run_rebuild(registry_db: DatabaseSession) -> int:
    async with registry_db.session() as session:
        inserted = await session.run_sync(
            lambda sync: rebuild_migration.rebuild_url_index(sync.connection())
        )
        await session.commit()
    return inserted


async def test_rebuild_migration_skips_revisions_that_can_never_serve(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    """A superseded (archived, not an overlay rollback target) revision is not rebuilt."""
    v1, v2 = await _live_with_draft(integration_context)
    await _promote(integration_context, v2)
    await _delete_rows(registry_db, v1, v2)

    assert await _run_rebuild(registry_db) == 1
    assert await _row_count(registry_db, v1) == 0
    assert await _row_count(registry_db, v2) == 1


async def test_rebuild_migration_skips_a_malformed_stored_spec(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    v1, v2 = await _live_with_draft(integration_context)
    await _delete_rows(registry_db, v1, v2)
    broken = _content("List widgets (v2)")
    broken["paths"] = ["not", "a", "map"]
    async with registry_db.session() as session:
        await session.execute(
            update(SpecFile).where(SpecFile.revision_id == v2).values(content=broken)
        )
        await session.commit()

    # The healthy live revision is still rebuilt; the broken draft is skipped.
    assert await _run_rebuild(registry_db) == 1
    assert await _row_count(registry_db, v1) == 1
    assert await _row_count(registry_db, v2) == 0


async def test_rebuild_migration_matches_existing_rows_structurally(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    """A present row whose template differs only in parameter names is not duplicated."""
    content = _content("Get widget")
    content["paths"] = {"/widgets/{id}": content["paths"]["/widgets"]}
    live = await _ingest(integration_context, _spec(content, sha="sha-param"))
    await _promote(integration_context, live)
    async with registry_db.session() as session:
        await session.execute(
            update(OperationURLIndex)
            .where(OperationURLIndex.revision_id == live)
            .values(path_template="/widgets/{widget_id}")
        )
        await session.commit()

    assert await _run_rebuild(registry_db) == 0
    assert await _row_count(registry_db, live) == 1
