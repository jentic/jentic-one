"""Integration tests for the catalog update-notify sweep against real Postgres.

Unlike the unit tests (which mock every repo + session), these drive
``CatalogService._run_update_notify_sweep`` / ``_probe_one`` end-to-end against
the real ``registry_db`` + ``admin_db`` and a real live ``catalog_entries`` row,
stubbing **only** the source fetch (``CatalogSpecFetcher.fetch_row``). This
exercises the multi-session orchestration (read check → fetch → upsert txn →
admin emit txn), the entry-keyed candidate selection and the digest-based dedupe
against persisted state — the parts unit mocks can't catch.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.registry.repos.catalog_update_check_repo import CatalogUpdateCheckRepository
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.catalog.sources.protocol import SpecBody
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_SWEEP = "jentic_one.registry.services.catalog.service"
_FETCH_ROW = "jentic_one.registry.services.catalog.spec_fetch.CatalogSpecFetcher.fetch_row"
_SPEC_URL = "https://raw.githubusercontent.com/jentic/x/main/openapi.json"
#: A live entry of the default (enabled) ``public`` source.
_ENTRY_ID = "public/notify-test.com"


def _changed(digest: str, validator: str = '"v2"', content: bytes = b"{}") -> SpecBody:
    return SpecBody(
        content=content, media_type="application/json", validator=validator, digest=digest
    )


async def _list_entry(
    db: DatabaseSession, *, content_hint: str | None = None, removed: bool = False
) -> None:
    """Make ``_ENTRY_ID`` a row the ``public`` source lists (or has stopped listing)."""
    now = datetime.now(UTC)
    async with db.session() as session:
        session.add(
            CatalogEntryRow(
                entry_id=_ENTRY_ID,
                source_slug="public",
                external_id="notify-test.com",
                catalog_api_id_hint="notify-test.com",
                catalog_api_id="notify-test.com",
                spec_ref={"url": _SPEC_URL},
                tags=[],
                content_hint=content_hint,
                seen_at=now,
                removed_at=now if removed else None,
            )
        )
        await session.commit()


@pytest.fixture()
async def clean_state(
    registry_db: DatabaseSession, integration_context: Context
) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(CatalogUpdateCheck))
            await session.execute(
                delete(CatalogEntryRow).where(CatalogEntryRow.entry_id == _ENTRY_ID)
            )
            await session.execute(delete(ApiRevision).where(ApiRevision.source_url == _SPEC_URL))
            await session.execute(delete(Api).where(Api.vendor == "notify-test.com"))
            await session.commit()
        async with integration_context.admin_db.session() as session:
            await session.execute(
                delete(Event).where(Event.type == EventType.CATALOG_UPDATE_AVAILABLE)
            )
            await session.commit()

    await _wipe()
    yield
    await _wipe()


@pytest.fixture()
async def registered_api(registry_db: DatabaseSession, clean_state: None) -> Api:
    """An API imported from a live catalog entry, with a published revision + digest."""
    await _list_entry(registry_db)
    api = Api(vendor="notify-test.com", name="widgets", version="v1", catalog_entry_id=_ENTRY_ID)
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        session.add(
            ApiRevision(
                api_id=api.id,
                state="published",
                spec_digest="sha256:registered",
                source_type="url",
                source_url=_SPEC_URL,
                origin="catalog",
            )
        )
        await session.commit()
    return api


async def _events(ctx: Context) -> list[Event]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(Event).where(Event.type == EventType.CATALOG_UPDATE_AVAILABLE)
        )
        return list(result.scalars().all())


async def test_sweep_emits_event_and_records_check(
    integration_context: Context, registered_api: Api
) -> None:
    """A changed upstream digest emits one event and persists the notify state."""
    integration_context.config.catalog.update_check_interval_seconds = 86400
    changed = _changed("sha256:upstream-new")
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    events = await _events(integration_context)
    assert len(events) == 1
    evt = events[0]
    assert evt.requires_action is True
    assert evt.data["api_id"] == str(registered_api.id)
    assert evt.data["upstream_digest"] == "sha256:upstream-new"
    assert evt.data["current_digest"] == "sha256:registered"
    assert evt.data["catalog_entry_id"] == _ENTRY_ID
    assert evt.data["spec_url"] == _SPEC_URL

    async with integration_context.registry_db.session() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
    assert check is not None
    assert check.last_notified_digest == "sha256:upstream-new"
    assert check.catalog_entry_id == _ENTRY_ID
    assert check.last_seen_validator == '"v2"'


async def test_sweep_dedupes_across_runs(integration_context: Context, registered_api: Api) -> None:
    """A second sweep observing the same upstream digest does not re-emit."""
    integration_context.config.catalog.update_check_interval_seconds = 1
    changed = _changed("sha256:upstream-new")
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()
        # Force the per-API interval gate open by clearing last_checked_at.
        async with integration_context.registry_db.transaction() as session:
            check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
            assert check is not None
            check.last_checked_at = None
        await svc._run_update_notify_sweep()

    events = await _events(integration_context)
    assert len(events) == 1  # deduped on the persisted last_notified_digest


async def test_sweep_no_event_when_upstream_matches_registered(
    integration_context: Context, registered_api: Api
) -> None:
    """Upstream digest equal to the registered revision's digest → no event."""
    integration_context.config.catalog.update_check_interval_seconds = 86400
    same = _changed("sha256:registered", '"v1"')
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=same):
        await svc._run_update_notify_sweep()

    assert await _events(integration_context) == []
    async with integration_context.registry_db.session() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
    assert check is not None
    # No event fired, and last_notified_digest is pinned to the served digest (sync): the
    # upstream matches what we serve, so nothing is outstanding and the outdated read
    # surface (last_notified_digest != served) must be empty. (Pre-M1 this stayed None; the
    # sync-back write keeps the outdated set honest without ever emitting.)
    assert check.last_notified_digest == "sha256:registered"


async def test_sweep_no_event_with_real_bare_hex_digest(
    integration_context: Context, registry_db: DatabaseSession, clean_state: None
) -> None:
    """Guards the digest *format* contract: ingest stores a bare sha256 hex
    (``hashlib.sha256(body).hexdigest()``, no ``sha256:`` prefix), and the probe
    computes the same bare hex over the same body — so an unchanged upstream must
    compare equal and emit nothing. The other tests hand-feed opaque ``sha256:*``
    markers on both sides, which would pass even if the two sides framed digests
    differently; this one exercises the real convention end-to-end.
    """
    body = b'{"openapi": "3.0.0", "info": {"title": "widgets", "version": "1"}}'
    real_digest = hashlib.sha256(body).hexdigest()
    assert ":" not in real_digest  # bare hex, matching the ingest convention

    await _list_entry(registry_db)
    api = Api(vendor="notify-test.com", name="widgets", version="v1", catalog_entry_id=_ENTRY_ID)
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        session.add(
            ApiRevision(
                api_id=api.id,
                state="published",
                spec_digest=real_digest,
                source_type="url",
                source_url=_SPEC_URL,
                origin="catalog",
            )
        )
        await session.commit()

    integration_context.config.catalog.update_check_interval_seconds = 86400
    same = _changed(real_digest, '"v1"', body)
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=same):
        await svc._run_update_notify_sweep()

    assert await _events(integration_context) == []
    async with integration_context.registry_db.session() as session:
        check = await CatalogUpdateCheckRepository.get(session, api.id)
    assert check is not None
    # Upstream matches served → no event, and last_notified_digest is synced to the served
    # digest so the outdated read surface stays empty (see M1 sync-back fix).
    assert check.last_notified_digest == real_digest


# ── candidates: APIs imported from a live entry ──────────────────────────────


async def test_sweep_skips_api_not_imported_from_an_entry(
    integration_context: Context, registry_db: DatabaseSession, clean_state: None
) -> None:
    """An API that records no catalog entry (a manual import) is never probed."""
    await _list_entry(registry_db)
    api = Api(vendor="notify-test.com", name="manual", version="v1")
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        session.add(
            ApiRevision(
                api_id=api.id,
                state="published",
                spec_digest="sha256:registered",
                source_type="url",
                source_url=_SPEC_URL,
                origin=None,  # manual import of the same URL
            )
        )
        await session.commit()

    integration_context.config.catalog.update_check_interval_seconds = 86400
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=_changed("sha256:new")) as fetch:
        await svc._run_update_notify_sweep()

    fetch.assert_not_called()
    assert await _events(integration_context) == []


async def test_sweep_skips_api_whose_entry_was_tombstoned(
    integration_context: Context, registry_db: DatabaseSession, clean_state: None
) -> None:
    """An entry its source no longer lists is not tracked: no fetch, no event."""
    await _list_entry(registry_db, removed=True)
    api = Api(vendor="notify-test.com", name="gone", version="v1", catalog_entry_id=_ENTRY_ID)
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        session.add(
            ApiRevision(
                api_id=api.id,
                state="published",
                spec_digest="sha256:registered",
                source_type="catalog",
                source_url=_SPEC_URL,
                origin="catalog",
            )
        )
        await session.commit()

    integration_context.config.catalog.update_check_interval_seconds = 86400
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=_changed("sha256:new")) as fetch:
        await svc._run_update_notify_sweep()

    fetch.assert_not_called()
    assert await _events(integration_context) == []


async def test_sweep_unchanged_content_hint_skips_the_fetch(
    integration_context: Context, registry_db: DatabaseSession, registered_api: Api
) -> None:
    """Once a hint is recorded, an entry still listing it is not fetched again."""
    async with registry_db.session() as session:
        row = (
            await session.execute(
                select(CatalogEntryRow).where(CatalogEntryRow.entry_id == _ENTRY_ID)
            )
        ).scalar_one()
        row.content_hint = "sha:abc"
        await session.commit()
    integration_context.config.catalog.update_check_interval_seconds = 1
    svc = CatalogService(integration_context)
    with patch(
        _FETCH_ROW, new_callable=AsyncMock, return_value=_changed("sha256:registered")
    ) as fetch:
        await svc._run_update_notify_sweep()
        assert fetch.await_count == 1  # first probe: nothing recorded yet
        async with registry_db.transaction() as session:
            check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
            assert check is not None
            assert check.last_seen_content_hint == "sha:abc"
            check.last_checked_at = None  # reopen the interval gate
        await svc._run_update_notify_sweep()
        assert fetch.await_count == 1  # same hint: short-circuited

        async with registry_db.transaction() as session:
            row = (
                await session.execute(
                    select(CatalogEntryRow).where(CatalogEntryRow.entry_id == _ENTRY_ID)
                )
            ).scalar_one()
            row.content_hint = "sha:def"
            check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
            assert check is not None
            check.last_checked_at = None
        await svc._run_update_notify_sweep()
        assert fetch.await_count == 2  # the hint moved: fetched again


async def test_outdated_entry_ids_reflects_notify_then_clears_on_adopt(
    integration_context: Context, registered_api: Api
) -> None:
    """outdated_entry_ids surfaces a notified update, then drops once the digest is adopted."""
    integration_context.config.catalog.update_check_interval_seconds = 86400
    changed = _changed("sha256:upstream-new")
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    async with integration_context.registry_db.session() as session:
        outdated = await CatalogUpdateCheckRepository.outdated_entry_ids(session)
    assert _ENTRY_ID in outdated  # notified digest != current revision digest

    # Simulate adopting the upstream: the current revision's digest now equals the
    # notified digest (as a re-import would produce).
    async with integration_context.registry_db.transaction() as session:
        rev = (
            await session.execute(
                select(ApiRevision).where(ApiRevision.api_id == registered_api.id)
            )
        ).scalar_one()
        rev.spec_digest = "sha256:upstream-new"

    async with integration_context.registry_db.session() as session:
        outdated_after = await CatalogUpdateCheckRepository.outdated_entry_ids(session)
    assert _ENTRY_ID not in outdated_after


async def test_outdated_clears_when_upstream_reverts_to_served(
    integration_context: Context, registered_api: Api
) -> None:
    """An upstream revert to the served content must drop the API out of the outdated set.

    Regression for the stuck-badge revert path: ``_probe_one`` never
    lowers ``last_notified_digest`` on a no-change probe (correct, for event dedupe), but
    the read surface (``outdated_api_ids`` / ``outdated_entry_ids``) keys off that same
    field. So without a fix, this sequence stuck the badge permanently:

    1. served digest is ``sha256:registered``; upstream publishes ``sha256:upstream-new``
       → sweep notifies, ``last_notified_digest = sha256:upstream-new``, API is outdated.
    2. upstream is *reverted* (bad publish rolled back) → it now serves the original
       ``sha256:registered`` again, byte-identical to what we already serve.
    3. next sweep sees no change vs served, so it emits nothing — but the API must ALSO
       stop being outdated, since the upstream no longer differs from what we serve. A
       re-import can't fix it (it would adopt content identical to the served revision, so
       the served digest never moves off ``sha256:registered``).

    The fix: when the upstream matches the served digest, the probe pins
    ``last_notified_digest`` to it (``sync_notified``) so the ``!= served digest`` read
    predicate clears. Event dedupe is unaffected — a later, genuinely different upstream
    digest still re-fires exactly once.
    """
    integration_context.config.catalog.update_check_interval_seconds = 1
    svc = CatalogService(integration_context)

    # 1. Upstream advances → notify, API becomes outdated.
    changed = _changed("sha256:upstream-new")
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    async with integration_context.registry_db.session() as session:
        assert registered_api.id in await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert _ENTRY_ID in await CatalogUpdateCheckRepository.outdated_entry_ids(session)

    # 2. Upstream reverts to the served content. Reopen the interval gate first.
    async with integration_context.registry_db.transaction() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
        assert check is not None
        check.last_checked_at = None
    reverted = _changed("sha256:registered", '"v1"')
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=reverted):
        await svc._run_update_notify_sweep()

    # 3. No new event (nothing changed vs served) AND the API is no longer outdated.
    assert len(await _events(integration_context)) == 1  # the step-1 event, not re-fired
    async with integration_context.registry_db.session() as session:
        check_after = await CatalogUpdateCheckRepository.get(session, registered_api.id)
        assert check_after is not None
        assert check_after.last_notified_digest == "sha256:registered"  # pinned to served
        assert registered_api.id not in await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert _ENTRY_ID not in await CatalogUpdateCheckRepository.outdated_entry_ids(session)


async def test_outdated_ignores_stale_draft_when_served_revision_adopted(
    integration_context: Context, registered_api: Api, registry_db: DatabaseSession
) -> None:
    """A stale non-archived draft must not keep an API outdated after the served revision adopts.

    Regression: ``outdated_entry_ids`` must compare the notified digest against the *single*
    served revision (current-or-newest), not "any non-archived revision". An API can keep a
    never-promoted draft alongside its live revision; once the live revision adopts the
    upstream the API must drop out of the outdated set even though the draft still differs.
    """
    integration_context.config.catalog.update_check_interval_seconds = 86400
    changed = _changed("sha256:upstream-new")
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    async with integration_context.registry_db.transaction() as session:
        # The served (published) revision adopts the upstream…
        served = (
            await session.execute(
                select(ApiRevision)
                .where(ApiRevision.api_id == registered_api.id)
                .where(ApiRevision.state == "published")
            )
        ).scalar_one()
        served.spec_digest = "sha256:upstream-new"
        # …but a stale, never-promoted draft with a different digest lingers. Make it
        # strictly older so the served revision wins the current-or-newest selection
        # (no current_revision_id pin needed → no dangling FK for teardown).
        session.add(
            ApiRevision(
                api_id=registered_api.id,
                state="draft",
                spec_digest="sha256:some-other-draft",
                source_type="url",
                source_url=_SPEC_URL,
                origin="catalog",
                created_at=datetime(2000, 1, 1, tzinfo=UTC),
            )
        )

    async with integration_context.registry_db.session() as session:
        outdated = await CatalogUpdateCheckRepository.outdated_entry_ids(session)
    assert _ENTRY_ID not in outdated


async def test_outdated_api_ids_does_not_collide_across_apis_sharing_an_entry(
    integration_context: Context, registry_db: DatabaseSession, clean_state: None
) -> None:
    """Two APIs tracking one catalog entry must be flagged independently.

    ``outdated_api_ids`` (used by the per-API surfaces) must flag only the
    genuinely-outdated API, even when a sibling API imported from the *same* entry is
    already up to date. ``outdated_entry_ids`` (the entry-keyed catalog form) legitimately
    can't disambiguate a shared entry, which is exactly why the /apis surfaces key on
    api_id instead.
    """
    shared_url = _SPEC_URL
    stale = Api(vendor="notify-test.com", name="stale", version="v1")
    fresh = Api(vendor="notify-test.com", name="fresh", version="v1")
    async with registry_db.transaction() as session:
        session.add_all([stale, fresh])
        await session.flush()
        # Both served revisions were imported from the same upstream URL.
        session.add_all(
            [
                ApiRevision(
                    api_id=stale.id,
                    state="published",
                    spec_digest="sha256:old",
                    source_type="url",
                    source_url=shared_url,
                    origin="catalog",
                ),
                ApiRevision(
                    api_id=fresh.id,
                    state="published",
                    spec_digest="sha256:new",
                    source_type="url",
                    source_url=shared_url,
                    origin="catalog",
                ),
            ]
        )
        await session.flush()
        # Upstream advanced to sha256:new and both APIs were notified. `stale` hasn't
        # adopted it (served digest still sha256:old); `fresh` already has (sha256:new).
        now = datetime.now(UTC)
        for api in (stale, fresh):
            await CatalogUpdateCheckRepository.upsert(
                session,
                local_api_id=api.id,
                catalog_entry_id=_ENTRY_ID,
                validator='"v2"',
                digest="sha256:new",
                checked_at=now,
                notified_digest="sha256:new",
            )

    async with integration_context.registry_db.session() as session:
        outdated_ids = await CatalogUpdateCheckRepository.outdated_api_ids(session)

    assert stale.id in outdated_ids  # served digest != notified → outdated
    assert fresh.id not in outdated_ids  # served digest == notified → not outdated


# ── #941: emit-then-mark durability ──────────────────────────────────────────


async def test_sweep_emit_failure_leaves_unmarked_so_next_sweep_re_emits(
    integration_context: Context, registered_api: Api
) -> None:
    """#941: a *failed emit* must NOT advance the notify marker (no permanent suppression).

    The change path uses the raising ``emit_event`` (not the swallowing best-effort
    wrapper) exactly so a transient admin-DB emit failure propagates to the sweep's per-API
    guard and the marker is left unadvanced. Patching ``emit_event`` here reproduces the
    real production seam (a raising emit), unlike patching the best-effort wrapper — which
    in production swallows and would let the marker advance. On the next healthy sweep the
    change re-emits rather than being deduped against a notification that never landed.
    """
    integration_context.config.catalog.update_check_interval_seconds = 1
    changed = _changed("sha256:upstream-new")
    svc = CatalogService(integration_context)

    # First sweep: the emit raises (transient admin-DB error). The per-API guard isolates
    # it; the API must be left unmarked (no notified digest) so it is not deduped away.
    with (
        patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed),
        patch(
            f"{_SWEEP}.emit_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("admin DB unavailable mid-emit"),
        ),
    ):
        await svc._run_update_notify_sweep()

    assert await _events(integration_context) == []  # nothing emitted
    async with integration_context.registry_db.session() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
    assert check is not None
    # Observation persisted (we did fetch), but the notify marker was NOT advanced —
    # so the change is still outstanding and will re-emit.
    assert check.last_seen_digest == "sha256:upstream-new"
    assert check.last_notified_digest is None
    assert check.last_notified_event_class is None

    # Second sweep with a healthy emit: it re-emits (the bug would have suppressed it).
    async with integration_context.registry_db.transaction() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
        assert check is not None
        check.last_checked_at = None  # reopen the per-API interval gate
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    events = await _events(integration_context)
    assert len(events) == 1  # re-emitted, not permanently suppressed
    async with integration_context.registry_db.session() as session:
        check_after = await CatalogUpdateCheckRepository.get(session, registered_api.id)
    assert check_after is not None
    assert check_after.last_notified_digest == "sha256:upstream-new"


async def test_sweep_crash_after_emit_before_mark_re_emits_at_most_once(
    integration_context: Context, registered_api: Api
) -> None:
    """#941: a crash *after* the emit commits but *before* the mark → duplicate, not loss.

    This is the residual window emit-then-mark deliberately accepts. Faithfully model it by
    letting the emit succeed (event committed to the admin DB) and raising inside the
    *mark* seam. The per-API guard isolates the failure; because the marker never landed,
    the next sweep re-emits — so the event fires a *second* time (at-most-once duplicate),
    which is strictly better than the permanent suppression the old ordering caused.
    """
    integration_context.config.catalog.update_check_interval_seconds = 1
    changed = _changed("sha256:upstream-new")
    svc = CatalogService(integration_context)

    with (
        patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed),
        patch(
            f"{_SWEEP}.CatalogUpdateCheckRepository.mark_notified",
            new_callable=AsyncMock,
            side_effect=RuntimeError("crash before mark commit"),
        ),
    ):
        await svc._run_update_notify_sweep()

    # The event WAS emitted (emit committed before the mark seam), but the marker did not
    # land because mark_notified raised.
    assert len(await _events(integration_context)) == 1
    async with integration_context.registry_db.session() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
    assert check is not None
    assert check.last_notified_digest is None  # marker not advanced

    # Next healthy sweep re-emits (the accepted duplicate) and this time marks.
    async with integration_context.registry_db.transaction() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
        assert check is not None
        check.last_checked_at = None
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    assert len(await _events(integration_context)) == 2  # duplicate, not suppressed
    async with integration_context.registry_db.session() as session:
        check_after = await CatalogUpdateCheckRepository.get(session, registered_api.id)
    assert check_after is not None
    assert check_after.last_notified_digest == "sha256:upstream-new"


async def test_sweep_marks_notified_after_successful_emit_and_dedupes(
    integration_context: Context, registered_api: Api
) -> None:
    """#941 happy path: a successful emit advances the notify marker and the next sweep dedupes.

    Confirms emit-then-mark does not double-notify on the success path: the marker is
    written after the emit, so a second sweep observing the same digest is deduped.
    """
    integration_context.config.catalog.update_check_interval_seconds = 1
    changed = _changed("sha256:upstream-new")
    svc = CatalogService(integration_context)
    with patch(_FETCH_ROW, new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()
        async with integration_context.registry_db.transaction() as session:
            check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
            assert check is not None
            assert check.last_notified_digest == "sha256:upstream-new"
            assert check.last_notified_event_class == EventType.CATALOG_UPDATE_AVAILABLE
            check.last_checked_at = None  # reopen the gate for a second pass
        await svc._run_update_notify_sweep()

    assert len(await _events(integration_context)) == 1  # deduped, not re-emitted
