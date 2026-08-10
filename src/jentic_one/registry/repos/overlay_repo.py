"""Repository for Overlay entities."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum, auto
from typing import Any, cast

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.shared.models import OverlayStatus


class _Unset(Enum):
    TOKEN = auto()


_UNSET = _Unset.TOKEN


class OverlayRepository:
    """Data access layer for Overlay entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        document: dict[str, Any],
        target_revision_id: uuid.UUID | None = None,
        contributed_by: str | None = None,
        created_by: str,
    ) -> Overlay:
        overlay = Overlay(
            api_id=api_id,
            document=document,
            target_revision_id=target_revision_id,
            contributed_by=contributed_by,
            created_by=created_by,
        )
        session.add(overlay)
        await session.flush()
        return overlay

    @staticmethod
    async def get_for_api(
        session: AsyncSession, api_id: uuid.UUID, overlay_id: str
    ) -> Overlay | None:
        result = await session.execute(
            select(Overlay).where(Overlay.api_id == api_id, Overlay.id == overlay_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, overlay_id: str) -> Overlay | None:
        """Fetch an overlay by its id alone (no api scoping).

        Used by worker-side paths that hold only the overlay id — e.g. the A4b
        auto-deprecate, which needs the overlay's ``created_by`` (author) + ``api_id`` to
        emit the attributed ``overlay.deprecated`` notification (L2). Read-only; the
        status flip itself is a separate CAS via :meth:`set_status`.
        """
        result = await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_live_confirmed_for_revision(
        session: AsyncSession, api_id: uuid.UUID, revision_id: uuid.UUID
    ) -> Overlay | None:
        """The CONFIRMED overlay whose materialization *is* the given revision, if any.

        Used by the re-import collision check (A4a): "does adopting an upstream change
        for this API supersede a live operator overlay?" is answered by asking whether
        the API's current revision was itself produced by confirming an overlay. We match
        on ``confirmed_revision_id == revision_id`` (not merely ``status==CONFIRMED``) so a
        stale/relinked CONFIRMED overlay that no longer backs the served revision is not
        mistaken for the live one. At most one overlay can back a given revision (each
        materialize produces a fresh revision), so this returns 0 or 1.
        """
        result = await session.execute(
            select(Overlay).where(
                Overlay.api_id == api_id,
                Overlay.status == OverlayStatus.CONFIRMED,
                Overlay.confirmed_revision_id == revision_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_live_confirmed_for_api(
        session: AsyncSession, api_id: uuid.UUID
    ) -> Overlay | None:
        """The live CONFIRMED overlay for *api_id* (for actionable-event enrichment).

        Used by the Flow-3 sweep: once an upstream change is *already* classified as
        ``conflicts_overlay`` (the API's current revision is overlay-origin and carries an
        ``overlay_base_digest``), this returns the overlay to deep-link for keep/rollback.

        Prefers the overlay whose ``confirmed_revision_id`` **is** the API's current served
        revision — that is unambiguously the live one. Falls back to the newest-confirmed
        overlay only when no CONFIRMED overlay is linked to the current revision yet, which
        covers the lazy-link window: the materialize path promotes the overlay revision to
        current *before* it stamps ``confirmed_revision_id``, so for a brief window the live
        overlay is CONFIRMED with a NULL link while its revision is already served (dropping
        the id here would blank the conflict event). ``confirmed_revision_id`` cannot be
        keyed strictly (unlike :meth:`get_live_confirmed_for_revision`, the A4b *authorization*
        path) for that reason.

        Note the confirm path does not itself deprecate a prior CONFIRMED overlay, so two
        CONFIRMED rows for one API are reachable (confirm A, then confirm B stacks B on A's
        output and makes B current, leaving A CONFIRMED-but-superseded). The
        current-revision-link preference returns **B** (the live one); newest-confirmed is the
        correct fallback for the lazy-link window because the most recently materialized
        overlay is the one now served. Returns 0 or 1.

        This is enrichment only; the *authorization* decision (A4b) still uses the strict
        revision-keyed :meth:`get_live_confirmed_for_revision`.
        """
        # 0 when the overlay's confirmed_revision_id IS the current served revision (the
        # unambiguous live overlay), 1 otherwise — sorts the linked-live overlay first, then
        # newest-confirmed as the lazy-link fallback.
        current_first = case(
            (Overlay.confirmed_revision_id == Api.current_revision_id, 0),
            else_=1,
        )
        result = await session.execute(
            select(Overlay)
            .join(Api, Api.id == Overlay.api_id)
            .where(Overlay.api_id == api_id, Overlay.status == OverlayStatus.CONFIRMED)
            .order_by(
                current_first,
                Overlay.confirmed_at.desc().nulls_last(),
                Overlay.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_page(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        status: str | None = None,
    ) -> list[Overlay]:
        stmt = (
            select(Overlay)
            .where(Overlay.api_id == api_id)
            .order_by(Overlay.created_at.desc(), Overlay.id.desc())
            .limit(limit)
        )
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    Overlay.created_at < cursor_created_at,
                    and_(Overlay.created_at == cursor_created_at, Overlay.id < cursor_id),
                )
            )
        if status is not None:
            stmt = stmt.where(Overlay.status == status)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_fields(
        session: AsyncSession,
        overlay_id: str,
        *,
        document: dict[str, Any] | None = None,
        target_revision_id: uuid.UUID | None | _Unset = _UNSET,
        reset_to_pending: bool = False,
    ) -> int:
        values: dict[str, Any] = {"updated_at": func.now()}
        if document is not None:
            values["document"] = document
        if not isinstance(target_revision_id, _Unset):
            values["target_revision_id"] = target_revision_id
        if reset_to_pending:
            # Recovery lever for a CONFIRMED-but-unmaterialized overlay whose materialize
            # job fails deterministically: editing the document sends it back to PENDING
            # (clearing the stale confirm metadata) so the operator can re-confirm the fix
            # instead of re-enqueueing the same failing job forever.
            values["status"] = OverlayStatus.PENDING
            values["confirmed_at"] = None
            values["confirmed_by_execution_id"] = None
        result = cast(
            "CursorResult[Any]",
            await session.execute(update(Overlay).where(Overlay.id == overlay_id).values(**values)),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def set_status(
        session: AsyncSession,
        overlay_id: str,
        status: OverlayStatus,
        *,
        confirmed_at: datetime | None = None,
        confirmed_by_execution_id: str | None = None,
        deprecated_at: datetime | None = None,
        deprecated_reason: str | None = None,
        expected_status: OverlayStatus | None = None,
    ) -> int:
        """Set an overlay's status, returning the number of rows updated.

        When ``expected_status`` is given the UPDATE is guarded by ``status =
        expected_status`` (a compare-and-swap), so a caller can detect that it lost a
        race (``rowcount == 0``) instead of blindly overwriting a concurrent
        transition (e.g. two confirms, or confirm vs deprecate).
        """
        values: dict[str, Any] = {"status": status, "updated_at": func.now()}
        if confirmed_at is not None:
            values["confirmed_at"] = confirmed_at
        if confirmed_by_execution_id is not None:
            values["confirmed_by_execution_id"] = confirmed_by_execution_id
        if deprecated_at is not None:
            values["deprecated_at"] = deprecated_at
        if deprecated_reason is not None:
            values["deprecated_reason"] = deprecated_reason
        stmt = update(Overlay).where(Overlay.id == overlay_id)
        if expected_status is not None:
            stmt = stmt.where(Overlay.status == expected_status)
        result = cast(
            "CursorResult[Any]",
            await session.execute(stmt.values(**values)),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def set_confirmed_revision(
        session: AsyncSession,
        overlay_id: str,
        confirmed_revision_id: uuid.UUID,
        *,
        superseded_revision_id: uuid.UUID | None = None,
    ) -> int:
        """Record the revision produced by materializing this overlay.

        Written by the ingest job after a confirm's re-ingest succeeds, so the
        overlay points at the concrete revision now serving the overlaid spec.

        ``superseded_revision_id`` records the revision this materialization archived
        (the API's current revision immediately before), giving a later
        un-confirm/rollback (A5b) a deterministic prior-revision target. It is only
        written when non-``None`` — a recovery/relink that doesn't know the superseded
        revision must not overwrite a previously-captured value with ``NULL``.
        """
        values: dict[str, Any] = {
            "confirmed_revision_id": confirmed_revision_id,
            "updated_at": func.now(),
        }
        if superseded_revision_id is not None:
            values["superseded_revision_id"] = superseded_revision_id
        result = cast(
            "CursorResult[Any]",
            await session.execute(update(Overlay).where(Overlay.id == overlay_id).values(**values)),
        )
        await session.flush()
        return result.rowcount
