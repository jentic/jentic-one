"""Staged per-source catalog sync against the real registry DB.

The source listing is scripted (no network); everything below it — staging, the
lease, the apply transaction, the guard, tombstones, re-resolve — runs for real.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from structlog.testing import capture_logs

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.core.schema.catalog_staged_entries import CatalogStagedEntry
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun
from jentic_one.registry.repos.catalog_entry_repo import (
    CatalogEntryRepository,
    CatalogSyncRunRepository,
)
from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.services.catalog.entries import (
    CatalogApiIdMapping,
    build_catalog_entry,
    curated_vendors_of,
    exclude_unowned,
)
from jentic_one.registry.services.catalog.sources.protocol import Removed, Upserted
from jentic_one.registry.services.catalog.sources.scanner import CatalogSourceSyncScanner
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.utils import utcnow
from tests.integration.registry.services.catalog_sources_support import (
    CtxFactory,
    ScriptedStream,
    entry,
    full,
    git_source,
)

pytestmark = pytest.mark.integration


async def _rows(ctx: Context, slug: str, *, live_only: bool = True) -> dict[str, CatalogEntryRow]:
    async with ctx.registry_db.session() as session:
        rows = await CatalogEntryRepository.rows_for_source(session, slug, live_only=live_only)
    return {r.external_id: r for r in rows}


async def _source(ctx: Context, slug: str) -> CatalogSourceRow:
    async with ctx.registry_db.session() as session:
        row = await CatalogSourceRepository.get(session, slug)
    assert row is not None
    return row


def _scripted_source(stream: ScriptedStream) -> Callable[..., Any]:
    """A ``build_source`` replacement whose source lists ``stream``."""

    class _Scripted:
        def list_entries(self, *, since: str | None) -> ScriptedStream:
            return stream

    return lambda self, cfg: _Scripted()


async def _staged_count(ctx: Context) -> int:
    async with ctx.registry_db.session() as session:
        return len((await session.execute(select(CatalogStagedEntry.id))).all())


async def test_full_listing_upserts_then_tombstones_absent_rows(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        first = await sync.sync_source("public", stream=full("a.io", "b.io", "c.io"))
        assert (first.status, first.upserted, first.removed) == ("applied", 3, 0)
        live = await _rows(ctx, "public")
        assert set(live) == {"a.io", "b.io", "c.io"}
        assert live["a.io"].entry_id == "public/a.io"
        assert live["a.io"].catalog_api_id == "a.io"
        source = await _source(ctx, "public")
        assert (source.live_entries, source.last_status, source.watermark) == (3, "ok", "w3")
        digest = source.content_digest
        assert await _staged_count(ctx) == 0

        second = await sync.sync_source("public", stream=full("a.io", "b.io"))
        assert (second.status, second.removed) == ("applied", 1)
        assert set(await _rows(ctx, "public")) == {"a.io", "b.io"}
        all_rows = await _rows(ctx, "public", live_only=False)
        assert all_rows["c.io"].removed_at is not None
        assert (await _source(ctx, "public")).content_digest != digest

        # A later listing revives the same row.
        third = await sync.sync_source("public", stream=full("a.io", "b.io", "c.io"))
        assert third.status == "applied"
        revived = await _rows(ctx, "public")
        assert revived["c.io"].id == all_rows["c.io"].id
        assert (await _source(ctx, "public")).content_digest == digest


async def test_unchanged_listing_leaves_digests_unchanged(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("a.io", "b.io"))
        before = await _source(ctx, "public")
        await sync.sync_source("public", stream=full("b.io", "a.io"))
        after = await _source(ctx, "public")
        assert (after.content_digest, after.listing_digest) == (
            before.content_digest,
            before.listing_digest,
        )


async def test_listing_that_raises_midway_applies_nothing(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("a.io", "b.io"))
        broken = ScriptedStream([Upserted(entry("x.io")), Upserted(entry("y.io"))], raise_after=1)
        outcome = await sync.sync_source("public", stream=broken)
        assert (outcome.status, outcome.error_code) == ("failed", "listing_error")
        assert set(await _rows(ctx, "public")) == {"a.io", "b.io"}
        assert await _staged_count(ctx) == 0
        source = await _source(ctx, "public")
        assert (source.last_status, source.consecutive_failures) == ("failed", 1)
        assert source.sync_lease_owner is None
        async with ctx.registry_db.session() as session:
            run = await session.get(CatalogSyncRun, outcome.run_id)
        assert run is not None and run.status == "failed"


async def test_delta_listing_never_tombstones_unlisted_rows(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("a.io", "b.io", "c.io"))
        delta = ScriptedStream([Upserted(entry("d.io")), Removed("a.io")], is_full=False)
        outcome = await sync.sync_source("public", stream=delta)
        assert (outcome.status, outcome.upserted, outcome.removed) == ("applied", 1, 1)
        assert set(await _rows(ctx, "public")) == {"b.io", "c.io", "d.io"}


async def test_superseded_run_applies_nothing(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        old_run = generate_ksuid("csyn")
        newer_run = generate_ksuid("csyn")
        cfg = sync.sources["public"]
        async with ctx.registry_db.transaction() as session:
            assert await CatalogSourceRepository.claim_lease(
                session, slug="public", owner=old_run, lease_seconds=60
            )
            # A run that started later has already applied.
            await CatalogSyncRunRepository.create(
                session,
                run_id=newer_run,
                slug="public",
                started_at=utcnow() + timedelta(minutes=5),
                forced=False,
            )
            await session.execute(
                update(CatalogSourceRow)
                .where(CatalogSourceRow.slug == "public")
                .values(last_applied_run_id=newer_run)
            )
        outcome = await sync.run_claimed(cfg, full("a.io"), run_id=old_run)
        assert outcome.status == "superseded"
        assert await _rows(ctx, "public") == {}
        assert await _staged_count(ctx) == 0
        assert (await _source(ctx, "public")).sync_lease_owner is None


async def test_two_processes_wanting_one_source_run_one_sync(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        await CatalogSourceSync(ctx).register_sources()
        gate = asyncio.Event()
        slow = ScriptedStream([Upserted(entry("a.io"))], gate=gate)
        first = asyncio.create_task(CatalogSourceSync(ctx).sync_source("public", stream=slow))
        for _ in range(50):
            if (await _source(ctx, "public")).sync_lease_owner is not None:
                break
            await asyncio.sleep(0.02)
        second = await CatalogSourceSync(ctx).sync_source("public", stream=full("b.io"))
        assert second.status == "busy"
        gate.set()
        assert (await first).status == "applied"
        assert set(await _rows(ctx, "public")) == {"a.io"}


async def test_crashed_runs_lease_lapses_after_sync_lease_seconds(
    catalog_ctx: CtxFactory,
) -> None:
    source = git_source("public", trust="curated", sync_lease_seconds=3)
    async with catalog_ctx(sources=[source]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        async with ctx.registry_db.transaction() as session:
            assert await CatalogSourceRepository.claim_lease(
                session, slug="public", owner=generate_ksuid("csyn"), lease_seconds=3
            )
        assert (await sync.sync_source("public", stream=full("a.io"))).status == "busy"
        await asyncio.sleep(3.2)
        assert (await sync.sync_source("public", stream=full("a.io"))).status == "applied"


async def test_staged_connector_text_is_stored_sanitised(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        dirty = entry(
            "a.io",
            title="Pay\u202e\x00ments " + "x" * 300,
            human_url="javascript:alert(1)",
            tags=["  ok ", "ok", "\x07bell", *[f"t{i}" for i in range(30)]],
            version=" 1.0\n",
        )
        await sync.sync_source("public", stream=ScriptedStream([Upserted(dirty)]))
        row = (await _rows(ctx, "public"))["a.io"]
        assert row.title is not None and len(row.title) <= 120
        assert row.title.startswith("Pay ments ")
        assert "\u202e" not in row.title
        assert row.human_url is None
        assert row.tags[:2] == ["ok", "bell"]
        assert len(row.tags) == 16
        assert row.version == "1.0"


async def test_mass_removal_guard_holds_and_force_overrides(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        eight = [f"e{i}.io" for i in range(8)]
        await sync.sync_source("public", stream=full(*eight))
        # 2 of 8 is above the ratio but not above min_removal_count: applies.
        two_gone = await sync.sync_source("public", stream=full(*eight[:6]))
        assert (two_gone.status, two_gone.removed) == ("applied", 2)

        # Removing everything always trips; the rows stay.
        empty = await sync.sync_source("public", stream=full())
        assert empty.status == "suspect_empty"
        assert len(await _rows(ctx, "public")) == 6
        assert (await _source(ctx, "public")).last_status == "suspect_empty"

        forced = await sync.sync_source("public", stream=full(), forced=True)
        assert (forced.status, forced.removed) == ("applied", 6)
        assert await _rows(ctx, "public") == {}


async def test_mass_removal_guard_trips_above_ratio_and_count(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        thirty = [f"e{i}.io" for i in range(30)]
        await sync.sync_source("public", stream=full(*thirty))
        held = await sync.sync_source("public", stream=full(*thirty[:20]))
        assert held.status == "suspect_empty"
        assert len(await _rows(ctx, "public")) == 30


async def test_stale_source_is_flagged_and_the_scanner_drains_it(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        assert await sync.mark_stale_wanted() == 1
        flagged = await _source(ctx, "public")
        assert flagged.sync_wanted_at is not None
        assert flagged.sync_lease_owner is None  # flagging starts no sync

        monkeypatch.setattr(CatalogSourceSync, "build_source", _scripted_source(full("a.io")))
        outcomes = await CatalogSourceSyncScanner(ctx).tick()
        assert [o.status for o in outcomes] == ["applied"]
        drained = await _source(ctx, "public")
        assert drained.sync_wanted_at is None
        assert drained.last_sync_at is not None
        assert await sync.mark_stale_wanted() == 0


async def test_max_age_zero_source_is_never_flagged(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(manifest_max_age_seconds=0) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        assert await sync.mark_stale_wanted() == 0
        assert await CatalogSourceSyncScanner(ctx).tick() == []


# ── re-resolve ────────────────────────────────────────────────────────────────


async def test_curated_apply_that_adds_a_vendor_flags_and_reresolves_others(
    catalog_ctx: CtxFactory,
) -> None:
    sources = [git_source("public", trust="curated"), git_source("corp")]
    async with catalog_ctx(sources=sources) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("corp", stream=full("pay", pay="stripe.com"))
        assert (await _rows(ctx, "corp"))["pay"].catalog_api_id == "stripe.com"

        # An API imported from that entry before the curated source claimed it.
        async with ctx.registry_db.session() as session:
            session.add(
                Api(
                    vendor="sync-test.io",
                    name="pay",
                    version="v1",
                    catalog_api_id="stripe.com",
                    catalog_entry_id="corp/pay",
                )
            )
            await session.commit()

        await sync.sync_source("public", stream=full("stripe.com"))
        assert (await _source(ctx, "corp")).reresolve_wanted_at is not None

        # Until it is re-resolved, the read-side fail-safe drops the stale claim.
        stale = (await _rows(ctx, "corp"))["pay"]
        view = build_catalog_entry(
            source_slug="corp",
            external_id="pay",
            catalog_api_id=stale.catalog_api_id,
            hint=stale.catalog_api_id_hint,
            title=None,
            version=None,
        )
        assert (
            exclude_unowned(
                [view],
                curated_sources=frozenset({"public"}),
                curated_vendors=curated_vendors_of({"stripe.com"}),
                mappings=[],
            )
            == []
        )

        with capture_logs() as logs:
            changed = await CatalogSourceSync(ctx).reresolve_source("corp")
        assert changed == 1
        assert (await _rows(ctx, "corp"))["pay"].catalog_api_id == "corp~stripe.com"
        corp = await _source(ctx, "corp")
        assert corp.reresolve_wanted_at is None
        assert corp.resolver_inputs_digest == sync.current_resolver_inputs_digest()
        [conflict] = [e for e in logs if e["event"] == "catalog_ownership_conflict"]
        assert conflict["entry_id"] == "corp/pay"
        assert conflict["kept_catalog_api_id"] == "stripe.com"
        async with ctx.registry_db.session() as session:
            api = (await session.execute(select(Api).where(Api.name == "pay"))).scalar_one()
        assert api.catalog_api_id == "stripe.com"


async def test_removing_a_mapping_row_flags_a_reresolve_at_boot(
    catalog_ctx: CtxFactory,
) -> None:
    sources = [git_source("public", trust="curated"), git_source("corp")]
    mapping = [{"source": "corp", "external_id": "pay", "catalog_api_id": "stripe.com"}]
    async with catalog_ctx(sources=sources, catalog_api_id_mappings=mapping) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("stripe.com"))
        await sync.sync_source("corp", stream=full("pay", pay="stripe.com"))
        assert (await _rows(ctx, "corp"))["pay"].catalog_api_id == "stripe.com"
        mapped_digest = (await _source(ctx, "corp")).resolver_inputs_digest

    async with catalog_ctx(sources=sources) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()  # boot: the resolver inputs changed
        corp = await _source(ctx, "corp")
        assert corp.reresolve_wanted_at is not None
        stored = (await _rows(ctx, "corp"))["pay"]
        view = build_catalog_entry(
            source_slug="corp",
            external_id="pay",
            catalog_api_id=stored.catalog_api_id,
            hint=stored.catalog_api_id_hint,
            title=None,
            version=None,
        )
        assert (
            exclude_unowned(
                [view],
                curated_sources=frozenset({"public"}),
                curated_vendors=curated_vendors_of({"stripe.com"}),
                mappings=[],
            )
            == []
        )
        await CatalogSourceSyncScanner(ctx).tick()
        assert (await _rows(ctx, "corp"))["pay"].catalog_api_id == "corp~stripe.com"
        corp = await _source(ctx, "corp")
        assert corp.reresolve_wanted_at is None
        assert corp.resolver_inputs_digest not in (None, mapped_digest)


async def test_mapping_applies_curated_casing_at_apply(catalog_ctx: CtxFactory) -> None:
    sources = [git_source("public", trust="curated"), git_source("corp")]
    mapping = [{"source": "corp", "external_id": "pay", "catalog_api_id": "STRIPE.com"}]
    async with catalog_ctx(sources=sources, catalog_api_id_mappings=mapping) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("Stripe.com"))
        await sync.sync_source("corp", stream=full("pay", pay="stripe.com"))
        assert (await _rows(ctx, "corp"))["pay"].catalog_api_id == "Stripe.com"
        assert sync._mappings == [
            CatalogApiIdMapping(source="corp", external_id="pay", catalog_api_id="STRIPE.com")
        ]
