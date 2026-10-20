"""Staged per-source catalog sync against the real registry DB.

The source listing is scripted (no network); everything below it — staging, the
lease, the apply transaction, the guard, tombstones, re-resolve — runs for real.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update
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
from jentic_one.registry.services.catalog.source_ops import CatalogSourceOps
from jentic_one.registry.services.catalog.sources import metrics
from jentic_one.registry.services.catalog.sources import sync as sync_module
from jentic_one.registry.services.catalog.sources.protocol import Removed, Upserted
from jentic_one.registry.services.catalog.sources.scanner import (
    CatalogSourceSyncScanner,
    wake_source_sync_scanner,
)
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync
from jentic_one.shared.auth.permission_catalog import ORG_ADMIN
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


async def test_listing_over_its_timeout_fails_and_releases_the_lease(
    catalog_ctx: CtxFactory,
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        stuck = ScriptedStream([Upserted(entry("a.io"))], gate=asyncio.Event())
        outcome = await sync.sync_source("public", stream=stuck, listing_timeout_s=0.05)
        assert (outcome.status, outcome.error_code) == ("failed", "listing_timeout")
        source = await _source(ctx, "public")
        assert (source.last_status, source.consecutive_failures) == ("failed", 1)
        assert source.sync_lease_owner is None
        assert await _staged_count(ctx) == 0


async def test_listing_timeout_counts_only_time_spent_awaiting_the_listing(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slow staging writes do not eat the listing budget."""
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        real_stage = CatalogSyncRunRepository.stage

        async def _slow_stage(session: Any, batch: Any) -> None:
            await asyncio.sleep(0.2)
            await real_stage(session, batch)

        monkeypatch.setattr(sync_module, "STAGE_BATCH_SIZE", 1)
        monkeypatch.setattr(CatalogSyncRunRepository, "stage", staticmethod(_slow_stage))
        outcome = await sync.sync_source(
            "public", stream=full("a.io", "b.io", "c.io"), listing_timeout_s=0.3
        )
        assert (outcome.status, outcome.upserted) == ("applied", 3)


async def test_apply_outlives_a_cancelled_caller_and_logs_its_outcome(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        entered, release = asyncio.Event(), asyncio.Event()
        real_apply = CatalogSourceSync._apply

        async def _gated_apply(self: CatalogSourceSync, *args: Any, **kwargs: Any) -> Any:
            entered.set()
            await release.wait()
            return await real_apply(self, *args, **kwargs)

        monkeypatch.setattr(CatalogSourceSync, "_apply", _gated_apply)
        recorded: list[tuple[str, str]] = []
        monkeypatch.setattr(
            metrics,
            "record_sync",
            lambda slug, status, _seconds: recorded.append((slug, status)),
        )
        task = asyncio.create_task(sync.sync_source("public", stream=full("a.io")))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with capture_logs() as logs:
            release.set()
            for _ in range(50):
                if recorded:
                    break
                await asyncio.sleep(0.02)
        assert recorded == [("public", "applied")]
        assert any(e["event"] == "catalog_source_detached_apply_done" for e in logs)
        source = await _source(ctx, "public")
        assert (source.last_status, source.sync_lease_owner) == ("ok", None)


async def test_run_cancelled_while_listing_fails_and_releases_the_lease(
    catalog_ctx: CtxFactory,
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        stuck = ScriptedStream([Upserted(entry("a.io"))], gate=asyncio.Event())
        task = asyncio.create_task(sync.sync_source("public", stream=stuck))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        source = await _source(ctx, "public")
        assert (source.last_status, source.consecutive_failures) == ("failed", 1)
        assert source.sync_lease_owner is None
        async with ctx.registry_db.session() as session:
            runs = (await session.execute(select(CatalogSyncRun))).scalars().all()
        assert [(r.status, r.error_code) for r in runs] == [("failed", "cancelled")]


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


async def _steal_lease(ctx: Context, slug: str, owner: str = "csyn_thief") -> None:
    """Another process claims the lease (as after this run's lease lapsed)."""
    async with ctx.registry_db.transaction() as session:
        await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug)
            .values(sync_lease_owner=owner, sync_lease_until=utcnow() + timedelta(minutes=5))
        )


class _StealOnSummary(ScriptedStream):
    """Loses the lease after the listing is staged and before the apply."""

    def __init__(self, ctx: Context, slug: str, changes: list[Any]) -> None:
        super().__init__(changes)
        self._ctx = ctx
        self._slug = slug

    async def summary(self) -> Any:
        await _steal_lease(self._ctx, self._slug)
        return await super().summary()


async def test_apply_rechecks_the_lease_and_a_run_that_lost_it_applies_nothing(
    catalog_ctx: CtxFactory,
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        stream = _StealOnSummary(ctx, "public", [Upserted(entry("a.io"))])
        outcome = await sync.sync_source("public", stream=stream)
        assert (outcome.status, outcome.error_code) == ("failed", "lease_lost")
        assert await _rows(ctx, "public") == {}
        assert await _staged_count(ctx) == 0
        source = await _source(ctx, "public")
        # The new owner's lease and the source's state are left alone.
        assert source.sync_lease_owner == "csyn_thief"
        assert (source.consecutive_failures, source.last_status) == (0, "never_synced")


async def test_heartbeat_that_finds_the_lease_taken_stops_the_listing(
    catalog_ctx: CtxFactory,
) -> None:
    source = git_source("public", trust="curated", sync_lease_seconds=3)
    async with catalog_ctx(sources=[source]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        never = asyncio.Event()  # the listing would hang forever
        run = asyncio.create_task(
            sync.sync_source("public", stream=ScriptedStream([Upserted(entry("a.io"))], gate=never))
        )
        for _ in range(50):
            if (await _source(ctx, "public")).sync_lease_owner is not None:
                break
            await asyncio.sleep(0.02)
        await _steal_lease(ctx, "public")
        outcome = await asyncio.wait_for(run, timeout=5)
        assert (outcome.status, outcome.error_code) == ("failed", "lease_lost")
        assert await _rows(ctx, "public") == {}
        async with ctx.registry_db.session() as session:
            [run_row] = (await session.execute(select(CatalogSyncRun))).scalars().all()
        assert (run_row.status, run_row.error_code) == ("failed", "lease_lost")


async def test_heartbeat_that_cannot_renew_loses_the_lease(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = git_source("public", trust="curated", sync_lease_seconds=3)
    async with catalog_ctx(sources=[source]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()

        async def _broken(*args: Any, **kwargs: Any) -> bool:
            raise ConnectionError("database went away")

        monkeypatch.setattr(CatalogSourceRepository, "renew_lease", _broken)
        never = asyncio.Event()
        stream = ScriptedStream([Upserted(entry("a.io"))], gate=never)
        outcome = await asyncio.wait_for(sync.sync_source("public", stream=stream), timeout=5)
        assert (outcome.status, outcome.error_code) == ("failed", "lease_lost")
        assert await _rows(ctx, "public") == {}


async def test_build_error_counts_as_a_failure_and_backs_off(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with catalog_ctx(
        sources=[git_source("public", trust="curated"), git_source("corp")]
    ) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()

        def _broken(self: CatalogSourceSync, cfg: Any) -> Any:
            raise RuntimeError("bad settings")

        monkeypatch.setattr(CatalogSourceSync, "build_source", _broken)
        outcome = await sync.sync_source("corp")
        assert (outcome.status, outcome.error_code) == ("failed", "build_error")
        corp = await _source(ctx, "corp")
        assert (corp.consecutive_failures, corp.last_status) == (1, "failed")
        assert corp.sync_lease_owner is None
        # Backing off: the scanner does not flag it again straight away (the
        # never-synced "public" is flagged; "corp" is not).
        assert await sync.mark_stale_wanted() == 1
        assert (await _source(ctx, "corp")).sync_wanted_at is None


async def test_apply_that_raises_fails_the_run_in_a_fresh_transaction(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()

        def _broken(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("apply broke")

        monkeypatch.setattr(sync_module, "_upsert_entry", _broken)
        outcome = await sync.sync_source("public", stream=full("a.io"))
        assert (outcome.status, outcome.error_code) == ("failed", "apply_error")
        assert await _rows(ctx, "public") == {}
        assert await _staged_count(ctx) == 0
        source = await _source(ctx, "public")
        assert (source.consecutive_failures, source.sync_lease_owner) == (1, None)


async def test_staging_strips_nul_and_lone_surrogates_everywhere(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        spec_ref = {"url": "https://specs.example/a\x00.json", "extra": [{"k\x00": "v\ud800"}]}
        stream = ScriptedStream(
            [Upserted(entry("a.io", title="Pay\ud83d API", spec_ref=spec_ref, tags=("t\x00",)))]
        )
        assert (await sync.sync_source("public", stream=stream)).status == "applied"
        row = (await _rows(ctx, "public"))["a.io"]
        assert row.spec_ref == {
            "url": "https://specs.example/a.json",
            "extra": [{"k": "v\ufffd"}],
        }
        assert row.title is not None and "\ud83d" not in row.title
        assert row.tags == ["t"]


async def test_regular_apply_of_a_non_curated_source_reports_ownership_conflicts(
    catalog_ctx: CtxFactory,
) -> None:
    sources = [git_source("public", trust="curated"), git_source("corp")]
    async with catalog_ctx(sources=sources) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("stripe.com"))
        # Imported before the curated source owned the vendor; it keeps its id.
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
        with capture_logs() as logs:
            outcome = await sync.sync_source("corp", stream=full("pay", pay="stripe.com"))
        assert outcome.status == "applied"
        [conflict] = [e for e in logs if e["event"] == "catalog_ownership_conflict"]
        assert (conflict["source"], conflict["entry_id"]) == ("corp", "corp/pay")
        assert (await _rows(ctx, "corp"))["pay"].catalog_api_id == "corp~stripe.com"

    mapping = [{"source": "corp", "external_id": "pay", "catalog_api_id": "stripe.com"}]
    async with catalog_ctx(sources=sources, catalog_api_id_mappings=mapping) as ctx:
        with capture_logs() as logs:
            outcome = await CatalogSourceSync(ctx).sync_source(
                "corp", stream=full("pay", pay="stripe.com")
            )
        assert outcome.status == "applied"
        assert not [e for e in logs if e["event"] == "catalog_ownership_conflict"]


async def test_a_source_that_becomes_curated_re_resolves_its_own_rows(
    catalog_ctx: CtxFactory,
) -> None:
    before = [git_source("public", trust="curated"), git_source("corp")]
    async with catalog_ctx(sources=before) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("stripe.com"))
        await sync.sync_source("corp", stream=full("pay", pay="stripe.com"))
        assert (await _rows(ctx, "corp"))["pay"].catalog_api_id == "corp~stripe.com"

    after = [git_source("public", trust="curated"), git_source("corp", trust="curated")]
    async with catalog_ctx(sources=after) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        assert (await _source(ctx, "corp")).reresolve_wanted_at is not None
        assert await sync.reresolve_source("corp") == 1
        assert (await _rows(ctx, "corp"))["pay"].catalog_api_id == "stripe.com"


async def test_apply_and_re_resolve_of_one_source_serialise(catalog_ctx: CtxFactory) -> None:
    sources = [git_source("public", trust="curated"), git_source("corp")]
    async with catalog_ctx(sources=sources) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("corp", stream=full(*(f"e{i}" for i in range(20))))
        apply = asyncio.create_task(
            CatalogSourceSync(ctx).sync_source("corp", stream=full(*(f"e{i}" for i in range(20))))
        )
        await asyncio.gather(*(CatalogSourceSync(ctx).reresolve_source("corp") for _ in range(3)))
        assert (await apply).status == "applied"
        assert len(await _rows(ctx, "corp")) == 20


async def test_concurrent_boots_register_each_source_once(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        await asyncio.gather(*(CatalogSourceSync(ctx).register_sources() for _ in range(4)))
        async with ctx.registry_db.session() as session:
            rows = (await session.execute(select(CatalogSourceRow))).scalars().all()
        assert [r.slug for r in rows] == ["public"]


async def test_run_start_time_comes_from_the_database_clock_on_postgres(
    catalog_ctx: CtxFactory,
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        if ctx.registry_db.backend.dialect_name == "sqlite":
            pytest.skip("SQLite has one process and uses the Python clock")
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        assert (await sync.sync_source("public", stream=full("a.io"))).status == "applied"
        async with ctx.registry_db.session() as session:
            [run] = (await session.execute(select(CatalogSyncRun))).scalars().all()
            db_now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        row = (await _rows(ctx, "public"))["a.io"]
        assert row.seen_at == run.started_at
        assert abs((db_now - run.started_at).total_seconds()) < 30


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
        assert row.title is not None and len(row.title) == 120
        assert row.title.startswith("Payments ")
        assert row.title.endswith("…")
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
    sources = [git_source("public", trust="curated"), git_source("corp")]
    async with catalog_ctx(sources=sources) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        # The legacy source is flagged like any other.
        assert await sync.mark_stale_wanted() == 2
        for slug in ("public", "corp"):
            flagged = await _source(ctx, slug)
            assert flagged.sync_wanted_at is not None
            assert flagged.sync_lease_owner is None  # flagging starts no sync

        monkeypatch.setattr(CatalogSourceSync, "build_source", _scripted_source(full("a.io")))
        outcomes = await CatalogSourceSyncScanner(ctx).tick()
        assert sorted((o.slug, o.status) for o in outcomes) == [
            ("corp", "applied"),
            ("public", "applied"),
        ]
        for slug in ("public", "corp"):
            drained = await _source(ctx, slug)
            assert drained.sync_wanted_at is None
            assert drained.last_sync_at is not None
        assert await sync.mark_stale_wanted() == 0


async def test_scanner_flags_the_legacy_source_on_max_age(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("a.io"))
        async with ctx.registry_db.session() as session:
            await session.execute(
                update(CatalogSourceRow).values(last_sync_at=utcnow() - timedelta(days=30))
            )
            await session.commit()
        built: list[str] = []

        def _build(self: CatalogSourceSync, cfg: Any) -> Any:
            built.append(cfg.slug)
            return _scripted_source(full())(self, cfg)

        monkeypatch.setattr(CatalogSourceSync, "build_source", _build)
        assert await sync.mark_stale_wanted() == 1
        assert [o.slug for o in await CatalogSourceSyncScanner(ctx).tick()] == ["public"]
        assert built == ["public"]
        assert (await _source(ctx, "public")).sync_wanted_at is None


async def test_sync_all_bounds_each_listing_so_a_stuck_source_never_stalls_the_rest(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = [git_source("public", trust="curated"), git_source("corp")]
    async with catalog_ctx(sources=sources) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        streams = {
            "public": ScriptedStream([Upserted(entry("a.io"))], gate=asyncio.Event()),
            "corp": full("b.io"),
        }
        monkeypatch.setattr(
            CatalogSourceSync,
            "build_source",
            lambda self, cfg: _scripted_source(streams[cfg.slug])(self, cfg),
        )
        outcomes = await asyncio.wait_for(sync.sync_all(listing_timeout_s=0.05), timeout=5)
        assert [(o.slug, o.status, o.error_code) for o in outcomes] == [
            ("public", "failed", "listing_timeout"),
            ("corp", "applied", None),
        ]


async def test_max_age_zero_source_is_never_flagged(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(
        sources=[git_source("public", trust="curated", max_age_seconds=0)]
    ) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        assert await sync.mark_stale_wanted() == 0
        assert await CatalogSourceSyncScanner(ctx).tick() == []


# ── abandoned runs and sync history ───────────────────────────────────────────


class _SweepOnSummary(ScriptedStream):
    """Ages this run's staging past the sweep cutoff and sweeps, before the apply."""

    def __init__(self, sync: CatalogSourceSync, changes: list[Any]) -> None:
        super().__init__(changes)
        self._sync = sync

    async def summary(self) -> Any:
        ctx = self._sync.ctx
        long_ago = utcnow() - timedelta(hours=3)
        async with ctx.registry_db.transaction() as session:
            await session.execute(update(CatalogStagedEntry).values(created_at=long_ago))
            await session.execute(
                update(CatalogSyncRun)
                .where(CatalogSyncRun.status == "running")
                .values(started_at=long_ago)
            )
        await self._sync.sweep_abandoned_staging()
        return await super().summary()


async def test_sweep_spares_a_long_running_run_that_holds_its_lease(
    catalog_ctx: CtxFactory,
) -> None:
    """A run staging for longer than the cutoff keeps its rows, so its full apply
    never tombstones entries the sweep took away."""
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        await sync.sync_source("public", stream=full("a.io", "b.io", "c.io"))
        stream = _SweepOnSummary(sync, [Upserted(entry(e)) for e in ("a.io", "b.io", "c.io")])
        outcome = await sync.sync_source("public", stream=stream)
        assert (outcome.status, outcome.upserted, outcome.removed) == ("applied", 3, 0)
        assert set(await _rows(ctx, "public")) == {"a.io", "b.io", "c.io"}
        async with ctx.registry_db.session() as session:
            run = await session.get(CatalogSyncRun, outcome.run_id)
        assert run is not None and (run.status, run.error_code) == ("applied", None)


async def test_sweep_closes_a_dead_run_and_drops_its_staging(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        dead, recent = generate_ksuid("csyn"), generate_ksuid("csyn")
        long_ago = utcnow() - timedelta(hours=3)
        async with ctx.registry_db.transaction() as session:
            # Neither run holds the lease: ``dead`` crashed long ago, ``recent``
            # is still inside the cutoff.
            for run_id, started in ((dead, long_ago), (recent, utcnow())):
                await CatalogSyncRunRepository.create(
                    session, run_id=run_id, slug="public", started_at=started, forced=False
                )
                session.add(
                    CatalogStagedEntry(
                        sync_run_id=run_id,
                        op="upsert",
                        external_id="a.io",
                        tags=[],
                        created_at=started,
                    )
                )
        assert await sync.sweep_abandoned_staging() == 1
        async with ctx.registry_db.session() as session:
            dead_row = await session.get(CatalogSyncRun, dead)
            recent_row = await session.get(CatalogSyncRun, recent)
            staged = (await session.execute(select(CatalogStagedEntry.sync_run_id))).scalars()
            assert set(staged) == {recent}
        assert dead_row is not None
        assert (dead_row.status, dead_row.error_code) == ("failed", "abandoned")
        assert dead_row.finished_at is not None
        assert recent_row is not None and recent_row.status == "running"


async def test_sweep_clears_a_long_expired_lease_and_drops_its_dead_run(
    catalog_ctx: CtxFactory,
) -> None:
    """A dead run on a source nothing syncs again (disabled here) still loses its
    lease, staging and ``running`` row; a lease that only just expired is kept."""
    sources = [git_source("public", trust="curated", enabled=False), git_source("corp")]
    async with catalog_ctx(sources=sources) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        dead, lapsing = generate_ksuid("csyn"), generate_ksuid("csyn")
        long_ago = utcnow() - timedelta(hours=3)
        async with ctx.registry_db.transaction() as session:
            for run_id, slug, lease_until in (
                (dead, "public", long_ago),
                (lapsing, "corp", utcnow() - timedelta(minutes=5)),
            ):
                await session.execute(
                    update(CatalogSourceRow)
                    .where(CatalogSourceRow.slug == slug)
                    .values(sync_lease_owner=run_id, sync_lease_until=lease_until)
                )
                await CatalogSyncRunRepository.create(
                    session, run_id=run_id, slug=slug, started_at=long_ago, forced=False
                )
                session.add(
                    CatalogStagedEntry(
                        sync_run_id=run_id,
                        op="upsert",
                        external_id="a.io",
                        tags=[],
                        created_at=long_ago,
                    )
                )
        assert await sync.sweep_abandoned_staging() == 1
        public, corp = await _source(ctx, "public"), await _source(ctx, "corp")
        assert (public.sync_lease_owner, public.sync_lease_until) == (None, None)
        assert corp.sync_lease_owner == lapsing
        async with ctx.registry_db.session() as session:
            dead_row = await session.get(CatalogSyncRun, dead)
            lapsing_row = await session.get(CatalogSyncRun, lapsing)
            staged = (await session.execute(select(CatalogStagedEntry.sync_run_id))).scalars()
            assert set(staged) == {lapsing}
        assert dead_row is not None
        assert (dead_row.status, dead_row.error_code) == ("failed", "abandoned")
        assert lapsing_row is not None and lapsing_row.status == "running"


async def test_sweep_prunes_old_runs_but_keeps_the_last_applied_one(
    catalog_ctx: CtxFactory,
) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        for _ in range(3):
            await sync.sync_source("public", stream=full("a.io"))
        async with ctx.registry_db.transaction() as session:
            await session.execute(
                update(CatalogSyncRun).values(finished_at=utcnow() - timedelta(days=40))
            )
        await sync.sweep_abandoned_staging(run_retention=timedelta(days=30))
        last_applied = (await _source(ctx, "public")).last_applied_run_id
        async with ctx.registry_db.session() as session:
            kept = (await session.execute(select(CatalogSyncRun.id))).scalars().all()
        assert kept == [last_applied]
        # A later run is still ordered against the kept one.
        assert (await sync.sync_source("public", stream=full("a.io"))).status == "applied"


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
        assert conflict["catalog_api_id"] == "stripe.com"
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


# ── requested syncs (``:sync`` / ``:notify``) ─────────────────────────────────


async def test_requested_sync_waits_for_the_run_holding_the_lease(
    catalog_ctx: CtxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request made mid-run is answered by a run that starts after it, never inline."""
    async with catalog_ctx(
        sources=[git_source("public", trust="curated", max_age_seconds=0)]
    ) as ctx:
        await CatalogSourceSync(ctx).register_sources()
        async with ctx.registry_db.transaction() as session:
            assert await CatalogSourceRepository.claim_lease(
                session, slug="public", owner="csyn_running", lease_seconds=60
            )
            await CatalogSourceRepository.request_sync(
                session, slug="public", due_at=None, forced=True
            )
        async with ctx.registry_db.transaction() as session:
            assert await CatalogSourceRepository.promote_due_syncs(session) == []
        held = await _source(ctx, "public")
        assert (held.sync_due_at is not None, held.sync_wanted_at) == (True, None)

        async with ctx.registry_db.transaction() as session:
            await CatalogSourceRepository.release_lease(
                session, slug="public", owner="csyn_running"
            )
        monkeypatch.setattr(CatalogSourceSync, "build_source", _scripted_source(full("a.io")))
        outcomes = await CatalogSourceSyncScanner(ctx).tick()
        assert [(o.slug, o.status) for o in outcomes] == [("public", "applied")]
        drained = await _source(ctx, "public")
        assert (drained.sync_due_at, drained.sync_wanted_at, drained.sync_forced_at) == (
            None,
            None,
            None,
        )
        async with ctx.registry_db.session() as session:
            forced = (await session.execute(select(CatalogSyncRun.forced))).scalars().all()
        assert forced == [True]


async def test_a_later_request_never_postpones_an_earlier_one(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        await CatalogSourceSync(ctx).register_sources()
        soon = utcnow() + timedelta(seconds=30)
        async with ctx.registry_db.transaction() as session:
            await CatalogSourceRepository.request_sync(
                session, slug="public", due_at=soon, forced=False
            )
            await CatalogSourceRepository.request_sync(
                session, slug="public", due_at=soon + timedelta(minutes=5), forced=False
            )
            due = await CatalogSourceRepository.sync_due_at(session, "public")
            assert await CatalogSourceRepository.promote_due_syncs(session) == []
        assert due is not None
        assert abs((due - soon).total_seconds()) < 1
        async with ctx.registry_db.session() as session:
            assert await CatalogSourceRepository.next_sync_due_at(session) == due


async def test_a_request_blocked_by_a_held_lease_waits_for_the_lease(
    catalog_ctx: CtxFactory,
) -> None:
    """A due request on a leased source counts from the lease's end, not 1 Hz polling."""
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        await CatalogSourceSync(ctx).register_sources()
        async with ctx.registry_db.transaction() as session:
            assert await CatalogSourceRepository.claim_lease(
                session, slug="public", owner="other-run", lease_seconds=600
            )
            await CatalogSourceRepository.request_sync(
                session, slug="public", due_at=utcnow(), forced=False
            )
        async with ctx.registry_db.session() as session:
            start = await CatalogSourceRepository.next_sync_due_at(session)
        assert start is not None
        assert (start - utcnow()).total_seconds() > 590
        scanner = CatalogSourceSyncScanner(ctx, tick_seconds=60)
        assert await scanner._next_wait() == 60


async def test_sync_requests_within_the_min_interval_wait_for_it(
    catalog_ctx: CtxFactory,
) -> None:
    """A :sync right after a run waits out ``min_sync_interval_seconds``; force does not."""
    source = git_source("public", trust="curated", min_sync_interval_seconds=30)
    async with catalog_ctx(sources=[source]) as ctx:
        await CatalogSourceSync(ctx).register_sources()
        started = utcnow()
        async with ctx.registry_db.transaction() as session:
            await CatalogSyncRunRepository.create(
                session,
                run_id=generate_ksuid("csyn"),
                slug="public",
                started_at=started,
                forced=False,
            )
        ops = CatalogSourceOps(ctx)
        perms = frozenset({ORG_ADMIN})
        held = await ops.request_sync("public", permissions=perms, actor_id="usr_admin")
        assert held.due_at is not None
        assert abs((held.due_at - (started + timedelta(seconds=30))).total_seconds()) < 1
        again = await ops.request_sync("public", permissions=perms, actor_id="usr_admin")
        assert again.status == "coalesced" and again.due_at == held.due_at
        forced = await ops.request_sync(
            "public", permissions=perms, actor_id="usr_admin", force=True
        )
        assert forced.due_at is not None and forced.due_at < held.due_at


async def test_wake_source_sync_scanner_cuts_the_wait_short(catalog_ctx: CtxFactory) -> None:
    async with catalog_ctx(sources=[git_source("public", trust="curated")]) as ctx:
        scanner = CatalogSourceSyncScanner(ctx, tick_seconds=3600)
        ticks = asyncio.Event()

        async def _tick() -> list[Any]:
            ticks.set()
            scanner.stop()
            return []

        scanner.tick = _tick  # type: ignore[method-assign]
        scanner.boot = AsyncMock()  # type: ignore[method-assign]
        task = asyncio.create_task(scanner.run())
        await asyncio.sleep(0.05)
        assert not ticks.is_set()
        wake_source_sync_scanner(ctx)
        await asyncio.wait_for(ticks.wait(), timeout=5)
        await asyncio.wait_for(task, timeout=5)
