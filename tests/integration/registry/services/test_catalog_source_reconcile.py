"""Boot reconcile of the legacy catalog source from the last ``catalog_snapshots`` blob.

Runs against the real registry DB with no network: the snapshot blob is the only
input, and a source that tries to fetch fails the test.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun
from jentic_one.registry.repos.catalog_entry_repo import CatalogEntryRepository
from jentic_one.registry.repos.catalog_repo import CatalogRepository
from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog.sources.reconcile import reconcile_legacy_source
from jentic_one.registry.services.catalog.sources.scanner import CatalogSourceSyncScanner
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.testing import FakeSourceHttp, fake_source_deps
from tests.integration.registry.services.catalog_sources_support import CtxFactory

pytestmark = pytest.mark.integration

_BASE = "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi"


def _blob(*api_ids: str) -> list[dict[str, Any]]:
    def _url(api_id: str) -> str:
        sub = "" if "/" in api_id else "/main"
        return f"{_BASE}/{api_id}{sub}/v1/apis.json"

    doc = {"include": [{"url": _url(a)} for a in api_ids]}
    return [e.to_dict() for e in mb.parse_apis_json(doc)]


def _spec_url(api_id: str) -> str:
    return f"{_BASE}/{api_id}/main/v1/openapi.json"


def _offline(_: object) -> Any:
    http = FakeSourceHttp()
    http.fail_with = AssertionError("the reconcile must not touch the network")
    return fake_source_deps("public", http)


async def _seed(ctx: Context, blob: list[dict[str, Any]]) -> dict[str, Api]:
    """A snapshot blob plus three pre-existing imports.

    ``by-id`` carries a ``catalog_api_id``; ``by-url`` predates it and only has
    a revision ``source_url``; ``orphan`` points at an id the blob doesn't list.
    """
    apis = {
        "by-id": Api(
            vendor="sync-test.io", name="by-id", version="v1", catalog_api_id="stripe.com"
        ),
        "by-url": Api(vendor="sync-test.io", name="by-url", version="v1"),
        "orphan": Api(
            vendor="sync-test.io", name="orphan", version="v1", catalog_api_id="gone.example"
        ),
    }
    async with ctx.registry_db.transaction() as session:
        await CatalogRepository.replace(session, source_url="https://x/apis.json", entries=blob)
        session.add_all(apis.values())
        await session.flush()
        session.add(
            ApiRevision(
                api_id=apis["by-url"].id,
                state="published",
                spec_digest="sha256:acme",
                source_type="url",
                source_url=_spec_url("acme.io"),
                origin="catalog",
            )
        )
    return apis


async def _entry_ids(ctx: Context) -> dict[str, str | None]:
    async with ctx.registry_db.session() as session:
        rows = (
            await session.execute(
                select(Api.name, Api.catalog_entry_id).where(Api.vendor == "sync-test.io")
            )
        ).all()
    return {r[0]: r[1] for r in rows}


async def test_reconcile_seeds_entries_and_backfills_offline(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx() as ctx:
        await _seed(ctx, _blob("stripe.com", "acme.io", "googleapis.com/admin"))
        sync = CatalogSourceSync(ctx, deps_factory=_offline)
        await sync.register_sources()
        outcome = await reconcile_legacy_source(sync)
        assert (outcome.status, outcome.seeded, outcome.backfilled, outcome.unmapped) == (
            "ran",
            3,
            2,
            1,
        )
        async with ctx.registry_db.session() as session:
            rows = await CatalogEntryRepository.rows_for_source(session, "public", live_only=True)
            source = await CatalogSourceRepository.get(session, "public")
        assert {(r.entry_id, r.catalog_api_id) for r in rows} == {
            ("public/stripe.com", "stripe.com"),
            ("public/acme.io", "acme.io"),
            ("public/googleapis.com/admin", "googleapis.com/admin"),
        }
        assert source is not None and source.last_applied_run_id is not None
        assert source.sync_lease_owner is None
        assert await _entry_ids(ctx) == {
            "by-id": "public/stripe.com",
            "by-url": "public/acme.io",
            "orphan": None,
        }

        # Second boot: nothing left to seed or backfill.
        again = await reconcile_legacy_source(CatalogSourceSync(ctx, deps_factory=_offline))
        assert (again.seeded, again.backfilled) == (0, 0)
        async with ctx.registry_db.session() as session:
            runs = (await session.execute(select(CatalogSyncRun.id))).all()
        assert len(runs) == 1


async def test_reconcile_counts_only_rows_it_could_have_mapped(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx() as ctx:
        await _seed(ctx, _blob("stripe.com", "acme.io"))
        async with ctx.registry_db.transaction() as session:
            manual = Api(vendor="sync-test.io", name="manual", version="v1")
            stray = Api(vendor="sync-test.io", name="stray", version="v1")
            session.add_all([manual, stray])
            await session.flush()
            session.add(
                ApiRevision(
                    api_id=stray.id,
                    state="published",
                    spec_digest="sha256:x",
                    source_type="url",
                    source_url="https://elsewhere.example/a.json",
                    origin="catalog",
                )
            )
        sync = CatalogSourceSync(ctx, deps_factory=_offline)
        await sync.register_sources()
        outcome = await reconcile_legacy_source(sync)
        # "orphan" (an unknown catalog_api_id) and "stray" (an unknown spec URL)
        # could have matched; "manual" has nothing to match and is not counted.
        assert (outcome.backfilled, outcome.unmapped) == (2, 2)
        assert (await _entry_ids(ctx))["manual"] is None


async def test_reconcile_runs_on_exactly_one_of_two_replicas(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx() as ctx:
        await _seed(ctx, _blob("stripe.com", "acme.io"))
        await CatalogSourceSync(ctx).register_sources()
        a, b = await asyncio.gather(
            reconcile_legacy_source(CatalogSourceSync(ctx, deps_factory=_offline)),
            reconcile_legacy_source(CatalogSourceSync(ctx, deps_factory=_offline)),
        )
        statuses = sorted([a.status, b.status])
        seeded = a.seeded + b.seeded
        # Either one replica held the lease and the other skipped, or the second
        # ran after the first finished and found nothing left to seed.
        assert seeded == 2
        assert statuses in (["ran", "skipped"], ["ran", "ran"])
        async with ctx.registry_db.session() as session:
            runs = (await session.execute(select(CatalogSyncRun.id))).all()
        assert len(runs) == 1


async def test_reconcile_skips_while_another_replica_holds_the_lease(
    catalog_ctx: CtxFactory,
) -> None:
    async with catalog_ctx() as ctx:
        await _seed(ctx, _blob("stripe.com"))
        sync = CatalogSourceSync(ctx, deps_factory=_offline)
        await sync.register_sources()
        async with ctx.registry_db.transaction() as session:
            assert await CatalogSourceRepository.claim_lease(
                session, slug="public", owner=generate_ksuid("csyn"), lease_seconds=60
            )
        outcome = await reconcile_legacy_source(sync)
        assert outcome.status == "skipped"
        assert (await _entry_ids(ctx))["by-id"] is None


async def test_reconcile_without_a_snapshot_seeds_nothing(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx() as ctx:
        sync = CatalogSourceSync(ctx, deps_factory=_offline)
        await sync.register_sources()
        outcome = await reconcile_legacy_source(sync)
        assert (outcome.status, outcome.seeded) == ("ran", 0)
        async with ctx.registry_db.session() as session:
            source = await CatalogSourceRepository.get(session, "public")
        assert source is not None
        assert source.last_applied_run_id is None
        assert source.sync_lease_owner is None


async def test_scanner_boot_registers_sources_and_reconciles(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx() as ctx:
        await _seed(ctx, _blob("stripe.com"))
        outcome = await CatalogSourceSyncScanner(ctx).boot()
        assert outcome.seeded == 1
        assert (await _entry_ids(ctx))["by-id"] == "public/stripe.com"
