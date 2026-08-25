"""Repository for ApiRevision entities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import and_, case, delete, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.shared.models import ApiRevisionSourceType, ApiRevisionState


@dataclass(frozen=True)
class RegisteredSpec:
    """A sweep candidate: a registered API's spec URL + its current identity/digest."""

    api_id: uuid.UUID
    source_url: str
    spec_digest: str | None
    vendor: str
    name: str
    version: str
    #: Current revision's provenance marker (``"catalog"``, ``"overlay"``, or ``None``
    #: for a manual import). The update-notify sweep uses it to decide whether the spec
    #: is upstream-tracked (see ``ORIGIN_CATALOG`` / ``ORIGIN_OVERLAY``).
    origin: str | None
    #: For an overlay-origin current revision: the ``spec_digest`` of the base the
    #: overlay was materialized over. The sweep compares the upstream digest against
    #: this base to classify a change as a plain "update available" vs one that
    #: "conflicts with the overlay". ``None`` for non-overlay revisions and for overlay
    #: revisions materialized before A2 shipped the column (treated as "unknown base").
    overlay_base_digest: str | None = None


class ApiRevisionRepository:
    """Data access layer for ApiRevision entities — flush-only, never commits."""

    @staticmethod
    async def create_draft(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        spec_digest: str | None,
        source_type: ApiRevisionSourceType,
        source_url: str | None = None,
        source_filename: str | None = None,
        source_content_id: uuid.UUID | None = None,
        submitted_by: str | None = None,
        created_by: str,
    ) -> ApiRevision:
        revision = ApiRevision(
            api_id=api_id,
            state=ApiRevisionState.DRAFT,
            spec_digest=spec_digest,
            source_type=source_type,
            source_url=source_url,
            source_filename=source_filename,
            source_content_id=source_content_id,
            submitted_by=submitted_by,
            created_by=created_by,
        )
        session.add(revision)
        await session.flush()
        return revision

    @staticmethod
    async def create_imported(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        origin: str,
        spec_digest: str | None,
        source_type: ApiRevisionSourceType,
        source_url: str | None = None,
        source_filename: str | None = None,
        source_content_id: uuid.UUID | None = None,
        submitted_by: str | None = None,
        overlay_base_digest: str | None = None,
        created_by: str,
    ) -> ApiRevision:
        revision = ApiRevision(
            api_id=api_id,
            state=ApiRevisionState.IMPORTED,
            origin=origin,
            spec_digest=spec_digest,
            source_type=source_type,
            source_url=source_url,
            source_filename=source_filename,
            source_content_id=source_content_id,
            submitted_by=submitted_by,
            overlay_base_digest=overlay_base_digest,
            promoted_at=datetime.now(UTC),
            created_by=created_by,
        )
        session.add(revision)
        await session.flush()
        return revision

    @staticmethod
    async def archive_active_imported(
        session: AsyncSession,
        api_id: uuid.UUID,
        origin: str,
    ) -> None:
        """Archive any existing IMPORTED revision for an API + origin pair."""
        stmt = (
            select(ApiRevision)
            .where(
                ApiRevision.api_id == api_id,
                ApiRevision.state == ApiRevisionState.IMPORTED,
                ApiRevision.origin == origin,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.state = ApiRevisionState.ARCHIVED
            existing.archived_at = datetime.now(UTC)
            await session.flush()

    @staticmethod
    async def archive_all_active_imported(
        session: AsyncSession,
        api_id: uuid.UUID,
    ) -> None:
        """Archive all IMPORTED revisions for an API regardless of origin."""
        now = datetime.now(UTC)
        await session.execute(
            update(ApiRevision)
            .where(
                ApiRevision.api_id == api_id,
                ApiRevision.state == ApiRevisionState.IMPORTED,
            )
            .values(state=ApiRevisionState.ARCHIVED, archived_at=now)
            # Keep in-session ApiRevision instances consistent with the bulk
            # UPDATE so callers that later read them don't see stale state. See #642.
            .execution_options(synchronize_session="fetch")
        )
        await session.flush()

    @staticmethod
    async def archive_all_active(
        session: AsyncSession,
        api_id: uuid.UUID,
    ) -> None:
        """Archive every *active* revision (PUBLISHED or IMPORTED) for an API.

        The one-active partial unique index (``ix_api_revisions_one_active``) covers
        ``state IN ('published','imported')``, so a new active revision must supersede
        whichever of those is currently live. Used by overlay materialization, whose
        base may be a manually-promoted PUBLISHED revision (not just an IMPORTED one).
        """
        now = datetime.now(UTC)
        await session.execute(
            update(ApiRevision)
            .where(
                ApiRevision.api_id == api_id,
                ApiRevision.state.in_([ApiRevisionState.PUBLISHED, ApiRevisionState.IMPORTED]),
            )
            .values(state=ApiRevisionState.ARCHIVED, archived_at=now)
            .execution_options(synchronize_session="fetch")
        )
        await session.flush()

    @staticmethod
    async def get_by_digest(
        session: AsyncSession, api_id: uuid.UUID, spec_digest: str
    ) -> ApiRevision | None:
        # Precondition: spec_digest is a real (non-NULL) digest. A NULL digest can
        # never match under `spec_digest == :value` (SQL `= NULL` is never true), so
        # the sha-less case is handled upstream by skipping this lookup (#780) rather
        # than being widened to str | None here.
        result = await session.execute(
            select(ApiRevision).where(
                ApiRevision.api_id == api_id, ApiRevision.spec_digest == spec_digest
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def latest_archived_non_overlay(
        session: AsyncSession, api_id: uuid.UUID, overlay_origin: str
    ) -> uuid.UUID | None:
        """The most-recently-archived non-overlay revision for an API, if any.

        Used to *reconstruct* the revision an overlay materialize superseded when the
        original confirm crashed after committing the re-ingest (which archived that
        revision) but before back-linking it onto the overlay — the recovery re-ingest
        no longer carries the superseded id in memory. A successful materialize archives
        exactly the prior current (non-overlay) revision and nothing archives afterwards
        until the next confirm, so the newest such archive is that superseded revision.
        Best-effort: ``None`` if the API never had a non-overlay revision (a first-ever
        materialize superseded nothing) — the overlay then legitimately has no rollback
        target.
        """
        result = await session.execute(
            select(ApiRevision.id)
            .where(
                ApiRevision.api_id == api_id,
                ApiRevision.state == ApiRevisionState.ARCHIVED,
                or_(ApiRevision.origin != overlay_origin, ApiRevision.origin.is_(None)),
            )
            .order_by(ApiRevision.archived_at.desc(), ApiRevision.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def current_revision_for_source_url(
        session: AsyncSession, source_url: str
    ) -> tuple[uuid.UUID, uuid.UUID] | None:
        """The (api_id, current_revision_id) of a local API served from ``source_url``.

        Resolves a catalog entry (identified by its upstream ``spec_url``) to the locally
        registered API and its *current* revision, so the A4 collision check can ask
        whether re-importing that upstream would supersede a live confirmed overlay. Keys
        on the current revision's ``source_url`` — the same provenance the Flow-3 sweep
        uses — and requires the API to actually have a current revision (a bare draft
        import that was never promoted has none, so there's nothing to supersede).
        Returns ``None`` when no such API is registered.
        """
        result = await session.execute(
            select(Api.id, Api.current_revision_id)
            .join(ApiRevision, ApiRevision.id == Api.current_revision_id)
            .where(ApiRevision.source_url == source_url)
            .limit(1)
        )
        row = result.first()
        if row is None or row[1] is None:
            return None
        return (row[0], row[1])

    @staticmethod
    async def origin_of(session: AsyncSession, revision_id: uuid.UUID) -> str | None:
        """The ``origin`` of a revision by id, or ``None`` if it doesn't exist.

        A minimal single-column lookup used by the supersede-recovery path to decide
        whether the served revision is the fresh upstream (non-overlay) — i.e. whether an
        interrupted supersede's durable effect already landed — without materializing the
        whole ORM row.
        """
        result = await session.execute(
            select(ApiRevision.origin).where(ApiRevision.id == revision_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def archive_one(session: AsyncSession, revision_id: uuid.UUID) -> int:
        """Archive a single *active* revision by id (CAS on state). Returns rowcount.

        Used by overlay rollback (A5b): the current overlay revision is archived in the
        same txn as restoring the superseded one, guarded on ``state IN (published,
        imported)`` so a concurrent transition that already archived it yields rowcount 0
        (the caller aborts rather than double-acting).
        """
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(ApiRevision)
                .where(
                    ApiRevision.id == revision_id,
                    ApiRevision.state.in_([ApiRevisionState.PUBLISHED, ApiRevisionState.IMPORTED]),
                )
                .values(state=ApiRevisionState.ARCHIVED, archived_at=datetime.now(UTC))
                .execution_options(synchronize_session="fetch")
            ),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def restore_archived(session: AsyncSession, revision_id: uuid.UUID) -> int:
        """Un-archive a revision to its *true prior state*, clearing ``archived_at``.

        Rollback (A5b) restores the revision an overlay superseded so it can serve again.
        The restored state must match what the revision was before materialization archived
        it — an overlay base can be a manually-promoted ``PUBLISHED`` revision, not only an
        ``IMPORTED`` one (see ``archive_all_active``), and always restoring to ``IMPORTED``
        silently drops the ``published`` label (#939).

        The prior state is derived from durable provenance rather than a captured column,
        which survives archival: ``PUBLISHED`` is only ever produced by
        ``RevisionService.promote`` (promoting a ``DRAFT``, which ``create_draft`` leaves
        with ``origin IS NULL``); ``IMPORTED`` is only ever produced by ``create_imported``
        (which always sets ``origin``). So ``origin IS NULL`` ⇒ restore to ``PUBLISHED``,
        else ``IMPORTED``. The decision is a single SQL ``CASE`` so it stays one atomic
        UPDATE (no read-then-write race).

        CAS on ``state == ARCHIVED`` so only a genuinely archived revision is restored
        (rowcount 0 otherwise). The caller archives the current overlay revision *first*
        in the same txn, so the one-active partial unique index is never transiently
        violated (never two active revisions at once).
        """
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(ApiRevision)
                .where(
                    ApiRevision.id == revision_id,
                    ApiRevision.state == ApiRevisionState.ARCHIVED,
                )
                .values(
                    state=case(
                        (ApiRevision.origin.is_(None), ApiRevisionState.PUBLISHED),
                        else_=ApiRevisionState.IMPORTED,
                    ),
                    archived_at=None,
                )
                .execution_options(synchronize_session="fetch")
            ),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def delete_replaceable_by_digest(
        session: AsyncSession, api_id: uuid.UUID, spec_digest: str
    ) -> int:
        """Delete any leftover, non-active revision with this (api_id, spec_digest).

        Makes re-import idempotent: an abandoned ``draft`` (or an ``archived``
        revision) sharing the target digest would otherwise collide with
        ``uq_api_revisions_api_id_spec_digest``. Active ``published``/``imported``
        revisions are never touched — a live revision with the same content is a
        genuine conflict that must surface, not be silently overwritten. Child
        rows cascade via the ``ondelete="CASCADE"`` foreign keys.

        Precondition: ``spec_digest`` is a real (non-NULL) digest. A NULL digest
        can never match ``spec_digest == :value``, so the sha-less case is handled
        upstream by skipping this call (#780) rather than being widened here.
        """
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                delete(ApiRevision).where(
                    ApiRevision.api_id == api_id,
                    ApiRevision.spec_digest == spec_digest,
                    ApiRevision.state.in_([ApiRevisionState.DRAFT, ApiRevisionState.ARCHIVED]),
                )
            ),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def set_operation_count(
        session: AsyncSession, revision_id: uuid.UUID, count: int
    ) -> None:
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(ApiRevision)
                .where(ApiRevision.id == revision_id)
                .values(operation_count=count)
            ),
        )
        if result.rowcount == 0:
            msg = f"ApiRevision {revision_id} not found"
            raise ValueError(msg)
        await session.flush()

    @staticmethod
    async def list_page(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        states: list[str] | None = None,
    ) -> list[ApiRevision]:
        stmt = (
            select(ApiRevision)
            .where(ApiRevision.api_id == api_id)
            .order_by(ApiRevision.created_at.desc(), ApiRevision.id.desc())
            .limit(limit)
        )
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    ApiRevision.created_at < cursor_created_at,
                    and_(
                        ApiRevision.created_at == cursor_created_at,
                        ApiRevision.id < cursor_id,
                    ),
                )
            )
        if states:
            stmt = stmt.where(ApiRevision.state.in_(states))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_for_api(
        session: AsyncSession, api_id: uuid.UUID, revision_id: uuid.UUID
    ) -> ApiRevision | None:
        stmt = (
            select(ApiRevision)
            .where(ApiRevision.api_id == api_id, ApiRevision.id == revision_id)
            .options(selectinload(ApiRevision.servers))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_state(
        session: AsyncSession,
        revision_id: uuid.UUID,
        state: ApiRevisionState,
        *,
        promoted_at: datetime | None = None,
        archived_at: datetime | None = None,
    ) -> int:
        values: dict[str, Any] = {"state": state}
        if promoted_at is not None:
            values["promoted_at"] = promoted_at
        if archived_at is not None:
            values["archived_at"] = archived_at
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(ApiRevision)
                .where(ApiRevision.id == revision_id)
                .values(**values)
                # Keep the in-session instance consistent with the bulk UPDATE so
                # callers that later read it don't see stale state. See #642.
                .execution_options(synchronize_session="fetch")
            ),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def delete(session: AsyncSession, revision_id: uuid.UUID) -> int:
        result = cast(
            "CursorResult[Any]",
            await session.execute(delete(ApiRevision).where(ApiRevision.id == revision_id)),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def registered_specs_for_notify(
        session: AsyncSession,
    ) -> list[RegisteredSpec]:
        """Non-archived revisions that carry a ``source_url`` — the sweep candidates.

        Joins each candidate revision to its API's identity + current spec digest so
        the update-notify sweep can build an event payload and dedupe without a
        second query. Exactly one row per ``api_id``: an API commonly has multiple
        non-archived revisions (imports create drafts that are never auto-promoted),
        so we must pick a **deterministic** one or the sweep would compare upstream
        against a row-order-dependent digest/``source_url`` and fire spurious
        notifications. Selection order: the API's ``current_revision_id`` if set,
        then newest ``created_at``, then ``id`` as a final tiebreak (matching the
        ``ix_api_revisions_api_id_created_at_id`` index).
        """
        # 0 for the API's current revision, 1 otherwise — sorts current first.
        current_first = case(
            (ApiRevision.id == Api.current_revision_id, 0),
            else_=1,
        )
        result = await session.execute(
            select(
                ApiRevision.api_id,
                ApiRevision.source_url,
                ApiRevision.spec_digest,
                ApiRevision.origin,
                ApiRevision.overlay_base_digest,
                Api.vendor,
                Api.name,
                Api.version,
            )
            .join(Api, Api.id == ApiRevision.api_id)
            .where(ApiRevision.source_url.is_not(None))
            .where(ApiRevision.state != ApiRevisionState.ARCHIVED)
            .order_by(
                ApiRevision.api_id,
                current_first,
                ApiRevision.created_at.desc(),
                ApiRevision.id,
            )
        )
        seen: set[uuid.UUID] = set()
        specs: list[RegisteredSpec] = []
        for (
            api_id,
            source_url,
            spec_digest,
            origin,
            overlay_base_digest,
            vendor,
            name,
            version,
        ) in result.all():
            if api_id in seen:
                continue
            seen.add(api_id)
            specs.append(
                RegisteredSpec(
                    api_id=api_id,
                    source_url=source_url,
                    spec_digest=spec_digest,
                    vendor=vendor,
                    name=name,
                    version=version,
                    origin=origin,
                    overlay_base_digest=overlay_base_digest,
                )
            )
        return specs
