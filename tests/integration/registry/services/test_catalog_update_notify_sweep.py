"""Integration tests for the catalog update-notify sweep against real Postgres.

Unlike the unit tests (which mock every repo + session), these drive
``CatalogService._run_update_notify_sweep`` / ``_probe_one`` end-to-end against
the real ``registry_db`` + ``admin_db``, stubbing **only** the out-of-process HTTP
boundary (``fetch_bytes_conditional``). This exercises the multi-session
orchestration (read check → fetch → upsert txn → admin emit txn) and the
digest-based dedupe against persisted state — the parts unit mocks can't catch.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.repos.catalog_update_check_repo import CatalogUpdateCheckRepository
from jentic_one.registry.services.catalog.fetch import ConditionalFetch
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.errors import (
    HostChangeRequiresOperatorError,
    RevisionStateConflictError,
)
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import (
    ORIGIN_CATALOG,
    ActorType,
    ApiRevisionState,
    StoredCredentialType,
)
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_SWEEP = "jentic_one.registry.services.catalog.service"
_SPEC_URL = "https://raw.githubusercontent.com/jentic/x/main/openapi.json"
_OTHER_SPEC_URL = "https://raw.githubusercontent.com/jentic/x/main/other-openapi.json"
_IMPORT_VENDOR = "notify-test-com"
_CLEAN_VENDORS = ("notify-test.com", _IMPORT_VENDOR)
_WRITER = Identity(sub="usr_notify_writer", actor_type=ActorType.USER, permissions=["apis:write"])
_ADMIN = Identity(sub="usr_notify_admin", actor_type=ActorType.USER, permissions=["org:admin"])


@pytest.fixture()
async def clean_state(
    registry_db: DatabaseSession, integration_context: Context
) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with registry_db.session() as session:
            api_ids = select(Api.id).where(Api.vendor.in_(_CLEAN_VENDORS))
            rev_ids = select(ApiRevision.id).where(
                (ApiRevision.source_url.in_([_SPEC_URL, _OTHER_SPEC_URL]))
                | (ApiRevision.api_id.in_(api_ids))
            )
            await session.execute(delete(CatalogUpdateCheck))
            await session.execute(
                delete(OperationURLIndex).where(OperationURLIndex.revision_id.in_(rev_ids))
            )
            await session.execute(delete(Server).where(Server.revision_id.in_(rev_ids)))
            await session.execute(delete(Operation).where(Operation.revision_id.in_(rev_ids)))
            await session.execute(delete(SpecFile).where(SpecFile.revision_id.in_(rev_ids)))
            await session.execute(
                update(Api).where(Api.vendor.in_(_CLEAN_VENDORS)).values(current_revision_id=None)
            )
            await session.execute(
                delete(ApiRevision).where(
                    (ApiRevision.source_url.in_([_SPEC_URL, _OTHER_SPEC_URL]))
                    | (ApiRevision.api_id.in_(api_ids))
                )
            )
            await session.execute(delete(Api).where(Api.vendor.in_(_CLEAN_VENDORS)))
            await session.commit()
        async with integration_context.control_db.session() as session:
            await session.execute(
                delete(Credential).where(Credential.api_vendor.in_(_CLEAN_VENDORS))
            )
            await session.commit()
        async with integration_context.admin_db.session() as session:
            agent_ids = select(Agent.id).where(Agent.name.like("notify-test-agent%"))
            await session.execute(
                delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id.in_(agent_ids))
            )
            await session.execute(delete(Agent).where(Agent.name.like("notify-test-agent%")))
            await session.execute(
                delete(Event).where(
                    Event.type.in_(
                        [
                            EventType.CATALOG_UPDATE_AVAILABLE,
                            EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY,
                        ]
                    )
                )
            )
            await session.commit()

    await _wipe()
    yield
    await _wipe()


@pytest.fixture()
async def registered_api(registry_db: DatabaseSession, clean_state: None) -> Api:
    """A registered API with a published revision carrying a source_url + digest."""
    api = Api(vendor="notify-test.com", name="widgets", version="v1")
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
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    svc = CatalogService(integration_context)
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    events = await _events(integration_context)
    assert len(events) == 1
    evt = events[0]
    assert evt.requires_action is True
    assert evt.data["api_id"] == str(registered_api.id)
    assert evt.data["upstream_digest"] == "sha256:upstream-new"
    assert evt.data["current_digest"] == "sha256:registered"
    assert evt.data["spec_url"] == _SPEC_URL

    async with integration_context.registry_db.session() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
    assert check is not None
    assert check.last_notified_digest == "sha256:upstream-new"
    assert check.last_seen_etag == '"v2"'


async def test_sweep_dedupes_across_runs(integration_context: Context, registered_api: Api) -> None:
    """A second sweep observing the same upstream digest does not re-emit."""
    integration_context.config.catalog.update_check_interval_seconds = 1
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    svc = CatalogService(integration_context)
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed):
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
    same = ConditionalFetch(
        not_modified=False, etag='"v1"', content=b"{}", digest="sha256:registered"
    )
    svc = CatalogService(integration_context)
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=same):
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

    api = Api(vendor="notify-test.com", name="widgets", version="v1")
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
    same = ConditionalFetch(not_modified=False, etag='"v1"', content=body, digest=real_digest)
    svc = CatalogService(integration_context)
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=same):
        await svc._run_update_notify_sweep()

    assert await _events(integration_context) == []
    async with integration_context.registry_db.session() as session:
        check = await CatalogUpdateCheckRepository.get(session, api.id)
    assert check is not None
    # Upstream matches served → no event, and last_notified_digest is synced to the served
    # digest so the outdated read surface stays empty (see M1 sync-back fix).
    assert check.last_notified_digest == real_digest


# ── Phase 4: origin-scoped candidates + outdated derivation ──────────────────


async def test_sweep_skips_manual_revision_not_in_manifest(
    integration_context: Context, registry_db: DatabaseSession, clean_state: None
) -> None:
    """A manual (origin=NULL) revision whose source_url isn't a manifest entry is skipped."""
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
                origin=None,  # manual import
            )
        )
        await session.commit()

    integration_context.config.catalog.update_check_interval_seconds = 86400
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    svc = CatalogService(integration_context)
    with patch(
        f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed
    ) as fetch:
        # No catalog snapshot exists → manifest_spec_urls is empty → the manual spec is
        # out of scope and never fetched.
        await svc._run_update_notify_sweep()

    fetch.assert_not_called()
    assert await _events(integration_context) == []


async def test_outdated_spec_urls_reflects_notify_then_clears_on_adopt(
    integration_context: Context, registered_api: Api
) -> None:
    """outdated_spec_urls surfaces a notified update, then drops once the digest is adopted."""
    integration_context.config.catalog.update_check_interval_seconds = 86400
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    svc = CatalogService(integration_context)
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    async with integration_context.registry_db.session() as session:
        outdated = await CatalogUpdateCheckRepository.outdated_spec_urls(session)
    assert _SPEC_URL in outdated  # notified digest != current revision digest

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
        outdated_after = await CatalogUpdateCheckRepository.outdated_spec_urls(session)
    assert _SPEC_URL not in outdated_after


async def test_outdated_clears_when_upstream_reverts_to_served(
    integration_context: Context, registered_api: Api
) -> None:
    """An upstream revert to the served content must drop the API out of the outdated set.

    Regression for the stuck-badge revert path: ``_probe_one`` never
    lowers ``last_notified_digest`` on a no-change probe (correct, for event dedupe), but
    the read surface (``outdated_api_ids`` / ``outdated_spec_urls``) keys off that same
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
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()

    async with integration_context.registry_db.session() as session:
        assert registered_api.id in await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert _SPEC_URL in await CatalogUpdateCheckRepository.outdated_spec_urls(session)

    # 2. Upstream reverts to the served content. Reopen the interval gate first.
    async with integration_context.registry_db.transaction() as session:
        check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
        assert check is not None
        check.last_checked_at = None
    reverted = ConditionalFetch(
        not_modified=False, etag='"v1"', content=b"{}", digest="sha256:registered"
    )
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=reverted):
        await svc._run_update_notify_sweep()

    # 3. No new event (nothing changed vs served) AND the API is no longer outdated.
    assert len(await _events(integration_context)) == 1  # the step-1 event, not re-fired
    async with integration_context.registry_db.session() as session:
        check_after = await CatalogUpdateCheckRepository.get(session, registered_api.id)
        assert check_after is not None
        assert check_after.last_notified_digest == "sha256:registered"  # pinned to served
        assert registered_api.id not in await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert _SPEC_URL not in await CatalogUpdateCheckRepository.outdated_spec_urls(session)


async def test_outdated_ignores_stale_draft_when_served_revision_adopted(
    integration_context: Context, registered_api: Api, registry_db: DatabaseSession
) -> None:
    """A stale non-archived draft must not keep an API outdated after the served revision adopts.

    Regression: ``outdated_spec_urls`` must compare the notified digest against the *single*
    served revision (current-or-newest), not "any non-archived revision". An API can keep a
    never-promoted draft alongside its live revision; once the live revision adopts the
    upstream the API must drop out of the outdated set even though the draft still differs.
    """
    integration_context.config.catalog.update_check_interval_seconds = 86400
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    svc = CatalogService(integration_context)
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed):
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
        outdated = await CatalogUpdateCheckRepository.outdated_spec_urls(session)
    assert _SPEC_URL not in outdated


async def test_outdated_api_ids_does_not_collide_across_apis_sharing_a_spec_url(
    integration_context: Context, registry_db: DatabaseSession, clean_state: None
) -> None:
    """Two APIs sharing one upstream spec_url must be flagged independently.

    Regression for the ``spec_url``-keyed collision: ``outdated_api_ids`` (used by the
    per-API surfaces) must flag only the genuinely-outdated API, even when a sibling API
    imported from the *same* ``source_url`` is already up to date. ``outdated_spec_urls``
    (the manifest-keyed catalog form) legitimately can't disambiguate a shared URL, which is
    exactly why the /apis surfaces key on api_id instead.
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
                spec_url=shared_url,
                etag='"v2"',
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
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    svc = CatalogService(integration_context)

    # First sweep: the emit raises (transient admin-DB error). The per-API guard isolates
    # it; the API must be left unmarked (no notified digest) so it is not deduped away.
    with (
        patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed),
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
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed):
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
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    svc = CatalogService(integration_context)

    with (
        patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed),
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
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed):
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
    changed = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:upstream-new"
    )
    svc = CatalogService(integration_context)
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=changed):
        await svc._run_update_notify_sweep()
        async with integration_context.registry_db.transaction() as session:
            check = await CatalogUpdateCheckRepository.get(session, registered_api.id)
            assert check is not None
            assert check.last_notified_digest == "sha256:upstream-new"
            assert check.last_notified_event_class == EventType.CATALOG_UPDATE_AVAILABLE
            check.last_checked_at = None  # reopen the gate for a second pass
        await svc._run_update_notify_sweep()

    assert len(await _events(integration_context)) == 1  # deduped, not re-emitted


# ── #1490: held catalog draft promotion resolves update_available ────────────


def _openapi_spec(host: str, *, marker: str) -> str:
    return json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "Widgets", "version": "v1", "description": marker},
            "servers": [{"url": f"https://{host}/v1"}],
            "paths": {
                "/widgets": {
                    "get": {
                        "operationId": "listWidgets",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )


def _catalog_source(
    host: str,
    *,
    marker: str,
    name: str = "widgets",
    source_url: str = _SPEC_URL,
    origin: str | None = ORIGIN_CATALOG,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "type": "inline",
        "content": _openapi_spec(host, marker=marker),
        "filename": "openapi.json",
        "source_url": source_url,
        "vendor": _IMPORT_VENDOR,
        "api_name": name,
        "version": "v1",
        "submitted_by": _WRITER.sub,
    }
    if origin is not None:
        source["origin"] = origin
    return source


async def _run_import(ctx: Context, source: dict[str, Any]) -> dict[str, Any]:
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        db=None,
        payload={"sources": [source]},
        created_by=_WRITER.sub,
    )
    revision: dict[str, Any] = result.body["revisions"][0]
    return revision


async def _bind_api_credential(ctx: Context, *, name: str = "widgets") -> None:
    credential_id = generate_ksuid("cred")
    async with ctx.control_db.session() as session:
        session.add(
            Credential(
                id=credential_id,
                type=StoredCredentialType.API_KEY,
                name=f"cred-{name}",
                api_vendor=_IMPORT_VENDOR,
                api_name=name,
                api_version="v1",
                created_by=_WRITER.sub,
            )
        )
        await session.commit()
    async with ctx.admin_db.session() as session:
        agent = Agent(name=f"notify-test-agent-{name}", registered_by=_WRITER.sub)
        session.add(agent)
        await session.flush()
        session.add(AgentCredentialBinding(agent_id=agent.id, credential_id=credential_id))
        await session.commit()


async def test_promote_held_catalog_draft_resolves_update_available(
    integration_context: Context, clean_state: None
) -> None:
    """Pins #1490: promoting a held catalog draft clears its open update-available state.

    Covers the full held-reimport lifecycle:
    1. A catalog API with bound credentials is notified of an upstream update by the sweep.
    2. A non-admin re-import fetches upstream bytes (including when upstream moved past the
       sweep-notified digest before the re-import) and is held as a DRAFT due to a server
       host change; ``update_available`` stays open while held and across a refused promote.
       The sweep then observes and notifies the held draft's exact digest.
    3. Once an ``org:admin`` promotes the notified catalog draft, ``update_available`` clears on
       the returned ``ApiView``, ``outdated_api_ids``, and ``outdated_spec_urls``, and a
       subsequent sweep observing the promoted digest does not re-emit.
    """
    integration_context.config.catalog.update_check_interval_seconds = 1
    base = await _run_import(
        integration_context, _catalog_source("old.notify-test.com", marker="v1")
    )
    assert base["state"] == ApiRevisionState.IMPORTED
    await _bind_api_credential(integration_context)

    async with integration_context.registry_db.session() as session:
        api = (
            await session.execute(
                select(Api).where(Api.vendor == _IMPORT_VENDOR, Api.name == "widgets")
            )
        ).scalar_one()

    # Sweep notifies for an upstream change ("sha256:v2-notified").
    notified = ConditionalFetch(
        not_modified=False, etag='"v2"', content=b"{}", digest="sha256:v2-notified"
    )
    catalog_svc = CatalogService(integration_context)
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=notified):
        await catalog_svc._run_update_notify_sweep()

    async with integration_context.registry_db.session() as session:
        assert api.id in await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert _SPEC_URL in await CatalogUpdateCheckRepository.outdated_spec_urls(session)

    # Non-admin re-imports from catalog; upstream is at v3 with a host change, so the
    # revision is held as a DRAFT. While held (and on refused promote), update stays open.
    held_source = _catalog_source("new.notify-test.com", marker="v3")
    held_digest = hashlib.sha256(held_source["content"].encode()).hexdigest()
    held = await _run_import(integration_context, held_source)
    assert held["state"] == ApiRevisionState.DRAFT
    assert held["held_for_review"] is True

    rev_svc = RevisionService(integration_context)
    with pytest.raises(HostChangeRequiresOperatorError):
        await rev_svc.promote(
            _IMPORT_VENDOR, "widgets", "v1", held["revision_id"], identity=_WRITER
        )

    async with integration_context.registry_db.session() as session:
        assert api.id in await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert _SPEC_URL in await CatalogUpdateCheckRepository.outdated_spec_urls(session)

    # Promoting a digest other than the outstanding notification leaves the update open.
    async with integration_context.registry_db.transaction() as session:
        check = await CatalogUpdateCheckRepository.get(session, api.id)
        assert check is not None
        check.last_notified_digest = held_digest

    # Operator promotes the notified held catalog draft → update_available clears everywhere.
    view = await rev_svc.promote(
        _IMPORT_VENDOR, "widgets", "v1", held["revision_id"], identity=_ADMIN
    )
    assert view.current_revision_id == held["revision_id"]
    assert view.update_available is False

    async with integration_context.registry_db.session() as session:
        assert api.id not in await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert _SPEC_URL not in await CatalogUpdateCheckRepository.outdated_spec_urls(session)
        check = await CatalogUpdateCheckRepository.get(session, api.id)
        assert check is not None
        assert check.last_notified_digest == held_digest
        assert check.last_notified_event_class == EventType.CATALOG_UPDATE_AVAILABLE

    # Repeated promote on the already-imported revision is rejected as a state conflict.
    with pytest.raises(RevisionStateConflictError):
        await rev_svc.promote(_IMPORT_VENDOR, "widgets", "v1", held["revision_id"], identity=_ADMIN)

    # Next sweep observing the promoted digest sees it in sync and emits no second event.
    async with integration_context.registry_db.transaction() as session:
        check_row = await CatalogUpdateCheckRepository.get(session, api.id)
        assert check_row is not None
        check_row.last_checked_at = None
    in_sync = ConditionalFetch(
        not_modified=False,
        etag='"v3"',
        content=held_source["content"].encode(),
        digest=held_digest,
    )
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=in_sync):
        await catalog_svc._run_update_notify_sweep()
    assert len(await _events(integration_context)) == 1


@pytest.mark.parametrize(
    "draft_matches_notified", [False, True], ids=["distinct-digest", "matching-digest"]
)
async def test_promote_catalog_draft_resolves_only_matching_notified_digest(
    integration_context: Context, clean_state: None, draft_matches_notified: bool
) -> None:
    """Promoting a catalog draft resolves only its exact outstanding notification."""
    integration_context.config.catalog.update_check_interval_seconds = 1
    await _run_import(integration_context, _catalog_source("old.notify-test.com", marker="v1"))
    await _bind_api_credential(integration_context)
    async with integration_context.registry_db.session() as session:
        api = (
            await session.execute(
                select(Api).where(Api.vendor == _IMPORT_VENDOR, Api.name == "widgets")
            )
        ).scalar_one()

    notified_source = _catalog_source("new.notify-test.com", marker="v2")
    notified_digest = hashlib.sha256(notified_source["content"].encode()).hexdigest()
    notified = ConditionalFetch(
        not_modified=False,
        etag='"v2"',
        content=notified_source["content"].encode(),
        digest=notified_digest,
    )
    with patch(f"{_SWEEP}.fetch_bytes_conditional", new_callable=AsyncMock, return_value=notified):
        await CatalogService(integration_context)._run_update_notify_sweep()

    draft_source = (
        notified_source
        if draft_matches_notified
        else _catalog_source("new.notify-test.com", marker="v3")
    )
    draft_digest = hashlib.sha256(draft_source["content"].encode()).hexdigest()
    assert (draft_digest == notified_digest) is draft_matches_notified
    draft = await _run_import(integration_context, draft_source)
    assert draft["state"] == ApiRevisionState.DRAFT

    view = await RevisionService(integration_context).promote(
        _IMPORT_VENDOR, "widgets", "v1", draft["revision_id"], identity=_ADMIN
    )

    expect_update = not draft_matches_notified
    assert view.update_available is expect_update
    async with integration_context.registry_db.session() as session:
        outdated_ids = await CatalogUpdateCheckRepository.outdated_api_ids(session)
        check = await CatalogUpdateCheckRepository.get(session, api.id)
    assert (api.id in outdated_ids) is expect_update
    assert check is not None
    assert check.last_notified_digest == notified_digest
    assert check.last_notified_event_class == EventType.CATALOG_UPDATE_AVAILABLE


async def test_promote_catalog_draft_leaves_unrelated_and_conflict_checks_open(
    integration_context: Context, clean_state: None
) -> None:
    """Pins #1490: promoting a catalog draft leaves other APIs and overlay conflicts open."""
    await _run_import(
        integration_context,
        _catalog_source("old.notify-test.com", marker="v1", name="widgets", source_url=_SPEC_URL),
    )
    await _run_import(
        integration_context,
        _catalog_source(
            "old.notify-test.com", marker="v1-other", name="other", source_url=_OTHER_SPEC_URL
        ),
    )
    await _run_import(
        integration_context,
        _catalog_source(
            "old.notify-test.com",
            marker="v1-conflict",
            name="conflicted",
            source_url=_OTHER_SPEC_URL,
        ),
    )
    await _bind_api_credential(integration_context, name="widgets")
    await _bind_api_credential(integration_context, name="conflicted")
    widgets_draft_source = _catalog_source(
        "new.notify-test.com", marker="v3-widgets", name="widgets"
    )
    widgets_draft_digest = hashlib.sha256(widgets_draft_source["content"].encode()).hexdigest()

    async with integration_context.registry_db.transaction() as session:
        apis = {
            a.name: a
            for a in (
                await session.execute(select(Api).where(Api.vendor == _IMPORT_VENDOR))
            ).scalars()
        }
        now = datetime.now(UTC)
        await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=apis["widgets"].id,
            spec_url=_SPEC_URL,
            etag='"v2"',
            digest="sha256:v2-widgets",
            checked_at=now,
            notified_digest=widgets_draft_digest,
            notified_event_class=EventType.CATALOG_UPDATE_AVAILABLE,
        )
        await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=apis["other"].id,
            spec_url=_OTHER_SPEC_URL,
            etag='"v2"',
            digest="sha256:v2-other",
            checked_at=now,
            notified_digest="sha256:v2-other",
            notified_event_class=EventType.CATALOG_UPDATE_AVAILABLE,
        )
        await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=apis["conflicted"].id,
            spec_url=_OTHER_SPEC_URL,
            etag='"v2"',
            digest="sha256:v2-conflict",
            checked_at=now,
            notified_digest="sha256:v2-conflict",
            notified_event_class=EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY,
        )

    held_widgets = await _run_import(integration_context, widgets_draft_source)
    held_conflict = await _run_import(
        integration_context,
        _catalog_source(
            "new.notify-test.com",
            marker="v3-conflict",
            name="conflicted",
            source_url=_OTHER_SPEC_URL,
        ),
    )

    rev_svc = RevisionService(integration_context)
    widgets_view = await rev_svc.promote(
        _IMPORT_VENDOR, "widgets", "v1", held_widgets["revision_id"], identity=_ADMIN
    )
    conflict_view = await rev_svc.promote(
        _IMPORT_VENDOR, "conflicted", "v1", held_conflict["revision_id"], identity=_ADMIN
    )

    assert widgets_view.update_available is False
    assert conflict_view.update_available is True

    async with integration_context.registry_db.session() as session:
        outdated_ids = await CatalogUpdateCheckRepository.outdated_api_ids(session)
        conflict_check = await CatalogUpdateCheckRepository.get(session, apis["conflicted"].id)
        other_check = await CatalogUpdateCheckRepository.get(session, apis["other"].id)

    assert apis["widgets"].id not in outdated_ids
    assert apis["other"].id in outdated_ids
    assert apis["conflicted"].id in outdated_ids
    assert other_check is not None
    assert other_check.last_notified_digest == "sha256:v2-other"
    assert conflict_check is not None
    assert conflict_check.last_notified_digest == "sha256:v2-conflict"
    assert conflict_check.last_notified_event_class == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY


async def test_promote_non_catalog_draft_does_not_resolve_update_available(
    integration_context: Context, clean_state: None
) -> None:
    """Pins #1490: promoting a manual (non-catalog) draft leaves update_available open."""
    await _run_import(integration_context, _catalog_source("old.notify-test.com", marker="v1"))
    async with integration_context.registry_db.transaction() as session:
        api = (
            await session.execute(
                select(Api).where(Api.vendor == _IMPORT_VENDOR, Api.name == "widgets")
            )
        ).scalar_one()
        await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=api.id,
            spec_url=_SPEC_URL,
            etag='"v2"',
            digest="sha256:v2-upstream",
            checked_at=datetime.now(UTC),
            notified_digest="sha256:v2-upstream",
            notified_event_class=EventType.CATALOG_UPDATE_AVAILABLE,
        )

    manual_draft = await _run_import(
        integration_context,
        _catalog_source("old.notify-test.com", marker="manual-upload", origin=None),
    )
    assert manual_draft["state"] == ApiRevisionState.DRAFT

    view = await RevisionService(integration_context).promote(
        _IMPORT_VENDOR, "widgets", "v1", manual_draft["revision_id"], identity=_WRITER
    )
    assert view.update_available is True

    async with integration_context.registry_db.session() as session:
        assert api.id in await CatalogUpdateCheckRepository.outdated_api_ids(session)
        check = await CatalogUpdateCheckRepository.get(session, api.id)
        assert check is not None
        assert check.last_notified_digest == "sha256:v2-upstream"
        assert check.last_notified_event_class == EventType.CATALOG_UPDATE_AVAILABLE
