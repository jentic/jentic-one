"""Unit tests for the overlay service lifecycle operations."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.services.errors import (
    ApiNotFoundError,
    InvalidOverlayDocumentError,
    NoCurrentRevisionError,
    OverlayApplyConflictError,
    OverlayNotFoundError,
    OverlayRematerializeForbiddenError,
    OverlayRollbackTargetMissingError,
    OverlayStateConflictError,
)
from jentic_one.registry.services.overlay_service import (
    OverlayService,
    _concrete_server_urls,
    _iter_server_urls,
    _validate_overlay_document,
)
from jentic_one.shared.auth.identity import Identity

_IDENTITY = Identity(sub="usr_test", email="test@example.com")
#: An operator identity that holds overlays:confirm — required to re-materialize an
#: overlay on edit (D1). The plain _IDENTITY above holds only the route floor (apis:write).
_OPERATOR = Identity(
    sub="usr_operator", email="operator@example.com", permissions=["overlays:confirm"]
)

_BASE_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Pets", "version": "1.0.0"},
    "servers": [{"url": "https://old.example.com"}],
    "paths": {},
}

# A remove-then-set overlay that rewrites servers to a safe URL.
_GOOD_DOC = {
    "overlay": "1.0.0",
    "actions": [
        {"target": "$.servers", "remove": True},
        {"target": "$", "update": {"servers": [{"url": "https://new.example.com"}]}},
    ],
}


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.registry_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    # Real egress config so the SSRF check in _apply_and_validate runs against actual policy.
    ctx.config.ingest.egress = None
    ctx.config.ingest.max_spec_bytes = 25 * 1024 * 1024
    return ctx


def _make_api_with_revision() -> MagicMock:
    """An Api whose current revision resolves to _BASE_SPEC (for confirm materialize)."""
    api = _make_api()
    api.current_revision_id = uuid.uuid4()
    api.current_revision = MagicMock()
    api.current_revision.source_url = "https://catalog.example.com/pets.json"
    return api


def _make_api() -> MagicMock:
    api = MagicMock()
    api.id = uuid.uuid4()
    api.vendor = "acme"
    api.name = "pets"
    api.version = "v1"
    return api


def _make_overlay(
    *,
    status: str = "pending",
    overlay_id: str = "ovr_abc123def456ghi789",
    confirmed_revision_id: uuid.UUID | None = None,
) -> MagicMock:
    overlay = MagicMock()
    overlay.id = overlay_id
    overlay.api_id = uuid.uuid4()
    overlay.status = status
    overlay.document = {"overlay": "1.0", "actions": []}
    overlay.target_revision_id = None
    overlay.confirmed_revision_id = confirmed_revision_id
    overlay.superseded_revision_id = None
    overlay.contributed_by = "agent"
    overlay.confirmed_by_execution_id = None
    overlay.created_at = datetime(2024, 6, 1, tzinfo=UTC)
    overlay.updated_at = None
    overlay.confirmed_at = None
    overlay.deprecated_at = None
    return overlay


@pytest.mark.asyncio
async def test_submit_returns_overlay_view() -> None:
    ctx = _make_ctx()
    api = _make_api()
    overlay = _make_overlay()

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.create",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
    ):
        svc = OverlayService(ctx)
        view = await svc.submit("acme", "pets", "v1", document={"actions": []}, identity=_IDENTITY)

    assert view.id == overlay.id
    assert view.id.startswith("ovr_")
    assert view.vendor == "acme"
    assert view.status == "pending"


@pytest.mark.asyncio
async def test_submit_api_not_found_raises_404() -> None:
    ctx = _make_ctx()

    with patch(
        "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
        new_callable=AsyncMock,
        return_value=None,
    ):
        svc = OverlayService(ctx)
        with pytest.raises(ApiNotFoundError):
            await svc.submit("acme", "missing", "v1", document={"actions": []}, identity=_IDENTITY)


@pytest.mark.asyncio
async def test_get_valid_overlay() -> None:
    ctx = _make_ctx()
    api = _make_api()
    overlay = _make_overlay()

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
    ):
        svc = OverlayService(ctx)
        view = await svc.get("acme", "pets", "v1", "ovr_abc123def456ghi789")

    assert view.id == overlay.id
    assert view.status == "pending"


@pytest.mark.asyncio
async def test_get_invalid_id_raises_not_found() -> None:
    ctx = _make_ctx()

    with patch(
        "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
        new_callable=AsyncMock,
        return_value=_make_api(),
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayNotFoundError):
            await svc.get("acme", "pets", "v1", "bad-id-no-prefix")


@pytest.mark.asyncio
async def test_get_nonexistent_overlay_raises_not_found() -> None:
    ctx = _make_ctx()
    api = _make_api()

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayNotFoundError):
            await svc.get("acme", "pets", "v1", "ovr_doesnotexist00000000")


@pytest.mark.asyncio
async def test_list_page_with_status_filter() -> None:
    ctx = _make_ctx()
    api = _make_api()
    overlay = _make_overlay()

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.list_page",
            new_callable=AsyncMock,
            return_value=[overlay],
        ) as mock_list,
    ):
        svc = OverlayService(ctx)
        page = await svc.list_page("acme", "pets", "v1", limit=50, status="pending")

    assert len(page.data) == 1
    assert page.has_more is False
    assert page.next_cursor is None
    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["status"] == "pending"


@pytest.mark.asyncio
async def test_list_page_pagination() -> None:
    ctx = _make_ctx()
    api = _make_api()
    overlays = [_make_overlay(overlay_id=f"ovr_{i:024d}") for i in range(3)]

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.list_page",
            new_callable=AsyncMock,
            return_value=overlays,
        ),
    ):
        svc = OverlayService(ctx)
        page = await svc.list_page("acme", "pets", "v1", limit=2)

    assert len(page.data) == 2
    assert page.has_more is True
    assert page.next_cursor is not None


@pytest.mark.asyncio
async def test_update_pending_succeeds() -> None:
    ctx = _make_ctx()
    api = _make_api()
    overlay = _make_overlay(status="pending")
    updated_overlay = _make_overlay(status="pending")
    updated_overlay.document = {"updated": True}

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[overlay, overlay, updated_overlay],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.update_fields",
            new_callable=AsyncMock,
            return_value=1,
        ),
    ):
        svc = OverlayService(ctx)
        view = await svc.update(
            "acme",
            "pets",
            "v1",
            "ovr_abc123def456ghi789",
            document={"actions": [{"target": "$.info", "update": {"title": "x"}}]},
            identity=_IDENTITY,
        )

    assert view.document == {"updated": True}


@pytest.mark.asyncio
async def test_update_materialized_without_confirm_scope_forbidden() -> None:
    """Editing a materialized overlay re-materializes it — an operator action.

    A caller with only ``apis:write`` (the ordinary contributor scope, no
    ``overlays:confirm``) editing a live materialized overlay is refused with a 403
    (``OverlayRematerializeForbiddenError``) before anything is mutated — it would
    otherwise rewrite the served spec.
    """
    ctx = _make_ctx()
    api = _make_api_with_revision()
    overlay = _make_overlay(status="confirmed", confirmed_revision_id=uuid.uuid4())

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayRematerializeForbiddenError) as exc_info:
            await svc.update(
                "acme",
                "pets",
                "v1",
                "ovr_abc123def456ghi789",
                document=_GOOD_DOC,
                identity=_IDENTITY,
            )
        assert exc_info.value.overlay_id == "ovr_abc123def456ghi789"
    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_materialized_re_materializes_over_clean_base() -> None:
    """Editing a live materialized overlay re-applies the edit over the ORIGINAL base.

    The base to re-apply over is the overlay's ``superseded_revision_id`` (its pre-overlay
    base), never its own current output. The edited document is persisted, then a
    re-ingest job is enqueued carrying the overlaid spec built from the clean base + the
    base's provenance/digest.
    """
    ctx = _make_ctx()
    overlay_rev = uuid.uuid4()
    clean_base_rev = uuid.uuid4()
    api = _make_api()
    api.current_revision_id = overlay_rev  # the overlay is live (current == confirmed rev)

    overlay = _make_overlay(status="confirmed", confirmed_revision_id=overlay_rev)
    overlay.superseded_revision_id = clean_base_rev

    live = MagicMock()
    live.current_revision_id = overlay_rev

    base_revision = MagicMock()
    base_revision.source_url = "https://catalog.example.com/pets.json"
    base_revision.spec_digest = "sha256:cleanbase"
    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    refreshed = _make_overlay(status="confirmed", confirmed_revision_id=overlay_rev)

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=live,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[overlay, overlay, overlay, refreshed],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.update_fields",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_update,
        patch(
            "jentic_one.registry.services.overlay_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=base_revision,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
            return_value="job_1",
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        view = await svc.update(
            "acme",
            "pets",
            "v1",
            "ovr_abc123def456ghi789",
            document=_GOOD_DOC,
            identity=_OPERATOR,
        )

    # The document edit was persisted (no reset_to_pending on a re-materialize).
    assert mock_update.await_count == 1
    assert mock_update.call_args.kwargs.get("reset_to_pending", False) is False
    # A re-ingest was enqueued with the overlaid spec built from the CLEAN base.
    mock_enqueue.assert_awaited_once()
    payload = mock_enqueue.call_args.kwargs["payload"]
    assert payload["overlay_id"] == "ovr_abc123def456ghi789"
    source = payload["sources"][0]
    assert source["origin"] == "overlay"
    assert source["overlay_base_digest"] == "sha256:cleanbase"
    assert source["source_url"] == "https://catalog.example.com/pets.json"
    overlaid = json.loads(source["content"])
    # _GOOD_DOC rewrites servers onto the clean base — proving apply ran over _BASE_SPEC.
    assert overlaid["servers"] == [{"url": "https://new.example.com"}]
    assert view.status == "confirmed"


@pytest.mark.asyncio
async def test_update_materialized_metadata_only_is_in_place_field_edit() -> None:
    """A metadata-only edit (document=None) of a materialized overlay does NOT re-materialize.

    Changing only advisory metadata (e.g. target_revision_id) rewrites nothing served, so it
    stays an ordinary in-place field edit: no re-ingest is enqueued, and it does not require
    overlays:confirm (the plain apis:write _IDENTITY suffices).
    """
    ctx = _make_ctx()
    api = _make_api()
    overlay = _make_overlay(status="confirmed", confirmed_revision_id=uuid.uuid4())
    updated = _make_overlay(status="confirmed", confirmed_revision_id=overlay.confirmed_revision_id)

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[overlay, overlay, updated],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.update_fields",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_update,
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        view = await svc.update(
            "acme",
            "pets",
            "v1",
            "ovr_abc123def456ghi789",
            document=None,
            target_revision_id=uuid.uuid4(),
            identity=_IDENTITY,
        )

    mock_enqueue.assert_not_awaited()  # no re-ingest for a metadata-only edit
    assert mock_update.await_count == 1
    assert mock_update.call_args.kwargs["document"] is None
    assert view.status == "confirmed"


@pytest.mark.asyncio
async def test_update_materialized_not_live_conflicts() -> None:
    """Re-materialize refuses when the overlay is no longer the live revision.

    If the API's current revision has moved off the overlay's confirmed revision (rolled
    back or re-imported since), there is no deterministic served spec to re-materialize
    onto — raise a conflict and enqueue nothing.
    """
    ctx = _make_ctx()
    overlay_rev = uuid.uuid4()
    api = _make_api()
    api.current_revision_id = overlay_rev
    overlay = _make_overlay(status="confirmed", confirmed_revision_id=overlay_rev)
    overlay.superseded_revision_id = uuid.uuid4()

    live = MagicMock()
    live.current_revision_id = uuid.uuid4()  # moved off the overlay revision

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=live,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[overlay, overlay],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayStateConflictError) as exc_info:
            await svc.update(
                "acme",
                "pets",
                "v1",
                "ovr_abc123def456ghi789",
                document=_GOOD_DOC,
                identity=_OPERATOR,
            )
        assert exc_info.value.action == "update"
    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_materialized_in_tx_race_bails_without_enqueue() -> None:
    """The in-tx re-read guard bails idempotently when the overlay changed during apply.

    Preconditions pass on the short read and the edit is applied over the clean base, but by
    the time we re-read inside the persist transaction the overlay's superseded_revision_id
    has moved (a concurrent rollback/re-import/edit landed). The guard must NOT persist or
    enqueue — it returns the current view idempotently (the persisted doc and the enqueued
    revision must always agree; re-issuing the PATCH self-heals).
    """
    ctx = _make_ctx()
    overlay_rev = uuid.uuid4()
    clean_base_rev = uuid.uuid4()
    api = _make_api()
    api.current_revision_id = overlay_rev

    overlay = _make_overlay(status="confirmed", confirmed_revision_id=overlay_rev)
    overlay.superseded_revision_id = clean_base_rev

    live = MagicMock()
    live.current_revision_id = overlay_rev

    base_revision = MagicMock()
    base_revision.source_url = "https://catalog.example.com/pets.json"
    base_revision.spec_digest = "sha256:cleanbase"
    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    # The in-tx re-read returns an overlay whose clean base has since moved — the guard's
    # `current.superseded_revision_id != base_revision_id` branch fires and bails.
    raced = _make_overlay(status="confirmed", confirmed_revision_id=overlay_rev)
    raced.superseded_revision_id = uuid.uuid4()

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=live,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            # routing read, short-read, in-tx re-read (raced → bail).
            side_effect=[overlay, overlay, raced],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=base_revision,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.update_fields",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_update,
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        view = await svc.update(
            "acme",
            "pets",
            "v1",
            "ovr_abc123def456ghi789",
            document=_GOOD_DOC,
            identity=_OPERATOR,
        )

    # Bailed idempotently: neither persisted the edit nor enqueued a re-ingest.
    mock_update.assert_not_awaited()
    mock_enqueue.assert_not_awaited()
    # Returned the current (raced) view rather than raising.
    assert view.status == "confirmed"


@pytest.mark.asyncio
async def test_update_materialized_without_superseded_base_raises() -> None:
    """Re-materialize needs a recorded clean base to re-apply over.

    A first-ever materialize superseded nothing (``superseded_revision_id`` is NULL), so
    there is no clean base to re-apply the edit over — surface
    ``OverlayRollbackTargetMissingError`` (409) rather than double-applying.
    """
    ctx = _make_ctx()
    overlay_rev = uuid.uuid4()
    api = _make_api()
    api.current_revision_id = overlay_rev
    overlay = _make_overlay(status="confirmed", confirmed_revision_id=overlay_rev)
    overlay.superseded_revision_id = None

    live = MagicMock()
    live.current_revision_id = overlay_rev

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=live,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[overlay, overlay],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayRollbackTargetMissingError):
            await svc.update(
                "acme",
                "pets",
                "v1",
                "ovr_abc123def456ghi789",
                document=_GOOD_DOC,
                identity=_OPERATOR,
            )
    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_stuck_confirmed_resets_to_pending() -> None:
    """A CONFIRMED-but-unmaterialized overlay is editable and resets to PENDING.

    If the materialize job fails deterministically the overlay is left CONFIRMED with a
    null confirmed_revision_id and re-confirm only re-enqueues the same failure. Editing
    the document is the operator's escape hatch: it must be allowed and must reset the
    overlay to PENDING (clearing stale confirm metadata) for a fresh confirm.
    """
    ctx = _make_ctx()
    api = _make_api()
    stuck = _make_overlay(status="confirmed", confirmed_revision_id=None)
    reset = _make_overlay(status="pending")
    reset.document = {"updated": True}

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[stuck, stuck, reset],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.update_fields",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_update,
    ):
        svc = OverlayService(ctx)
        view = await svc.update(
            "acme",
            "pets",
            "v1",
            "ovr_abc123def456ghi789",
            document={"actions": [{"target": "$.info", "update": {"title": "x"}}]},
            identity=_IDENTITY,
        )

    # Edit went through and requested the reset-to-PENDING back to an editable state.
    assert mock_update.call_args.kwargs["reset_to_pending"] is True
    assert view.status == "pending"


@pytest.mark.asyncio
async def test_confirm_pending_succeeds() -> None:
    ctx = _make_ctx()
    api = _make_api_with_revision()
    overlay = _make_overlay(status="pending")
    overlay.document = _GOOD_DOC
    confirmed_overlay = _make_overlay(status="confirmed")
    confirmed_overlay.confirmed_at = datetime(2024, 6, 2, tzinfo=UTC)
    confirmed_overlay.confirmed_by_execution_id = "exec-99"

    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository."
            "get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[overlay, overlay, confirmed_overlay],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
            return_value=1,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
            return_value="job_1",
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        view = await svc.confirm(
            "acme",
            "pets",
            "v1",
            "ovr_abc123def456ghi789",
            execution_id="exec-99",
            identity=_IDENTITY,
        )

    assert view.status == "confirmed"
    assert view.confirmed_by_execution_id == "exec-99"
    # A materialize job was enqueued with an overlay-origin inline source + overlay_id.
    mock_enqueue.assert_called_once()
    payload = mock_enqueue.call_args.kwargs["payload"]
    assert payload["overlay_id"] == "ovr_abc123def456ghi789"
    source = payload["sources"][0]
    assert source["type"] == "inline"
    assert source["origin"] == "overlay"
    assert source["source_url"] == "https://catalog.example.com/pets.json"
    # The inline content is the overlaid spec (servers rewritten).
    materialized = json.loads(source["content"])
    assert materialized["servers"] == [{"url": "https://new.example.com"}]


@pytest.mark.asyncio
async def test_confirm_rejects_unsafe_server_url() -> None:
    """An overlay that points servers at a private/loopback host is rejected."""
    ctx = _make_ctx()
    api = _make_api_with_revision()
    overlay = _make_overlay(status="pending")
    overlay.document = {
        "overlay": "1.0.0",
        "actions": [{"target": "$", "update": {"servers": [{"url": "http://169.254.169.254/"}]}}],
    }
    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository."
            "get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
        ) as mock_set_status,
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayApplyConflictError, match="unsafe servers"):
            await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)
    # Overlay stays pending, no job enqueued.
    mock_set_status.assert_not_called()
    mock_enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_rejects_drifted_overlay() -> None:
    """An overlay whose target no longer resolves against the base spec is rejected."""
    ctx = _make_ctx()
    api = _make_api_with_revision()
    overlay = _make_overlay(status="pending")
    overlay.document = {"actions": [{"target": "$.gone.missing", "update": {"x": 1}}]}
    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository."
            "get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
        ) as mock_set_status,
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayApplyConflictError, match="does not resolve"):
            await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)
    mock_set_status.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_no_current_revision_raises() -> None:
    ctx = _make_ctx()
    api = _make_api_with_revision()
    api.current_revision_id = None
    overlay = _make_overlay(status="pending")

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository."
            "get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
    ):
        svc = OverlayService(ctx)
        with pytest.raises(NoCurrentRevisionError):
            await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)


@pytest.mark.asyncio
async def test_confirm_already_confirmed_is_idempotent() -> None:
    ctx = _make_ctx()
    api = _make_api()
    overlay = _make_overlay(status="confirmed", confirmed_revision_id=uuid.uuid4())
    overlay.confirmed_at = datetime(2024, 6, 2, tzinfo=UTC)

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
        ) as mock_set_status,
    ):
        svc = OverlayService(ctx)
        view = await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)

    assert view.status == "confirmed"
    mock_set_status.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_deprecated_raises_conflict() -> None:
    ctx = _make_ctx()
    api = _make_api()
    overlay = _make_overlay(status="deprecated")

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayStateConflictError) as exc_info:
            await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)
        assert exc_info.value.action == "confirm"


@pytest.mark.asyncio
async def test_deprecate_sets_status() -> None:
    ctx = _make_ctx()
    api = _make_api()
    overlay = _make_overlay(status="pending")

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_set_status,
    ):
        svc = OverlayService(ctx)
        await svc.deprecate("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)

    mock_set_status.assert_called_once()
    assert mock_set_status.call_args.args[1] == "ovr_abc123def456ghi789"
    assert mock_set_status.call_args.args[2] == "deprecated"


@pytest.mark.asyncio
async def test_deprecate_nonexistent_raises_not_found() -> None:
    ctx = _make_ctx()
    api = _make_api()

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayNotFoundError):
            await svc.deprecate(
                "acme", "pets", "v1", "ovr_doesnotexist00000000", identity=_IDENTITY
            )


@pytest.mark.asyncio
async def test_confirm_recovers_confirmed_but_unmaterialized() -> None:
    """A CONFIRMED overlay with a null confirmed_revision_id re-enqueues (H2 recovery).

    Models the crash/enqueue-failure window: a prior confirm flipped status to
    CONFIRMED but the materialize job never landed. Re-confirming must re-drive
    materialization (re-enqueue) *without* re-claiming (set_status not called), so the
    stuck overlay is not left permanently unmaterialized.
    """
    ctx = _make_ctx()
    api = _make_api_with_revision()
    overlay = _make_overlay(status="confirmed", confirmed_revision_id=None)
    overlay.document = _GOOD_DOC
    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository."
            "get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
        ) as mock_set_status,
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
            return_value="job_recover",
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)

    # Recovery path: no re-claim, but the materialize job is re-enqueued.
    mock_set_status.assert_not_called()
    mock_enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_confirm_lost_race_returns_without_enqueue() -> None:
    """When the compare-and-swap claim loses (rowcount 0), confirm is idempotent (M1).

    Two concurrent confirms both read PENDING; the loser's guarded set_status matches no
    row (a concurrent confirm already flipped it), so it must return the current view and
    NOT enqueue a duplicate materialize job.
    """
    ctx = _make_ctx()
    api = _make_api_with_revision()
    overlay = _make_overlay(status="pending")
    overlay.document = _GOOD_DOC
    # After losing the race the re-read shows the winner's CONFIRMED state.
    winner_view = _make_overlay(status="confirmed", confirmed_revision_id=uuid.uuid4())
    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository."
            "get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[overlay, overlay, winner_view],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
            return_value=0,  # compare-and-swap matched no row → lost the race
        ),
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        view = await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)

    assert view.status == "confirmed"
    mock_enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_document_changed_during_apply_returns_without_enqueue() -> None:
    """A concurrent update landing during apply+validate is not silently materialized.

    confirm reads the document in tx1, applies+validates outside any transaction (a real
    window on a large spec), then re-reads inside the claim transaction. If an update
    changed the document in the meantime the CAS must be skipped so we never enqueue a
    materialize job carrying the *old* document while the row stores the *new* one — the
    doc/served divergence this PR otherwise defers, but happening inside a single confirm.
    """
    ctx = _make_ctx()
    api = _make_api_with_revision()
    overlay = _make_overlay(status="pending")
    overlay.document = _GOOD_DOC
    # Re-read inside the claim transaction shows a different document (a concurrent
    # update landed while apply+validate ran) — still PENDING, so the plain status CAS
    # would have succeeded; the document guard is what saves us.
    changed = _make_overlay(status="pending")
    changed.document = {"actions": [{"target": "$", "update": {"info": {"title": "X"}}}]}
    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository."
            "get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[overlay, changed],
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
        ) as mock_set_status,
        patch(
            "jentic_one.registry.services.overlay_service.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        svc = OverlayService(ctx)
        view = await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)

    # Neither claimed nor enqueued: returns the current (still PENDING, freshly-updated)
    # overlay so the caller re-confirms against the new document.
    mock_set_status.assert_not_called()
    mock_enqueue.assert_not_called()
    assert view.status == "pending"
    """SSRF validation covers operation-level servers[], not just the document root."""
    ctx = _make_ctx()
    api = _make_api_with_revision()
    overlay = _make_overlay(status="pending")
    overlay.document = {
        "overlay": "1.0.0",
        "actions": [
            {
                "target": "$",
                "update": {
                    "paths": {
                        "/pets": {"get": {"servers": [{"url": "http://169.254.169.254/latest/"}]}}
                    }
                },
            }
        ],
    }
    spec_file = MagicMock()
    spec_file.content = _BASE_SPEC

    with (
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.ApiRepository."
            "get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.SpecFileRepository.get_for_revision",
            new_callable=AsyncMock,
            return_value=spec_file,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=overlay,
        ),
        patch(
            "jentic_one.registry.services.overlay_service.OverlayRepository.set_status",
            new_callable=AsyncMock,
        ) as mock_set_status,
    ):
        svc = OverlayService(ctx)
        with pytest.raises(OverlayApplyConflictError, match="unsafe servers"):
            await svc.confirm("acme", "pets", "v1", "ovr_abc123def456ghi789", identity=_IDENTITY)
    mock_set_status.assert_not_called()


def test_validate_overlay_document_rejects_non_object() -> None:
    with pytest.raises(InvalidOverlayDocumentError, match="JSON object"):
        _validate_overlay_document([1, 2, 3])


def test_validate_overlay_document_requires_actions_array() -> None:
    with pytest.raises(InvalidOverlayDocumentError, match="actions"):
        _validate_overlay_document({"overlay": "1.0.0"})


def test_validate_overlay_document_rejects_too_many_actions() -> None:
    with pytest.raises(InvalidOverlayDocumentError, match="too many actions"):
        _validate_overlay_document({"actions": [{"target": "$"}] * 1000})


def test_validate_overlay_document_rejects_oversized() -> None:
    big = {"actions": [{"target": "$", "update": {"x": "a" * (1024 * 1024 + 10)}}]}
    with pytest.raises(InvalidOverlayDocumentError, match="exceeds"):
        _validate_overlay_document(big)


def test_validate_overlay_document_accepts_minimal() -> None:
    _validate_overlay_document({"actions": []})  # no raise


def test_iter_server_urls_covers_all_levels() -> None:
    spec = {
        "servers": [{"url": "https://root.example.com"}],
        "paths": {
            "/a": {
                "servers": [{"url": "https://path.example.com"}],
                "get": {"servers": [{"url": "https://op.example.com"}]},
            }
        },
    }
    urls = set(_iter_server_urls(spec))
    assert urls == {
        "https://root.example.com",
        "https://path.example.com",
        "https://op.example.com",
    }


def test_concrete_server_urls_non_templated() -> None:
    assert _concrete_server_urls("https://api.example.com") == ["https://api.example.com"]


def test_concrete_server_urls_templated_literal_host_kept() -> None:
    # A literal host with a templated path still yields a concrete host to validate.
    result = _concrete_server_urls("https://api.example.com/{version}")
    assert result == ["https://api.example.com/x"]


def test_concrete_server_urls_whole_host_templated_deferred() -> None:
    # Entire host is a variable → nothing literal to validate here; defer to runtime.
    assert _concrete_server_urls("https://{host}/v1") == []
