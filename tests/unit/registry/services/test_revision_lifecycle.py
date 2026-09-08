"""Unit tests for revision lifecycle state-machine (promote, archive, delete)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.services.errors import (
    ApiNotFoundError,
    RevisionNotFoundError,
    RevisionStateConflictError,
)
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity

_IDENTITY = Identity(sub="usr_test", email="test@example.com")


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    # expire_all is a synchronous AsyncSession method; keep it sync so the mock
    # doesn't emit "coroutine never awaited" warnings when promote calls it.
    mock_session.expire_all = MagicMock()
    ctx.registry_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_api(*, current_revision_id: uuid.UUID | None = None) -> MagicMock:
    api = MagicMock()
    api.id = uuid.uuid4()
    api.vendor = "acme"
    api.name = "widget"
    api.version = "1.0"
    api.display_name = "Widget"
    api.description = None
    api.icon_url = None
    api.current_revision_id = current_revision_id
    api.current_revision = None
    api.revision_count = 1
    api.operation_count = 0
    api.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    api.updated_at = datetime(2024, 1, 2, tzinfo=UTC)
    return api


def _make_revision(*, state: str = "draft", api_id: uuid.UUID | None = None) -> MagicMock:
    rev = MagicMock()
    rev.id = uuid.uuid4()
    rev.api_id = api_id or uuid.uuid4()
    rev.state = state
    rev.spec_digest = "sha256:abc"
    rev.source_type = "url"
    rev.source_url = "https://example.com/spec.yaml"
    rev.source_filename = None
    rev.submitted_by = "bot"
    rev.operation_count = 3
    rev.servers = []
    rev.promoted_at = None
    rev.archived_at = None
    rev.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    return rev


@pytest.mark.asyncio
async def test_promote_draft_succeeds() -> None:
    ctx = _make_ctx()
    old_pub_id = uuid.uuid4()
    api = _make_api(current_revision_id=old_pub_id)
    revision = _make_revision(state="draft", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.set_state",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_set_state,
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.set_current_revision",
            new_callable=AsyncMock,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiService._fetch_api_view",
            new_callable=AsyncMock,
        ) as mock_fetch,
    ):
        mock_fetch.return_value = MagicMock()
        svc = RevisionService(ctx)
        await svc.promote("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)

        calls = mock_set_state.call_args_list
        assert len(calls) == 2
        # First call archives old published revision
        assert calls[0].args[1] == old_pub_id
        assert calls[0].args[2] == "archived"
        # Second call promotes the draft
        assert calls[1].args[1] == revision.id
        assert calls[1].args[2] == "published"

        # The stale, bulk-updated identity map is expired before the view is
        # re-read so _fetch_api_view can't trigger a lazy load. See #642.
        session = ctx.registry_db.transaction.return_value.__aenter__.return_value
        session.expire_all.assert_called_once()


@pytest.mark.asyncio
async def test_promote_archives_imported_revisions() -> None:
    """Promoting a draft archives any active imported revisions for the same API."""
    ctx = _make_ctx()
    api = _make_api(current_revision_id=None)
    revision = _make_revision(state="draft", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.set_state",
            new_callable=AsyncMock,
            return_value=1,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.archive_all_active_imported",
            new_callable=AsyncMock,
        ) as mock_archive_imported,
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.set_current_revision",
            new_callable=AsyncMock,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiService._fetch_api_view",
            new_callable=AsyncMock,
        ) as mock_fetch,
    ):
        mock_fetch.return_value = MagicMock()
        svc = RevisionService(ctx)
        await svc.promote("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)

        mock_archive_imported.assert_called_once_with(
            mock_archive_imported.call_args.args[0], api.id
        )


@pytest.mark.asyncio
async def test_archive_imported_clears_current_revision_id() -> None:
    """Archiving an imported revision that is current clears Api.current_revision_id."""
    ctx = _make_ctx()
    revision = _make_revision(state="imported")
    api = _make_api(current_revision_id=revision.id)
    revision.api_id = api.id

    archived_rev = _make_revision(state="archived", api_id=api.id)
    archived_rev.id = revision.id
    archived_rev.archived_at = datetime(2024, 6, 1, tzinfo=UTC)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[revision, archived_rev],
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.set_state",
            new_callable=AsyncMock,
            return_value=1,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.clear_current_revision",
            new_callable=AsyncMock,
        ) as mock_clear,
    ):
        svc = RevisionService(ctx)
        await svc.archive("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)

        mock_clear.assert_called_once_with(mock_clear.call_args.args[0], api.id)


@pytest.mark.asyncio
async def test_promote_published_raises_409() -> None:
    ctx = _make_ctx()
    api = _make_api()
    revision = _make_revision(state="published", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
    ):
        svc = RevisionService(ctx)
        with pytest.raises(RevisionStateConflictError) as exc_info:
            await svc.promote("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)
        assert exc_info.value.action == "promote"
        assert exc_info.value.current_state == "published"


@pytest.mark.asyncio
async def test_promote_archived_raises_409() -> None:
    ctx = _make_ctx()
    api = _make_api()
    revision = _make_revision(state="archived", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
    ):
        svc = RevisionService(ctx)
        with pytest.raises(RevisionStateConflictError) as exc_info:
            await svc.promote("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)
        assert exc_info.value.action == "promote"


@pytest.mark.asyncio
async def test_archive_draft_succeeds() -> None:
    ctx = _make_ctx()
    api = _make_api()
    revision = _make_revision(state="draft", api_id=api.id)

    archived_rev = _make_revision(state="archived", api_id=api.id)
    archived_rev.id = revision.id
    archived_rev.archived_at = datetime(2024, 6, 1, tzinfo=UTC)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            side_effect=[revision, archived_rev],
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.set_state",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_set_state,
    ):
        svc = RevisionService(ctx)
        view = await svc.archive("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)

        mock_set_state.assert_called_once()
        assert mock_set_state.call_args.args[2] == "archived"
        assert view.state == "archived"


@pytest.mark.asyncio
async def test_archive_published_raises_409() -> None:
    ctx = _make_ctx()
    api = _make_api()
    revision = _make_revision(state="published", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
    ):
        svc = RevisionService(ctx)
        with pytest.raises(RevisionStateConflictError) as exc_info:
            await svc.archive("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)
        assert exc_info.value.action == "archive"


@pytest.mark.asyncio
async def test_archive_archived_raises_409() -> None:
    ctx = _make_ctx()
    api = _make_api()
    revision = _make_revision(state="archived", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
    ):
        svc = RevisionService(ctx)
        with pytest.raises(RevisionStateConflictError) as exc_info:
            await svc.archive("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)
        assert exc_info.value.action == "archive"


@pytest.mark.asyncio
async def test_delete_archived_succeeds() -> None:
    ctx = _make_ctx()
    api = _make_api()
    revision = _make_revision(state="archived", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.delete",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_delete,
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.apply_counts",
            new_callable=AsyncMock,
        ) as mock_counts,
    ):
        svc = RevisionService(ctx)
        await svc.delete("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)

        mock_delete.assert_called_once()
        mock_counts.assert_called_once()
        assert mock_counts.call_args.kwargs["revision_count_delta"] == -1


@pytest.mark.asyncio
async def test_delete_draft_raises_409() -> None:
    ctx = _make_ctx()
    api = _make_api()
    revision = _make_revision(state="draft", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
    ):
        svc = RevisionService(ctx)
        with pytest.raises(RevisionStateConflictError) as exc_info:
            await svc.delete("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)
        assert exc_info.value.action == "delete"


@pytest.mark.asyncio
async def test_delete_published_raises_409() -> None:
    ctx = _make_ctx()
    api = _make_api()
    revision = _make_revision(state="published", api_id=api.id)

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=revision,
        ),
    ):
        svc = RevisionService(ctx)
        with pytest.raises(RevisionStateConflictError) as exc_info:
            await svc.delete("acme", "widget", "1.0", str(revision.id), identity=_IDENTITY)
        assert exc_info.value.action == "delete"


@pytest.mark.asyncio
async def test_all_actions_unknown_revision_raises_404() -> None:
    ctx = _make_ctx()
    api = _make_api()

    with (
        patch(
            "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.revision_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        svc = RevisionService(ctx)
        fake_id = str(uuid.uuid4())

        with pytest.raises(RevisionNotFoundError):
            await svc.promote("acme", "widget", "1.0", fake_id, identity=_IDENTITY)

        with pytest.raises(RevisionNotFoundError):
            await svc.archive("acme", "widget", "1.0", fake_id, identity=_IDENTITY)

        with pytest.raises(RevisionNotFoundError):
            await svc.delete("acme", "widget", "1.0", fake_id, identity=_IDENTITY)


@pytest.mark.asyncio
async def test_all_actions_unknown_api_raises_404() -> None:
    ctx = _make_ctx()

    with patch(
        "jentic_one.registry.services.revision_service.ApiRepository.get_by_identifier",
        new_callable=AsyncMock,
        return_value=None,
    ):
        svc = RevisionService(ctx)
        fake_id = str(uuid.uuid4())

        with pytest.raises(ApiNotFoundError):
            await svc.promote("acme", "missing", "1.0", fake_id, identity=_IDENTITY)

        with pytest.raises(ApiNotFoundError):
            await svc.archive("acme", "missing", "1.0", fake_id, identity=_IDENTITY)

        with pytest.raises(ApiNotFoundError):
            await svc.delete("acme", "missing", "1.0", fake_id, identity=_IDENTITY)
