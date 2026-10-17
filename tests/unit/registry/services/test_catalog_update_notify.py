"""Unit tests for the catalog update-notify sweep (Flow 3 MVP).

Covers ``CatalogService._run_update_notify_sweep`` / ``_probe_one`` behaviour with
a mocked source fetch + repos: the interval gate, the not-modified no-op, the
``content_hint`` short-circuit, the change-emits-once dedupe, the no-change no-op,
and the per-API best-effort isolation.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.repos.revision_repo import RegisteredSpec
from jentic_one.registry.services.catalog.service import (
    UPDATE_SWEEP_LOCK_KEY,
    CatalogService,
    _ReadState,
)
from jentic_one.registry.services.catalog.snapshot import build_snapshot
from jentic_one.registry.services.catalog.sources.protocol import NotModified, SpecBody
from jentic_one.registry.services.catalog.spec_fetch import CatalogSpecFetcher
from jentic_one.shared.config import CatalogConfig
from jentic_one.shared.models.events import EventType

_API_ID = uuid.uuid4()
_ENTRY_ID = "public/acme.com"
_SPEC_URL = "https://raw.githubusercontent.com/x/y/main/openapi.json"
_SWEEP = "jentic_one.registry.services.catalog.service"
_FETCH_ROW = "jentic_one.registry.services.catalog.spec_fetch.CatalogSpecFetcher.fetch_row"


def _body(digest: str, validator: str | None = '"v2"') -> SpecBody:
    return SpecBody(
        content=b"{}", media_type="application/json", validator=validator, digest=digest
    )


def _entry_row(entry_id: str = _ENTRY_ID, *, content_hint: str | None = None) -> SimpleNamespace:
    slug, external_id = entry_id.split("/", 1)
    return SimpleNamespace(
        entry_id=entry_id,
        source_slug=slug,
        external_id=external_id,
        spec_ref={"url": _SPEC_URL},
        content_hint=content_hint,
    )


@pytest.fixture(autouse=True)
def _live_entries() -> Any:
    """Every candidate's entry is live in the enabled ``public`` source unless a test says not."""

    async def _live(_session: Any, ids: Any) -> dict[str, SimpleNamespace]:
        return {i: _entry_row(i) for i in ids}

    with patch(f"{_SWEEP}.CatalogEntryRepository.live_entries", side_effect=_live) as live:
        yield live


def _fake_try_advisory_lock(held: set[int] | None = None) -> MagicMock:
    """Stand-in for ``DatabaseSession.try_advisory_lock`` backed by a shared ``held`` set.

    Contexts that share one ``held`` set behave like processes on one Postgres: the
    first to take a key wins until it exits; the rest are told ``False``.
    """
    held = set() if held is None else held

    @asynccontextmanager
    async def _try(key: int) -> AsyncIterator[bool]:
        if key in held:
            yield False
            return
        held.add(key)
        try:
            yield True
        finally:
            held.discard(key)

    return MagicMock(side_effect=_try)


def _make_ctx(*, interval: int = 86400, advisory_held: set[int] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.config.catalog = CatalogConfig(
        update_check_interval_seconds=interval,
        update_sweep_deadline_seconds=300,
        update_sweep_max_concurrency=4,
    )
    ctx.config.ingest = MagicMock()
    ctx.config.catalog.catalog_api_id_mappings = []
    ctx.update_sweep_lock = asyncio.Lock()
    session = AsyncMock()
    for db in (ctx.registry_db, ctx.admin_db):
        db.transaction.return_value.__aenter__ = AsyncMock(return_value=session)
        db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        db.session.return_value.__aenter__ = AsyncMock(return_value=session)
        db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.registry_db.try_advisory_lock = _fake_try_advisory_lock(advisory_held)
    return ctx


def _spec(
    digest: str | None = "local-digest",
    *,
    origin: str | None = "catalog",
    overlay_base_digest: str | None = None,
) -> RegisteredSpec:
    return RegisteredSpec(
        api_id=_API_ID,
        catalog_entry_id=_ENTRY_ID,
        source_url=_SPEC_URL,
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
    row.catalog_entry_id = _ENTRY_ID
    row.last_seen_validator = etag
    row.last_seen_content_hint = None
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


async def _probe(
    svc: CatalogService,
    spec: RegisteredSpec,
    *,
    now: datetime,
    interval: int,
    entry: SimpleNamespace | None = None,
) -> None:
    fetcher = CatalogSpecFetcher(svc._ctx)
    await svc._probe_one(spec, entry or _entry_row(), fetcher, now=now, interval=interval)  # type: ignore[arg-type]


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
        patch(_FETCH_ROW, new_callable=AsyncMock) as fetch,
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await _probe(svc, _spec(), now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=NotModified(validator='"v1"'),
        ) as fetch,
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock),
    ):
        await _probe(svc, _spec(), now=now, interval=100)
        fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_304_is_noop_but_records_check() -> None:
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get", new_callable=AsyncMock, return_value=None
        ),
        patch(
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=NotModified(validator='"v1"'),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock) as upsert,
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await _probe(svc, _spec(), now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("upstream-new", '"v2"'),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified", new_callable=AsyncMock
        ) as mark_notified,
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await _probe(svc, _spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("upstream-new", '"v2"'),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await _probe(svc, _spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("local-digest", '"v1"'),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await _probe(svc, _spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("upstream-new", '"v2"'),
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
        await _probe(svc, spec, now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("upstream-new", '"v2"'),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock) as upsert,
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified", new_callable=AsyncMock
        ) as mark_notified,
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await _probe(svc, _spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("upstream-new", '"v2"'),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock),
        patch(f"{_SWEEP}.record_update_notified") as metric,
    ):
        await _probe(svc, _spec(digest="local-digest"), now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("base-old", '"v1"'),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        spec = _spec(digest="overlaid", origin="overlay", overlay_base_digest="base-old")
        await _probe(svc, spec, now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("upstream-new", '"v2"'),
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        spec = _spec(digest="overlaid", origin="overlay", overlay_base_digest=None)
        await _probe(svc, spec, now=datetime.now(UTC), interval=86400)
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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("upstream-new", '"v2"'),
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
        await _probe(svc, spec, now=datetime.now(UTC), interval=86400)
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
        patch(_FETCH_ROW, new_callable=AsyncMock) as fetch,
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
async def test_sweep_skips_when_advisory_lock_held_elsewhere() -> None:
    """Another process holding the sweep advisory lock makes this sweep skip quietly."""
    ctx = _make_ctx(advisory_held={UPDATE_SWEEP_LOCK_KEY})
    svc = CatalogService(ctx)
    with patch(f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify") as specs:
        await svc._run_update_notify_sweep()
        specs.assert_not_called()
    ctx.registry_db.try_advisory_lock.assert_called_once_with(UPDATE_SWEEP_LOCK_KEY)
    # The in-process lock is released on the skip path, so the next trigger can run.
    assert not ctx.update_sweep_lock.locked()


@pytest.mark.asyncio
async def test_sweep_takes_advisory_lock_for_its_duration() -> None:
    """The sweep body runs while holding the advisory lock and releases it after."""
    held: set[int] = set()
    ctx = _make_ctx(advisory_held=held)
    svc = CatalogService(ctx)
    seen: list[bool] = []

    async def _specs(_session: Any) -> list[RegisteredSpec]:
        seen.append(UPDATE_SWEEP_LOCK_KEY in held)
        return []

    with patch(f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify", side_effect=_specs):
        await svc._run_update_notify_sweep()
    assert seen == [True]
    assert held == set()


def _shared_check_store() -> tuple[dict[uuid.UUID, MagicMock], dict[str, Any]]:
    """An in-memory ``catalog_update_checks`` table shared by fake processes."""
    rows: dict[uuid.UUID, MagicMock] = {}

    async def _get(_session: Any, api_id: uuid.UUID) -> MagicMock | None:
        return rows.get(api_id)

    async def _upsert(_session: Any, *, local_api_id: uuid.UUID, **kw: Any) -> None:
        row = rows.get(local_api_id) or _check(last_checked_at=None, etag=None, notified=None)
        row.last_checked_at = kw["checked_at"]
        row.last_seen_validator = kw["validator"]
        rows[local_api_id] = row

    async def _mark(
        _session: Any,
        *,
        local_api_id: uuid.UUID,
        notified_digest: str,
        notified_event_class: str,
    ) -> int:
        row = rows[local_api_id]
        row.last_notified_digest = notified_digest
        row.last_notified_event_class = notified_event_class
        return 1

    return rows, {"get": _get, "upsert": _upsert, "mark_notified": _mark}


async def _slow_changed_fetch(*_a: Any, **_kw: Any) -> SpecBody:
    # Yield to the loop so concurrently started sweeps genuinely overlap.
    await asyncio.sleep(0.01)
    return _body("upstream-new", '"v2"')


async def _run_concurrent_sweeps(ctxs: list[MagicMock]) -> AsyncMock:
    """Run one sweep per ctx concurrently over a shared check store; return the emit mock."""
    _rows, repo = _shared_check_store()
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[_spec(digest="local-digest")],
        ),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.get", side_effect=repo["get"]),
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", side_effect=repo["upsert"]),
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified",
            side_effect=repo["mark_notified"],
        ),
        patch(_FETCH_ROW, side_effect=_slow_changed_fetch),
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await asyncio.gather(*(CatalogService(c)._run_update_notify_sweep() for c in ctxs))
        # A later sweep (next cadence, any process) still dedupes on the persisted digest.
        for c in ctxs:
            await CatalogService(c)._run_update_notify_sweep()
    return emit


@pytest.mark.asyncio
async def test_concurrent_in_process_sweeps_emit_once_per_digest() -> None:
    """Scanner + request-triggered sweep in one process: one emit for a changed digest."""
    ctx = _make_ctx(interval=1)
    emit = await _run_concurrent_sweeps([ctx, ctx])
    assert emit.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_cross_process_sweeps_emit_once_per_digest() -> None:
    """Sweeps in two processes (own in-process locks, one shared DB) emit once."""
    shared: set[int] = set()
    emit = await _run_concurrent_sweeps(
        [_make_ctx(interval=1, advisory_held=shared), _make_ctx(interval=1, advisory_held=shared)]
    )
    assert emit.await_count == 1


@pytest.mark.asyncio
async def test_cross_process_sweeps_without_shared_lock_would_double_emit() -> None:
    """Control for the test above: without a shared advisory lock both processes emit.

    Proves the cross-process lock (not the in-process one or the dedupe columns) is what
    keeps the overlapping case to a single ``catalog.update_available``.
    """
    emit = await _run_concurrent_sweeps([_make_ctx(interval=1), _make_ctx(interval=1)])
    assert emit.await_count == 2


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
            _FETCH_ROW,
            new_callable=AsyncMock,
            return_value=_body("upstream-new", '"v2"'),
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

    async def _slow(*_args: object, now: object, interval: int) -> None:
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
            catalog_entry_id=f"public/acme-{i}.com",
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

    async def _probe_stub(*_args: object, now: object, interval: int) -> None:
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
        patch.object(svc, "_probe_one", side_effect=_probe_stub),
    ):
        await svc._run_update_notify_sweep()

    assert peak <= 2


# ── candidate selection: tracked while the entry is live ────────────────────


@pytest.mark.asyncio
async def test_sweep_skips_api_whose_entry_is_no_longer_listed(_live_entries: Any) -> None:
    """An API whose catalog entry is tombstoned (or gone) is not probed."""
    svc = CatalogService(_make_ctx(interval=86400))

    async def _none(_session: Any, _ids: Any) -> dict[str, Any]:
        return {}

    _live_entries.side_effect = _none
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[_spec()],
        ),
        patch.object(svc, "_probe_one", new_callable=AsyncMock) as probe,
    ):
        await svc._run_update_notify_sweep()
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_skips_api_whose_source_is_disabled(_live_entries: Any) -> None:
    """An entry of a source that is no longer enabled is not probed."""
    svc = CatalogService(_make_ctx(interval=86400))

    async def _other(_session: Any, ids: Any) -> dict[str, Any]:
        return {i: _entry_row("gone-source/acme.com") for i in ids}

    _live_entries.side_effect = _other
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[_spec()],
        ),
        patch.object(svc, "_probe_one", new_callable=AsyncMock) as probe,
    ):
        await svc._run_update_notify_sweep()
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_probes_overlay_api_on_its_entry() -> None:
    """An overlay is a revision of the imported API, so it is probed on the API's entry."""
    svc = CatalogService(_make_ctx(interval=86400))
    with (
        patch(
            f"{_SWEEP}.ApiRevisionRepository.registered_specs_for_notify",
            new_callable=AsyncMock,
            return_value=[_spec(origin="overlay")],
        ),
        patch.object(svc, "_probe_one", new_callable=AsyncMock) as probe,
    ):
        await svc._run_update_notify_sweep()
    probe.assert_awaited_once()
    assert probe.await_args is not None
    assert probe.await_args.args[1].entry_id == _ENTRY_ID


# ── content_hint short-circuit and validator reuse ───────────────────────────


@pytest.mark.asyncio
async def test_probe_unchanged_content_hint_skips_fetch() -> None:
    """The entry still lists the hint last probed: unchanged, no fetch at all."""
    svc = CatalogService(_make_ctx())
    check = _check(last_checked_at=None, etag='"v1"', notified=None)
    check.last_seen_content_hint = "sha:abc"
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            return_value=check,
        ),
        patch(_FETCH_ROW, new_callable=AsyncMock) as fetch,
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock) as upsert,
        patch(f"{_SWEEP}.emit_event", new_callable=AsyncMock) as emit,
    ):
        await _probe(
            svc,
            _spec(),
            now=datetime.now(UTC),
            interval=86400,
            entry=_entry_row(content_hint="sha:abc"),
        )
    fetch.assert_not_called()
    emit.assert_not_called()
    assert upsert.await_args is not None
    assert upsert.await_args.kwargs["content_hint"] == "sha:abc"


@pytest.mark.asyncio
async def test_probe_changed_content_hint_fetches_with_validator() -> None:
    """A different hint means the spec may have changed: fetch, revalidating."""
    svc = CatalogService(_make_ctx())
    check = _check(last_checked_at=None, etag='"v1"', notified=None)
    check.last_seen_content_hint = "sha:old"
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            return_value=check,
        ),
        patch(
            _FETCH_ROW, new_callable=AsyncMock, return_value=NotModified(validator='"v1"')
        ) as fetch,
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock) as upsert,
    ):
        await _probe(
            svc,
            _spec(),
            now=datetime.now(UTC),
            interval=86400,
            entry=_entry_row(content_hint="sha:new"),
        )
    fetch.assert_awaited_once()
    assert fetch.await_args is not None
    assert fetch.await_args.kwargs["validator"] == '"v1"'
    assert upsert.await_args is not None
    assert upsert.await_args.kwargs["content_hint"] == "sha:new"


@pytest.mark.asyncio
async def test_probe_null_content_hint_always_fetches() -> None:
    """A source that publishes no hint never short-circuits (None == None is not a match)."""
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            return_value=_check(last_checked_at=None, etag='"v1"', notified=None),
        ),
        patch(
            _FETCH_ROW, new_callable=AsyncMock, return_value=NotModified(validator='"v1"')
        ) as fetch,
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
    ):
        await _probe(svc, _spec(), now=datetime.now(UTC), interval=86400)
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_does_not_reuse_validator_of_another_entry() -> None:
    """After the API switched entries, the old entry's validator and hint are not reused."""
    svc = CatalogService(_make_ctx())
    check = _check(last_checked_at=None, etag='"old-entry"', notified=None)
    check.catalog_entry_id = "other/acme.com"
    check.last_seen_content_hint = "sha:abc"
    with (
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.get",
            new_callable=AsyncMock,
            return_value=check,
        ),
        patch(_FETCH_ROW, new_callable=AsyncMock, return_value=_body("local-digest")) as fetch,
        patch(f"{_SWEEP}.CatalogUpdateCheckRepository.upsert", new_callable=AsyncMock),
    ):
        await _probe(
            svc,
            _spec(),
            now=datetime.now(UTC),
            interval=86400,
            entry=_entry_row(content_hint="sha:abc"),
        )
    fetch.assert_awaited_once()
    assert fetch.await_args is not None
    assert fetch.await_args.kwargs["validator"] is None


# ── catalog entry view: update_available derivation ──────────────────────────


def _state(registered: set[str], outdated: set[str]) -> Any:
    snapshot = build_snapshot(
        ("k", "k"),
        [
            SimpleNamespace(
                source_slug="public",
                external_id="acme.com",
                catalog_api_id="acme.com",
                catalog_api_id_hint="acme.com",
                title=None,
                version=None,
                tags=[],
                human_url=None,
                spec_ref={"url": _SPEC_URL},
                content_hint=None,
            )
        ],
        sources={},
        mappings=[],
    )
    allowed = frozenset({"public"})
    return _ReadState(
        snapshot=snapshot,
        rows={},
        allowed=allowed,
        registered=frozenset(registered),
        outdated_ids=outdated,
        view=snapshot.collapse(allowed, frozenset(registered)),
    )


def _view(registered: set[str], outdated: set[str]) -> Any:
    state = _state(registered, outdated)
    return CatalogService._to_view(state, state.snapshot.by_id[_ENTRY_ID])


def test_to_view_update_available_only_when_registered_and_outdated() -> None:
    v = _view({_ENTRY_ID}, {_ENTRY_ID})
    assert v.registered is True
    assert v.update_available is True


def test_to_view_not_outdated_when_not_registered() -> None:
    # In the outdated set but NOT registered locally → never update_available.
    v = _view(set(), {_ENTRY_ID})
    assert v.registered is False
    assert v.update_available is False


def test_to_view_registered_not_outdated() -> None:
    v = _view({_ENTRY_ID}, set())
    assert v.registered is True
    assert v.update_available is False
