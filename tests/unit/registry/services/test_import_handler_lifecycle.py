"""Unit tests for ImportHandler Flow-3 lifecycle attribution (L2, L6).

Covers ``_deprecate_superseded_overlay``: on a successful CAS demote it emits an
attributed ``overlay.deprecated`` event (author + acting operator) and increments the
auto-deprecate metric; on a no-op demote (overlay not CONFIRMED) it emits nothing.
Repos/DB are mocked — this asserts wiring, not persistence (integration covers that).
"""

from __future__ import annotations

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


@pytest.mark.asyncio
async def test_deprecate_superseded_emits_attributed_event_and_metric() -> None:
    """A successful auto-deprecate emits an attributed overlay.deprecated + a metric (L2/L6)."""
    handler = ImportHandler(_ctx())
    overlay = MagicMock()
    overlay.created_by = "usr_author"
    overlay.api_id = "api-123"
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
            f"{_SVC}.OverlayRepository.get_by_id", new_callable=AsyncMock, return_value=MagicMock()
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
    overlay = MagicMock()
    overlay.created_by = "usr_author"
    overlay.api_id = "api-123"
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
