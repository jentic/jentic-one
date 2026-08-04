"""Unit tests for the A4b overlay-supersede gate on catalog re-import.

Covers ``CatalogService._authorize_overlay_supersede`` / ``import_entry``:
- no local API / no live confirmed overlay → ordinary import (no stamp, no refusal);
- collision + caller holds ``overlays:confirm`` → authorized supersede stamps the job;
- collision + caller lacks the scope → refused (403) and an operator-facing
  ``catalog.update_conflicts_overlay`` event is re-emitted (privilege-inversion guard).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.errors import OverlaySupersedeForbiddenError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.events import EventType

_SVC = "jentic_one.registry.services.catalog.service"
_API_ID = uuid.uuid4()
_REV_ID = uuid.uuid4()


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.update_sweep_lock = asyncio.Lock()
    session = AsyncMock()
    for db in (ctx.registry_db, ctx.admin_db):
        db.transaction.return_value.__aenter__ = AsyncMock(return_value=session)
        db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        db.session.return_value.__aenter__ = AsyncMock(return_value=session)
        db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _entry() -> MagicMock:
    entry = MagicMock()
    entry.spec_url = "https://raw.githubusercontent.com/x/y/main/openapi.json"
    entry.vendor = "acme"
    entry.api_id = "acme.com/widgets/1.0.0"
    return entry


def _identity(perms: list[str]) -> Identity:
    return Identity(sub="usr_x", email="x@test.local", permissions=perms)


@pytest.mark.asyncio
async def test_no_local_api_is_ordinary_import() -> None:
    """No registered API for the spec_url → nothing to supersede, plain import."""
    svc = CatalogService(_make_ctx())
    with patch(
        f"{_SVC}.ApiRevisionRepository.current_revision_for_source_url",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await svc._authorize_overlay_supersede(_entry(), _identity(["catalog:import"]))
    assert result is None


@pytest.mark.asyncio
async def test_no_live_overlay_is_ordinary_import() -> None:
    """Current revision is not an overlay's materialization → plain import."""
    svc = CatalogService(_make_ctx())
    with (
        patch(
            f"{_SVC}.ApiRevisionRepository.current_revision_for_source_url",
            new_callable=AsyncMock,
            return_value=(_API_ID, _REV_ID),
        ),
        patch(
            f"{_SVC}.OverlayRepository.get_live_confirmed_for_revision",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await svc._authorize_overlay_supersede(_entry(), _identity(["catalog:import"]))
    assert result is None


@pytest.mark.asyncio
async def test_collision_with_confirm_scope_authorizes_supersede() -> None:
    """A confirm-scope holder may supersede: returns the overlay id to stamp."""
    svc = CatalogService(_make_ctx())
    overlay = MagicMock()
    overlay.id = "ovr_abc"
    with (
        patch(
            f"{_SVC}.ApiRevisionRepository.current_revision_for_source_url",
            new_callable=AsyncMock,
            return_value=(_API_ID, _REV_ID),
        ),
        patch(
            f"{_SVC}.OverlayRepository.get_live_confirmed_for_revision",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(f"{_SVC}.emit_event_best_effort", new_callable=AsyncMock) as emit,
    ):
        result = await svc._authorize_overlay_supersede(_entry(), _identity(["overlays:confirm"]))
    assert result == "ovr_abc"
    emit.assert_not_awaited()  # authorized: no operator-facing refusal event


@pytest.mark.asyncio
async def test_collision_via_org_admin_authorizes_supersede() -> None:
    """org:admin expands to overlays:confirm, so an admin may supersede too."""
    svc = CatalogService(_make_ctx())
    overlay = MagicMock()
    overlay.id = "ovr_admin"
    with (
        patch(
            f"{_SVC}.ApiRevisionRepository.current_revision_for_source_url",
            new_callable=AsyncMock,
            return_value=(_API_ID, _REV_ID),
        ),
        patch(
            f"{_SVC}.OverlayRepository.get_live_confirmed_for_revision",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
    ):
        result = await svc._authorize_overlay_supersede(_entry(), _identity(["org:admin"]))
    assert result == "ovr_admin"


@pytest.mark.asyncio
async def test_collision_without_scope_refuses_and_reemits() -> None:
    """Low-privilege caller: refuse the silent revert and re-emit a conflict event."""
    svc = CatalogService(_make_ctx())
    overlay = MagicMock()
    overlay.id = "ovr_xyz"
    with (
        patch(
            f"{_SVC}.ApiRevisionRepository.current_revision_for_source_url",
            new_callable=AsyncMock,
            return_value=(_API_ID, _REV_ID),
        ),
        patch(
            f"{_SVC}.OverlayRepository.get_live_confirmed_for_revision",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(f"{_SVC}.emit_event_best_effort", new_callable=AsyncMock) as emit,
        pytest.raises(OverlaySupersedeForbiddenError),
    ):
        await svc._authorize_overlay_supersede(_entry(), _identity(["catalog:import"]))
    emit.assert_awaited_once()
    kwargs = emit.await_args.kwargs if emit.await_args else {}
    assert kwargs["type"] == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY
    assert kwargs["requires_action"] is True
    assert kwargs["data"]["overlay_id"] == "ovr_xyz"
    assert kwargs["data"]["event_class"] == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY


@pytest.mark.asyncio
async def test_import_entry_stamps_supersede_when_authorized() -> None:
    """import_entry threads supersede_overlay_id + supersede_active into the job payload."""
    svc = CatalogService(_make_ctx())
    with (
        patch.object(svc, "get", new_callable=AsyncMock, return_value=_entry()),
        patch.object(
            svc, "_authorize_overlay_supersede", new_callable=AsyncMock, return_value="ovr_1"
        ),
        patch.object(
            svc,
            "_to_import_source",
            return_value={"type": "url", "url": "https://u", "origin": "catalog"},
        ),
        patch(f"{_SVC}.enqueue_job", new_callable=AsyncMock, return_value="job_1") as enqueue,
    ):
        job_id = await svc.import_entry("acme.com/widgets/1.0.0", _identity(["overlays:confirm"]))
    assert job_id == "job_1"
    assert enqueue.await_args is not None
    payload = enqueue.await_args.kwargs["payload"]
    assert payload["supersede_overlay_id"] == "ovr_1"
    assert payload["sources"][0]["supersede_active"] == "true"


@pytest.mark.asyncio
async def test_import_entry_ordinary_when_no_collision() -> None:
    """No supersede → payload carries no supersede markers."""
    svc = CatalogService(_make_ctx())
    with (
        patch.object(svc, "get", new_callable=AsyncMock, return_value=_entry()),
        patch.object(
            svc, "_authorize_overlay_supersede", new_callable=AsyncMock, return_value=None
        ),
        patch.object(
            svc,
            "_to_import_source",
            return_value={"type": "url", "url": "https://u", "origin": "catalog"},
        ),
        patch(f"{_SVC}.enqueue_job", new_callable=AsyncMock, return_value="job_2") as enqueue,
    ):
        await svc.import_entry("acme.com/widgets/1.0.0", _identity(["catalog:import"]))
    assert enqueue.await_args is not None
    payload = enqueue.await_args.kwargs["payload"]
    assert "supersede_overlay_id" not in payload
    assert "supersede_active" not in payload["sources"][0]
