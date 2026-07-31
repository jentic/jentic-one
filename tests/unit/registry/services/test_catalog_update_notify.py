"""Unit tests for the catalog update-notify sweep (Flow 3 MVP).

Covers ``CatalogService._run_update_notify_sweep`` / ``_probe_one`` behaviour with
mocked fetch + repos: the interval gate, the 304 no-op, the change-emits-once
dedupe, the no-change no-op, and the per-API best-effort isolation.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.repos.revision_repo import RegisteredSpec
from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog.fetch import ConditionalFetch
from jentic_one.registry.services.catalog.service import CatalogService, _is_upstream_tracked
from jentic_one.registry.services.errors import CatalogUnavailableError
from jentic_one.shared.models.events import EventType

_API_ID = uuid.uuid4()


def _make_ctx(*, interval: int = 86400) -> MagicMock:
    ctx = MagicMock()
    ctx.config.catalog.update_check_interval_seconds = interval
    ctx.config.catalog.manifest_max_age_seconds = 300
    ctx.config.catalog.manifest_url = "https://example.com/apis.json"
    ctx.config.catalog.update_sweep_deadline_seconds = 300
    ctx.config.catalog.update_sweep_max_concurrency = 4
    ctx.config.ingest = MagicMock()
    ctx.update_sweep_lock = asyncio.Lock()
    session = AsyncMock()
    for db in (ctx.registry_db, ctx.admin_db):
        db.transaction.return_value.__aenter__ = AsyncMock(return_value=session)
        db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        db.session.return_value.__aenter__ = AsyncMock(return_value=session)
        db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _spec(digest: str | None = "local-digest", *, origin: str | None = "catalog") -> RegisteredSpec:
    return RegisteredSpec(
        api_id=_API_ID,
        source_url="https://raw.githubusercontent.com/x/y/main/openapi.json",
        spec_digest=digest,
        vendor="acme",
        name="widgets",
        version="1.0.0",
        origin=origin,
    )


def _check(
    *, last_checked_at: datetime | None, etag: str | None, notified: str | None
) -> MagicMock:
    row = MagicMock()
    row.last_checked_at = last_checked_at
    row.last_seen_etag = etag
    row.last_notified_digest = notified
    return row


_SWEEP = "jentic_one.registry.services.catalog.service"


@pytest.mark.asyncio
async def test_sweep_disabled_when_interval_zero() -> None:
    svc = CatalogService(_make_ctx(interval=0))
    with patch(f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify") as specs:
        await svc._run_update_notify_sweep()
        specs.assert_not_called()


@pytest.mark.asyncio
async def test_probe_skips_within_interval() -> None:
    """A recently-checked API is not re-probed until the interval elapses."""
    svc = CatalogService(_make_ctx(interval=86400))
    recent = datetime.now(UTC) - timedelta(seconds=10)
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            return_value=_check(last_checked_at=recent, etag='"v1"', notified=None),
        ),
        patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock) as fetch,
        patch(f"{_SWEEP}.emit_event_best_effort", new_callable=AsyncMock) as emit,
    ):
        await svc._probe_one(_spec(), now=datetime.now(UTC), interval=86400)
        fetch.assert_not_called()
        emit.assert_not_called()


@pytest.mark.asyncio
async def test_probe_at_interval_boundary_re_probes() -> None:
    """At exactly the interval boundary the API is re-probed (strict age < interval)."""
    svc = CatalogService(_make_ctx(interval=100))
    now = datetime.now(UTC)
    checked = now - timedelta(seconds=100)  # age == interval → not < interval → probe
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            return_value=_check(last_checked_at=checked, etag='"v1"', notified=None),
        ),
        patch(
            f"{_SWEEP}.fetch_bytes_conditional",
            new_callable=AsyncMock,
            return_value=ConditionalFetch(
                not_modified=True, etag='"v1"', content=None, digest=None
            ),
        ) as fetch,
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event_best_effort", new_callable=AsyncMock),
    ):
        await svc._probe_one(_spec(), now=now, interval=100)
        fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_304_is_noop_but_records_check() -> None:
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get", new_callable=AsyncMock, return_value=None
        ),
        patch(
            f"{_SWEEP}.fetch_bytes_conditional",
            new_callable=AsyncMock,
            return_value=ConditionalFetch(
                not_modified=True, etag='"v1"', content=None, digest=None
            ),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock) as upsert,
        patch(f"{_SWEEP}.emit_event_best_effort", new_callable=AsyncMock) as emit,
    ):
        await svc._probe_one(_spec(), now=datetime.now(UTC), interval=86400)
        emit.assert_not_called()
        upsert.assert_awaited_once()
        upsert_kwargs = upsert.await_args.kwargs if upsert.await_args else {}
        assert upsert_kwargs.get("notified_digest") is None


@pytest.mark.asyncio
async def test_probe_change_emits_event_once() -> None:
    """A digest differing from both the registered and last-notified emits + records it."""
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get", new_callable=AsyncMock, return_value=None
        ),
        patch(
            f"{_SWEEP}.fetch_bytes_conditional",
            new_callable=AsyncMock,
            return_value=ConditionalFetch(
                not_modified=False, etag='"v2"', content=b"{}", digest="upstream-new"
            ),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock) as upsert,
        patch(f"{_SWEEP}.emit_event_best_effort", new_callable=AsyncMock) as emit,
    ):
        await svc._probe_one(_spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
        emit.assert_awaited_once()
        emit_kwargs = emit.await_args.kwargs if emit.await_args else {}
        assert emit_kwargs["type"] == EventType.CATALOG_UPDATE_AVAILABLE
        assert emit_kwargs["requires_action"] is True
        assert emit_kwargs["data"]["upstream_digest"] == "upstream-new"
        upsert_kwargs = upsert.await_args.kwargs if upsert.await_args else {}
        assert upsert_kwargs["notified_digest"] == "upstream-new"


@pytest.mark.asyncio
async def test_probe_already_notified_digest_does_not_re_emit() -> None:
    """Dedupe: the same upstream digest we already notified for stays silent."""
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            return_value=_check(last_checked_at=None, etag=None, notified="upstream-new"),
        ),
        patch(
            f"{_SWEEP}.fetch_bytes_conditional",
            new_callable=AsyncMock,
            return_value=ConditionalFetch(
                not_modified=False, etag='"v2"', content=b"{}", digest="upstream-new"
            ),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event_best_effort", new_callable=AsyncMock) as emit,
    ):
        await svc._probe_one(_spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
        emit.assert_not_called()


@pytest.mark.asyncio
async def test_probe_upstream_matches_registered_is_noop() -> None:
    """Upstream digest equal to the registered revision's digest → no event."""
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get", new_callable=AsyncMock, return_value=None
        ),
        patch(
            f"{_SWEEP}.fetch_bytes_conditional",
            new_callable=AsyncMock,
            return_value=ConditionalFetch(
                not_modified=False, etag='"v1"', content=b"{}", digest="local-digest"
            ),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event_best_effort", new_callable=AsyncMock) as emit,
    ):
        await svc._probe_one(_spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
        emit.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_isolates_per_api_failures() -> None:
    """A probe that raises for one API does not abort the rest of the sweep."""
    svc = CatalogService(_make_ctx())
    specs = [_spec(), _spec(digest="other")]
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=specs,
        ),
        patch.object(svc, "_probe_one", new_callable=AsyncMock) as probe,
    ):
        probe.side_effect = [RuntimeError("boom"), None]
        await svc._run_update_notify_sweep()
        assert probe.await_count == 2


def test_catalog_update_event_in_all_frozenset() -> None:
    assert EventType.CATALOG_UPDATE_AVAILABLE in EventType.ALL


@pytest.mark.asyncio
async def test_sweep_no_candidates_is_noop() -> None:
    """Empty registered-spec set → no fetch, no emit, clean early return."""
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock) as fetch,
    ):
        await svc._run_update_notify_sweep()
        fetch.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_skips_when_lock_already_held() -> None:
    """A sweep in flight (lock held) makes a concurrent trigger skip, not double-run.

    Guards the in-process double-emit fix: the scanner and the read-path trigger share
    ``Context.update_sweep_lock``; the second one to start must bail before touching the DB.
    """
    ctx = _make_ctx()
    svc = CatalogService(ctx)
    await ctx.update_sweep_lock.acquire()  # simulate an in-flight sweep
    try:
        with patch(f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify") as specs:
            await svc._run_update_notify_sweep()
            specs.assert_not_called()
    finally:
        ctx.update_sweep_lock.release()


@pytest.mark.asyncio
async def test_sweep_swallows_emit_failure_per_api() -> None:
    """A hard failure in a probe's emit path is isolated by the sweep, not propagated."""
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[_spec(digest="local-digest")],
        ),
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get", new_callable=AsyncMock, return_value=None
        ),
        patch(
            f"{_SWEEP}.fetch_bytes_conditional",
            new_callable=AsyncMock,
            return_value=ConditionalFetch(
                not_modified=False, etag='"v2"', content=b"{}", digest="upstream-new"
            ),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(
            f"{_SWEEP}.emit_event_best_effort",
            new_callable=AsyncMock,
            side_effect=RuntimeError("emit boom"),
        ),
    ):
        # Must complete without raising — the sweep-level guard isolates the failure.
        await svc._run_update_notify_sweep()


@pytest.mark.asyncio
async def test_safe_refresh_runs_sweep_after_successful_refresh() -> None:
    """_safe_refresh spawns the update-notify sweep once the refresh succeeds."""
    svc = CatalogService(_make_ctx(interval=86400))
    with (
        patch(
            f"{_SWEEP}.CatalogRepository.try_acquire_refresh_lock",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(f"{_SWEEP}.CatalogRepository.fetched_at", new_callable=AsyncMock, return_value=None),
        patch.object(svc, "refresh", new_callable=AsyncMock),
        patch.object(svc, "trigger_update_notify_sweep") as spawn,
    ):
        await svc._safe_refresh()
        spawn.assert_called_once()


@pytest.mark.asyncio
async def test_safe_refresh_skips_sweep_when_refresh_fails() -> None:
    """A CatalogUnavailableError short-circuits before the sweep runs."""
    svc = CatalogService(_make_ctx(interval=86400))
    with (
        patch(
            f"{_SWEEP}.CatalogRepository.try_acquire_refresh_lock",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(f"{_SWEEP}.CatalogRepository.fetched_at", new_callable=AsyncMock, return_value=None),
        patch.object(
            svc, "refresh", new_callable=AsyncMock, side_effect=CatalogUnavailableError("x")
        ),
        patch.object(svc, "trigger_update_notify_sweep") as spawn,
    ):
        await svc._safe_refresh()
        spawn.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_sweep_detaches_and_runs_task() -> None:
    """The spawn seam runs the sweep as a background task (not awaited inline)."""
    svc = CatalogService(_make_ctx(interval=86400))
    ran = asyncio.Event()

    async def _fake_sweep() -> None:
        ran.set()

    with patch.object(svc, "_run_update_notify_sweep", side_effect=_fake_sweep):
        svc.trigger_update_notify_sweep()
        # Not yet awaited — the coroutine only runs once we yield to the loop.
        await asyncio.wait_for(ran.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_spawn_sweep_noops_when_disabled() -> None:
    """The kill switch (interval<=0) means no task is ever spawned."""
    svc = CatalogService(_make_ctx(interval=0))
    with patch.object(svc, "_run_update_notify_sweep", new_callable=AsyncMock) as run:
        svc.trigger_update_notify_sweep()
        await asyncio.sleep(0)  # give any (wrongly) spawned task a chance to run
        run.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_deadline_hit_is_swallowed() -> None:
    """A sweep exceeding the wall-clock budget logs and returns, never raises."""
    ctx = _make_ctx(interval=86400)
    ctx.config.catalog.update_sweep_deadline_seconds = 0  # trip the deadline immediately
    svc = CatalogService(ctx)

    async def _slow(_spec_arg: object, *, now: object, interval: int) -> None:
        await asyncio.sleep(0.2)

    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[_spec()],
        ),
        patch.object(svc, "_probe_one", side_effect=_slow),
    ):
        # Must not raise despite the probe outrunning the deadline.
        await svc._run_update_notify_sweep()


@pytest.mark.asyncio
async def test_sweep_bounds_concurrency() -> None:
    """No more than update_sweep_max_concurrency probes run at once."""
    ctx = _make_ctx(interval=86400)
    ctx.config.catalog.update_sweep_max_concurrency = 2
    svc = CatalogService(ctx)
    specs = [
        RegisteredSpec(
            api_id=uuid.uuid4(),
            source_url=f"https://raw.githubusercontent.com/x/y/main/{i}.json",
            spec_digest="d",
            vendor="acme",
            name="widgets",
            version="1.0.0",
            origin="catalog",
        )
        for i in range(6)
    ]
    in_flight = 0
    peak = 0

    async def _probe(_spec_arg: object, *, now: object, interval: int) -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1

    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=specs,
        ),
        patch.object(svc, "_probe_one", side_effect=_probe),
    ):
        await svc._run_update_notify_sweep()

    assert peak <= 2


# ── origin-scoped candidate selection (Phase 4, OQ-3) ────────────────────────


def test_is_upstream_tracked_catalog_origin_always() -> None:
    spec = _spec(origin="catalog")
    assert _is_upstream_tracked(spec, set()) is True


def test_is_upstream_tracked_overlay_only_when_source_in_manifest() -> None:
    spec = _spec(origin="overlay")
    assert _is_upstream_tracked(spec, set()) is False
    assert _is_upstream_tracked(spec, {spec.source_url}) is True


def test_is_upstream_tracked_manual_only_when_source_in_manifest() -> None:
    spec = _spec(origin=None)
    assert _is_upstream_tracked(spec, set()) is False
    assert _is_upstream_tracked(spec, {spec.source_url}) is True


def test_is_upstream_tracked_other_origin_skipped() -> None:
    spec = _spec(origin="imported")
    assert _is_upstream_tracked(spec, {spec.source_url}) is False


@pytest.mark.asyncio
async def test_sweep_skips_manual_spec_not_in_manifest() -> None:
    """A manual import whose source_url is not a catalog entry is never probed."""
    ctx = _make_ctx(interval=86400)
    svc = CatalogService(ctx)
    manual = _spec(origin=None)
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[manual],
        ),
        patch(
            f"{_SWEEP}.CatalogRepository.manifest_spec_urls",
            new_callable=AsyncMock,
            return_value=set(),  # not in the manifest → skipped
        ),
        patch.object(svc, "_probe_one", new_callable=AsyncMock) as probe,
    ):
        await svc._run_update_notify_sweep()
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_probes_overlay_spec_in_manifest() -> None:
    """An overlay-origin revision whose source_url is a catalog entry is probed."""
    ctx = _make_ctx(interval=86400)
    svc = CatalogService(ctx)
    overlay = _spec(origin="overlay")
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[overlay],
        ),
        patch(
            f"{_SWEEP}.CatalogRepository.manifest_spec_urls",
            new_callable=AsyncMock,
            return_value={overlay.source_url},
        ),
        patch.object(svc, "_probe_one", new_callable=AsyncMock) as probe,
    ):
        await svc._run_update_notify_sweep()
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_skips_manifest_query_for_pure_catalog() -> None:
    """No manifest coverage query when every candidate is catalog-origin (common case)."""
    ctx = _make_ctx(interval=86400)
    svc = CatalogService(ctx)
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[_spec(origin="catalog")],
        ),
        patch(
            f"{_SWEEP}.CatalogRepository.manifest_spec_urls",
            new_callable=AsyncMock,
        ) as spec_urls,
        patch.object(svc, "_probe_one", new_callable=AsyncMock),
    ):
        await svc._run_update_notify_sweep()
    spec_urls.assert_not_called()


# ── catalog entry view: update_available derivation (Phase 4) ────────────────


def _entry(
    spec_url: str = "https://raw.githubusercontent.com/x/y/main/openapi.json",
) -> mb.ManifestEntry:
    return mb.ManifestEntry.from_dict(
        {"api_id": "acme.com", "vendor": "acme.com", "path": "apis/acme", "spec_url": spec_url}
    )


def test_to_view_update_available_only_when_registered_and_outdated() -> None:
    entry = _entry()
    url = entry.spec_url or ""
    # Registered + outdated → True.
    v = CatalogService._to_view(entry, {url}, {url})
    assert v.registered is True
    assert v.update_available is True


def test_to_view_not_outdated_when_not_registered() -> None:
    entry = _entry()
    url = entry.spec_url or ""
    # In the outdated set but NOT registered locally → never update_available.
    v = CatalogService._to_view(entry, set(), {url})
    assert v.registered is False
    assert v.update_available is False


def test_to_view_registered_not_outdated() -> None:
    entry = _entry()
    url = entry.spec_url or ""
    v = CatalogService._to_view(entry, {url}, set())
    assert v.registered is True
    assert v.update_available is False


def test_to_view_defaults_when_outdated_set_omitted() -> None:
    entry = _entry()
    url = entry.spec_url or ""
    v = CatalogService._to_view(entry, {url})
    assert v.update_available is False
