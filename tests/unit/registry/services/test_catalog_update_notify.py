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


def _spec(
    digest: str | None = "local-digest",
    *,
    origin: str | None = "catalog",
    overlay_base_digest: str | None = None,
) -> RegisteredSpec:
    return RegisteredSpec(
        api_id=_API_ID,
        source_url="https://raw.githubusercontent.com/x/y/main/openapi.json",
        spec_digest=digest,
        vendor="acme",
        name="widgets",
        version="1.0.0",
        origin=origin,
        overlay_base_digest=overlay_base_digest,
    )


def _check(
    *,
    last_checked_at: datetime | None,
    etag: str | None,
    notified: str | None,
    notified_class: str | None = EventType.CATALOG_UPDATE_AVAILABLE,
) -> MagicMock:
    row = MagicMock()
    row.last_checked_at = last_checked_at
    row.last_seen_etag = etag
    row.last_notified_digest = notified
    # Default to the plain-update class so a "already notified this digest" row dedupes
    # against a plain update (the common case). Tests exercising the overlay-conflict
    # class pass notified_class explicitly.
    row.last_notified_event_class = notified_class if notified is not None else None
    # C1 snooze fields default to "not snoozed" — a bare MagicMock attr is a truthy mock,
    # which would make _is_snoozed spuriously suppress emits in unrelated tests.
    row.snoozed_digest = None
    row.snoozed_until = None
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
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
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
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock),
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
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
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
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified", new_callable=AsyncMock
        ) as mark_notified,
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await svc._probe_one(_spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
        emit.assert_awaited_once()
        emit_kwargs = emit.await_args.kwargs if emit.await_args else {}
        assert emit_kwargs["type"] == EventType.CATALOG_UPDATE_AVAILABLE
        assert emit_kwargs["requires_action"] is True
        assert emit_kwargs["data"]["upstream_digest"] == "upstream-new"
        # #941: the notify marker is written *after* the emit (emit-then-mark).
        mark_notified.assert_awaited_once()
        mark_kwargs = mark_notified.await_args.kwargs if mark_notified.await_args else {}
        assert mark_kwargs["notified_digest"] == "upstream-new"


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
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
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
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await svc._probe_one(_spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
        emit.assert_not_called()


@pytest.mark.asyncio
async def test_probe_overlay_conflict_emits_conflict_class() -> None:
    """Overlay-origin served revision + upstream ≠ overlay base → conflict class.

    The served spec was produced by materializing an overlay over a base whose digest
    we recorded (``overlay_base_digest``). When upstream now differs from that base,
    adopting it would supersede the overlay, so the sweep classifies the change as
    ``CATALOG_UPDATE_CONFLICTS_OVERLAY`` (an operator decision), not a routine update.
    """
    svc = CatalogService(_make_ctx())
    overlay = MagicMock()
    overlay.id = "ovr_live"
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
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified", new_callable=AsyncMock
        ) as mark_notified,
        patch(
            f"{_SWEEP}.OverlayRepository.get_live_confirmed_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        # Overlaid served digest is "overlaid"; the base it was built over is "base-old".
        spec = _spec(digest="overlaid", origin="overlay", overlay_base_digest="base-old")
        await svc._probe_one(spec, now=datetime.now(UTC), interval=86400)
        emit.assert_awaited_once()
        emit_kwargs = emit.await_args.kwargs if emit.await_args else {}
        assert emit_kwargs["type"] == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY
        assert emit_kwargs["data"]["event_class"] == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY
        assert emit_kwargs["data"]["overlay_base_digest"] == "base-old"
        # The conflict event deep-links to the live overlay so a UI/CLI can keep/rollback.
        assert emit_kwargs["data"]["overlay_id"] == "ovr_live"
        # L1 (#920): the conflict carries a structured "why" — the three digests that pin
        # the collision (base the overlay was built on, served, diverged upstream).
        conflict = emit_kwargs["data"]["conflict"]
        assert conflict == {
            "base_digest": "base-old",
            "served_digest": "overlaid",
            "upstream_digest": "upstream-new",
        }
        mark_kwargs = mark_notified.await_args.kwargs if mark_notified.await_args else {}
        assert mark_kwargs["notified_event_class"] == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY


@pytest.mark.asyncio
async def test_probe_suppresses_emit_when_snoozed() -> None:
    """C1: an active snooze on the observed upstream digest suppresses the emit.

    The sweep still marks the notify digest inline (so dedupe stays consistent + the
    outdated-set snooze exclusion, which keys on last_notified_digest, applies) but skips
    creating a new inbox/rail item. Unlike the emit path, the snooze path emits no event,
    so marking inline is safe (#941 emit-then-mark only defers the marker to guard a
    cross-DB emit).
    """
    svc = CatalogService(_make_ctx())
    snoozed_check = _check(
        last_checked_at=None,
        etag=None,
        notified=None,
    )
    snoozed_check.snoozed_digest = "upstream-new"
    snoozed_check.snoozed_until = None
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            return_value=snoozed_check,
        ),
        patch(
            f"{_SWEEP}.fetch_bytes_conditional",
            new_callable=AsyncMock,
            return_value=ConditionalFetch(
                not_modified=False, etag='"v2"', content=b"{}", digest="upstream-new"
            ),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock) as upsert,
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified", new_callable=AsyncMock
        ) as mark_notified,
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await svc._probe_one(_spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
        # Recorded the observation and marked notified inline (snooze read-surface keys on
        # last_notified_digest), but did NOT emit.
        upsert.assert_awaited()
        mark_notified.assert_awaited_once()
        mark_kwargs = mark_notified.await_args.kwargs if mark_notified.await_args else {}
        assert mark_kwargs["notified_digest"] == "upstream-new"
        emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_records_notify_metric() -> None:
    """L6: a real notify increments the Flow-3 emit counter."""
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
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock),
        patch(f"{_SWEEP}.record_update_notified") as metric,
    ):
        await svc._probe_one(_spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
        metric.assert_called_once_with(EventType.CATALOG_UPDATE_AVAILABLE)


@pytest.mark.asyncio
async def test_probe_overlay_upstream_matches_base_is_plain_noop() -> None:
    """Overlay-origin, but upstream still equals the overlay's base → no conflict.

    If upstream matches the base the overlay was built on, the operator's fix is still
    valid against the current upstream — there's nothing to reconcile. (Here upstream
    also equals the served digest, so it's a full no-op; the point is that base-equality
    never yields the conflict class.)
    """
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get", new_callable=AsyncMock, return_value=None
        ),
        patch(
            f"{_SWEEP}.fetch_bytes_conditional",
            new_callable=AsyncMock,
            return_value=ConditionalFetch(
                not_modified=False, etag='"v1"', content=b"{}", digest="base-old"
            ),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        spec = _spec(digest="overlaid", origin="overlay", overlay_base_digest="base-old")
        await svc._probe_one(spec, now=datetime.now(UTC), interval=86400)
        # Upstream == base != served("overlaid"); classified plain-update, and it emits
        # once (a genuine change vs the served spec) — never the conflict class.
        emit.assert_awaited_once()
        emit_kwargs = emit.await_args.kwargs if emit.await_args else {}
        assert emit_kwargs["type"] == EventType.CATALOG_UPDATE_AVAILABLE


@pytest.mark.asyncio
async def test_probe_overlay_unknown_base_falls_back_to_plain() -> None:
    """A pre-A2 overlay revision (NULL base digest) can't prove a conflict → plain class.

    We can't assert the upstream collides with the overlay's base when we never recorded
    that base, so the safe fallback is the non-alarming plain-update class. The column
    self-heals on the next re-materialize.
    """
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
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        spec = _spec(digest="overlaid", origin="overlay", overlay_base_digest=None)
        await svc._probe_one(spec, now=datetime.now(UTC), interval=86400)
        emit.assert_awaited_once()
        emit_kwargs = emit.await_args.kwargs if emit.await_args else {}
        assert emit_kwargs["type"] == EventType.CATALOG_UPDATE_AVAILABLE


@pytest.mark.asyncio
async def test_probe_reclassified_digest_re_emits_once() -> None:
    """Same digest, different class → the widened dedupe key re-fires exactly once.

    A digest previously notified as a plain update can later re-classify (e.g. the served
    revision becomes an overlay whose base the upstream now collides with). Deduping on
    the digest alone would wrongly swallow the conflict; deduping on
    ``(digest, event_class)`` lets the new class fire once.
    """
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            # Already notified this exact digest, but under the plain-update class.
            return_value=_check(
                last_checked_at=None,
                etag=None,
                notified="upstream-new",
                notified_class=EventType.CATALOG_UPDATE_AVAILABLE,
            ),
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
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified", new_callable=AsyncMock
        ) as mark_notified,
        patch(
            f"{_SWEEP}.OverlayRepository.get_live_confirmed_for_api",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        # Now overlay-origin with a base the upstream digest differs from → conflict class.
        spec = _spec(digest="overlaid", origin="overlay", overlay_base_digest="base-old")
        await svc._probe_one(spec, now=datetime.now(UTC), interval=86400)
        emit.assert_awaited_once()
        emit_kwargs = emit.await_args.kwargs if emit.await_args else {}
        assert emit_kwargs["type"] == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY
        mark_kwargs = mark_notified.await_args.kwargs if mark_notified.await_args else {}
        assert mark_kwargs["notified_event_class"] == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY


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
    assert EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY in EventType.ALL


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
    """A failure in a probe's emit path is isolated by the sweep AND leaves the row unmarked.

    #941: the change path now uses the raising ``emit_event`` (not the swallowing
    best-effort wrapper) precisely so a failed emit propagates to the per-API guard and
    the notify marker is NOT advanced — otherwise a swallowed emit failure would dedupe an
    undelivered notification forever.
    """
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
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified", new_callable=AsyncMock
        ) as mark_notified,
        patch(
            f"{_SWEEP}.emit_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("emit boom"),
        ),
    ):
        # Must complete without raising — the sweep-level guard isolates the failure.
        await svc._run_update_notify_sweep()
        # The emit failed → the marker must NOT have been advanced (else a next sweep would
        # dedupe an event that was never delivered).
        mark_notified.assert_not_awaited()


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
