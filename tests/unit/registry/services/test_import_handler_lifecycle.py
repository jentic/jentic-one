"""Unit tests for ImportHandler Flow-3 lifecycle attribution (L2, L6).

Covers ``_deprecate_superseded_overlay``: on a successful CAS demote it emits an
attributed ``overlay.deprecated`` event (author + acting operator) and increments the
auto-deprecate metric; on a no-op demote (overlay not CONFIRMED) it emits nothing; and
the same-API guard (#1078) skips the demote when the re-import did not supersede the
overlay's served revision. Repos/DB are mocked — this asserts wiring, not persistence
(integration covers that).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.models.events import EventType

_SVC = "jentic_one.registry.services.import_service"


def _ctx() -> MagicMock:
    ctx = MagicMock()
    session = AsyncMock()
    for db in (ctx.registry_db, ctx.admin_db):
        db.transaction.return_value.__aenter__ = AsyncMock(return_value=session)
        db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _overlay(
    api_id: uuid.UUID | str = "api-123", confirmed_revision_id: uuid.UUID | None = None
) -> MagicMock:
    overlay = MagicMock()
    overlay.created_by = "usr_author"
    overlay.api_id = api_id
    overlay.confirmed_revision_id = confirmed_revision_id
    return overlay


@pytest.mark.asyncio
async def test_deprecate_superseded_emits_attributed_event_and_metric() -> None:
    """A successful auto-deprecate emits an attributed overlay.deprecated + a metric (L2/L6)."""
    handler = ImportHandler(_ctx())
    overlay = _overlay()
    with (
        patch(f"{_SVC}.OverlayRepository.get_by_id", new_callable=AsyncMock, return_value=overlay),
        patch(f"{_SVC}.OverlayRepository.set_status", new_callable=AsyncMock, return_value=1),
        patch(f"{_SVC}.emit_event_best_effort", new_callable=AsyncMock) as emit,
        patch(f"{_SVC}.record_overlay_auto_deprecated") as metric,
    ):
        await handler._deprecate_superseded_overlay(
            "job-1", "ovr_1", actor_id="usr_operator", actor_type="user"
        )
        emit.assert_awaited_once()
        kwargs = emit.await_args.kwargs if emit.await_args else {}
        assert kwargs["type"] == EventType.OVERLAY_DEPRECATED
        assert kwargs["created_by"] == "usr_operator"
        assert kwargs["actor_id"] == "usr_operator"
        assert kwargs["requires_action"] is False
        assert kwargs["data"]["overlay_id"] == "ovr_1"
        assert kwargs["data"]["author"] == "usr_author"
        assert kwargs["data"]["reason"] == "superseded_by_catalog_reimport"
        metric.assert_called_once()


@pytest.mark.asyncio
async def test_deprecate_superseded_noop_emits_nothing() -> None:
    """A no-op demote (overlay not CONFIRMED → rowcount 0) emits no event and no metric."""
    handler = ImportHandler(_ctx())
    with (
        patch(
            f"{_SVC}.OverlayRepository.get_by_id", new_callable=AsyncMock, return_value=_overlay()
        ),
        patch(f"{_SVC}.OverlayRepository.set_status", new_callable=AsyncMock, return_value=0),
        patch(f"{_SVC}.emit_event_best_effort", new_callable=AsyncMock) as emit,
        patch(f"{_SVC}.record_overlay_auto_deprecated") as metric,
    ):
        await handler._deprecate_superseded_overlay(
            "job-1", "ovr_1", actor_id="usr_operator", actor_type="user"
        )
        emit.assert_not_awaited()
        metric.assert_not_called()


@pytest.mark.asyncio
async def test_deprecate_superseded_swallows_emit_failure_but_still_records_metric() -> None:
    """A best-effort notification failure must not undo the durable demote (review #4).

    The CAS demote already committed, so an ``emit_event_best_effort`` that raises is
    swallowed (``_emit_overlay_deprecated`` owns its own try/except) — no exception
    propagates to fail/retry the import job — and the auto-deprecate metric, which counts
    *deprecations* (not notifications), is still recorded. Only the (best-effort)
    notification is lost.
    """
    handler = ImportHandler(_ctx())
    overlay = _overlay()
    with (
        patch(f"{_SVC}.OverlayRepository.get_by_id", new_callable=AsyncMock, return_value=overlay),
        patch(f"{_SVC}.OverlayRepository.set_status", new_callable=AsyncMock, return_value=1),
        patch(
            f"{_SVC}.emit_event_best_effort",
            new_callable=AsyncMock,
            side_effect=RuntimeError("admin db down"),
        ) as emit,
        patch(f"{_SVC}.record_overlay_auto_deprecated") as metric,
    ):
        # Must not raise — the demote is durable, the notification is best-effort.
        await handler._deprecate_superseded_overlay(
            "job-1", "ovr_1", actor_id="usr_operator", actor_type="user"
        )
        emit.assert_awaited_once()
        metric.assert_called_once()


@pytest.mark.parametrize(
    ("overlay_on_superseding_api", "confirmed_revision_state", "expected_skip"),
    [
        # #1078: the re-import landed on a different API row (e.g. the clean identity of a
        # vendor-doubled API) — the overlay's revision was never archived.
        (False, "imported", "api_mismatch"),
        # Same API row, but the overlay's confirmed revision is still the active one.
        (True, "imported", "revision_still_served"),
        (True, "published", "revision_still_served"),
        # Normal A4b supersede: same API row, the confirmed revision was archived.
        (True, "archived", None),
        # Confirmed revision id no longer resolves to a row — nothing served, deprecate.
        (True, None, None),
    ],
)
@pytest.mark.asyncio
async def test_deprecate_superseded_guards_on_same_api_and_archived_revision(
    overlay_on_superseding_api: bool,
    confirmed_revision_state: str | None,
    expected_skip: str | None,
) -> None:
    """#1078: only deprecate an overlay whose revision the re-import actually archived."""
    handler = ImportHandler(_ctx())
    superseding_api_id = uuid.uuid4()
    overlay = _overlay(
        api_id=superseding_api_id if overlay_on_superseding_api else uuid.uuid4(),
        confirmed_revision_id=uuid.uuid4(),
    )
    with (
        patch(f"{_SVC}.OverlayRepository.get_by_id", new_callable=AsyncMock, return_value=overlay),
        patch(
            f"{_SVC}.ApiRevisionRepository.state_of",
            new_callable=AsyncMock,
            return_value=confirmed_revision_state,
        ),
        patch(
            f"{_SVC}.OverlayRepository.set_status", new_callable=AsyncMock, return_value=1
        ) as set_status,
        patch(f"{_SVC}.emit_event_best_effort", new_callable=AsyncMock) as emit,
        patch(f"{_SVC}.record_overlay_auto_deprecated") as metric,
    ):
        skipped = await handler._deprecate_superseded_overlay(
            "job-1",
            "ovr_1",
            superseding_api_id=superseding_api_id,
            actor_id="usr_operator",
            actor_type="user",
        )
    assert skipped == expected_skip
    if expected_skip is None:
        set_status.assert_awaited_once()
        emit.assert_awaited_once()
        metric.assert_called_once()
    else:
        set_status.assert_not_awaited()
        emit.assert_not_awaited()
        metric.assert_not_called()


@pytest.mark.asyncio
async def test_deprecate_superseded_resolves_superseding_api_from_revision_identity() -> None:
    """Without an explicit api id, the superseding API is resolved from the revision triple.

    Models the #1078 doubled-identity case: the re-import resolved the clean identity
    (``posthog-com/posthog-api``) and archived nothing (``superseded_revision_id`` is
    ``None``), so the resolver falls back to the enqueue-time overlay — which lives on the
    doubled row. The guard must see the API mismatch and leave the overlay confirmed.
    """
    handler = ImportHandler(_ctx())
    clean_api = MagicMock()
    clean_api.id = uuid.uuid4()
    overlay = _overlay(api_id=uuid.uuid4(), confirmed_revision_id=uuid.uuid4())
    superseding_revision = {
        "api": {"vendor": "posthog-com", "name": "posthog-api", "version": "1.0.0"},
        "revision_id": str(uuid.uuid4()),
        "superseded_revision_id": None,
        "state": "imported",
    }
    with (
        patch(f"{_SVC}.OverlayRepository.get_by_id", new_callable=AsyncMock, return_value=overlay),
        patch(
            f"{_SVC}.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=clean_api,
        ) as get_api,
        patch(f"{_SVC}.ApiRevisionRepository.state_of", new_callable=AsyncMock) as state_of,
        patch(f"{_SVC}.OverlayRepository.set_status", new_callable=AsyncMock) as set_status,
    ):
        skipped = await handler._deprecate_superseded_overlay(
            "job-1",
            "ovr_1",
            superseding_revision=superseding_revision,
            actor_id="usr_operator",
            actor_type="user",
        )
    assert skipped == "api_mismatch"
    get_api.assert_awaited_once()
    assert get_api.await_args is not None
    assert get_api.await_args.args[1:] == ("posthog-com", "posthog-api", "1.0.0")
    state_of.assert_not_awaited()
    set_status.assert_not_awaited()
