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
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.registry.repos.catalog_update_check_repo import CatalogUpdateCheckRepository
from jentic_one.registry.services.catalog.fetch import ConditionalFetch
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_SWEEP = "jentic_one.registry.services.catalog.service"
_SPEC_URL = "https://raw.githubusercontent.com/jentic/x/main/openapi.json"


@pytest.fixture()
async def clean_state(
    registry_db: DatabaseSession, integration_context: Context
) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(CatalogUpdateCheck))
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
    assert check.last_notified_digest is None  # recorded the check, but never notified


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
    assert check.last_notified_digest is None


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
