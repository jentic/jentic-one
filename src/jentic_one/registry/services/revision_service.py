"""Revision service — listing, retrieval, and lifecycle transitions for API revisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel

from jentic_one.registry.core.server_hosts import hosts_from_servers, needs_review
from jentic_one.registry.ingest.host_change_guard import (
    api_has_bound_credentials,
    may_approve_host_change,
)
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.repos.server_repo import ServerRepository
from jentic_one.registry.repos.url_index_repo import (
    UrlIndexRepository,
    describe_live_host_owners,
)
from jentic_one.registry.services.api_service import ApiService, ApiView
from jentic_one.registry.services.errors import (
    ApiNotFoundError,
    HostChangeRequiresOperatorError,
    HostOwnedByOtherVendorError,
    RevisionNotFoundError,
    RevisionStateConflictError,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionState
from jentic_one.shared.pagination import decode_cursor, encode_cursor


class RevisionPageItem(BaseModel):
    """View model for a single revision in a paginated list."""

    id: uuid.UUID
    api_id: uuid.UUID
    state: str
    origin: str | None
    spec_digest: str | None
    source_type: str | None
    source_url: str | None
    source_filename: str | None
    submitted_by: str | None
    operation_count: int
    host: str | None
    is_current: bool
    promoted_at: datetime | None
    archived_at: datetime | None
    created_at: datetime


class RevisionPage(BaseModel):
    """Paginated result of revisions."""

    data: list[RevisionPageItem]
    has_more: bool
    next_cursor: str | None = None


@dataclass(frozen=True)
class RevisionView:
    """Resolved view of a single revision with context for link construction."""

    id: uuid.UUID
    api_id: uuid.UUID
    vendor: str
    name: str
    version: str
    state: str
    origin: str | None
    spec_digest: str | None
    source_type: str | None
    source_url: str | None
    source_filename: str | None
    submitted_by: str | None
    operation_count: int
    host: str | None
    is_current: bool
    promoted_at: datetime | None
    archived_at: datetime | None
    created_at: datetime


class RevisionService:
    """Read operations for API revisions."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_revisions(
        self,
        *,
        vendor: str,
        name: str,
        version: str,
        states: list[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> RevisionPage:
        cursor_created_at = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_created_at, cursor_id = decode_cursor(cursor)

        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)

            rows = await ApiRevisionRepository.list_page(
                session,
                api_id=api.id,
                limit=limit + 1,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
                states=states,
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        revision_ids = [r.id for r in rows]
        server_hosts: dict[uuid.UUID, str | None] = {}
        if revision_ids:
            async with self._ctx.registry_db.session() as session:
                server_hosts = await ApiRepository.load_server_hosts(session, revision_ids)

        items: list[RevisionPageItem] = []
        for row in rows:
            items.append(
                RevisionPageItem(
                    id=row.id,
                    api_id=row.api_id,
                    state=row.state,
                    origin=row.origin,
                    spec_digest=row.spec_digest,
                    source_type=row.source_type,
                    source_url=row.source_url,
                    source_filename=row.source_filename,
                    submitted_by=row.submitted_by,
                    operation_count=row.operation_count,
                    host=server_hosts.get(row.id),
                    is_current=row.state in (ApiRevisionState.PUBLISHED, ApiRevisionState.IMPORTED),
                    promoted_at=row.promoted_at,
                    archived_at=row.archived_at,
                    created_at=row.created_at,
                )
            )

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, str(last.id))

        return RevisionPage(data=items, has_more=has_more, next_cursor=next_cursor)

    async def get_revision(
        self,
        *,
        vendor: str,
        name: str,
        version: str,
        revision_id: str,
    ) -> RevisionView:
        try:
            revision_uuid = uuid.UUID(revision_id)
        except ValueError:
            raise RevisionNotFoundError(revision_id, vendor, name, version) from None

        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)

            revision = await ApiRevisionRepository.get_for_api(session, api.id, revision_uuid)
            if revision is None:
                raise RevisionNotFoundError(revision_id, vendor, name, version)

            host: str | None = None
            if revision.servers:
                parsed = urlparse(revision.servers[0].url)
                host = parsed.hostname

        return RevisionView(
            id=revision.id,
            api_id=revision.api_id,
            vendor=vendor,
            name=name,
            version=version,
            state=revision.state,
            origin=revision.origin,
            spec_digest=revision.spec_digest,
            source_type=revision.source_type,
            source_url=revision.source_url,
            source_filename=revision.source_filename,
            submitted_by=revision.submitted_by,
            operation_count=revision.operation_count,
            host=host,
            is_current=revision.state in (ApiRevisionState.PUBLISHED, ApiRevisionState.IMPORTED),
            promoted_at=revision.promoted_at,
            archived_at=revision.archived_at,
            created_at=revision.created_at,
        )

    async def promote(
        self, vendor: str, name: str, version: str, revision_id: str, *, identity: Identity
    ) -> ApiView:
        try:
            revision_uuid = uuid.UUID(revision_id)
        except ValueError:
            raise RevisionNotFoundError(revision_id, vendor, name, version) from None

        async with self._ctx.registry_db.transaction() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)

            revision = await ApiRevisionRepository.get_for_api(session, api.id, revision_uuid)
            if revision is None:
                raise RevisionNotFoundError(revision_id, vendor, name, version)

            if revision.state not in (ApiRevisionState.DRAFT,):
                raise RevisionStateConflictError(
                    revision_id,
                    revision.state,
                    [ApiRevisionState.DRAFT],
                    "promote",
                )

            if not may_approve_host_change(identity.permissions):
                await self._require_no_host_change(
                    session, api.id, revision_uuid, vendor, name, version
                )

            # One vendor per host: the revision's URL-index rows exist from import
            # (every revision is indexed), but they only start serving once it is
            # live — refuse to go live on a host another vendor's live API serves.
            owners = await UrlIndexRepository.find_live_hosts_of_other_vendors(
                session, revision_id=revision_uuid, vendor=api.vendor
            )
            if owners:
                raise HostOwnedByOtherVendorError(revision_id, describe_live_host_owners(owners))

            # A draft that came from an origin-tracked source (a catalog revision held
            # for review) goes live as IMPORTED, like any catalog revision, so the next
            # catalog re-import archives it instead of colliding with it.
            promoted_state = (
                ApiRevisionState.IMPORTED
                if revision.origin is not None
                else ApiRevisionState.PUBLISHED
            )
            now = datetime.now(UTC)
            if api.current_revision_id is not None:
                await ApiRevisionRepository.set_state(
                    session, api.current_revision_id, ApiRevisionState.ARCHIVED, archived_at=now
                )
            await ApiRevisionRepository.archive_all_active_imported(session, api.id)

            await ApiRevisionRepository.set_state(
                session, revision_uuid, promoted_state, promoted_at=now
            )
            await ApiRepository.set_current_revision(session, api.id, revision_uuid)

            # The demote path above mutates revision rows via bulk UPDATEs and
            # repoints ``Api.current_revision_id``. Bulk updates do not sync the
            # ORM identity map, so the cached ``Api`` (and its ``current_revision``,
            # which is ``lazy="joined"``) is now stale. Expire everything so
            # ``_fetch_api_view`` re-reads fresh rows and its eager-load options
            # populate the relationships, instead of triggering an async lazy load
            # on a stale instance (which raises MissingGreenlet). See #642.
            await session.flush()
            session.expire_all()

            view = await ApiService._fetch_api_view(session, vendor, name, version)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.PROMOTE,
            target_type=AuditTargetType.REVISION,
            target_id=revision_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(api.id),
            after={"state": promoted_state},
            origin=identity.origin.value,
        )
        return view

    async def _require_no_host_change(
        self,
        session: Any,
        api_id: uuid.UUID,
        revision_id: uuid.UUID,
        vendor: str,
        name: str,
        version: str,
    ) -> None:
        """Refuse a promote that changes the server hosts of a credential-bound API.

        Only called for callers without ``credentials:write``. Same rules as the
        catalog re-import guard (``registry/ingest/host_change_guard.py``): the
        baseline is the current revision, else the most recently live one, so
        archiving the current revision first does not skip the check. When the
        API has never had a live revision there is no baseline and every origin
        of the draft is new: a draft that declares any server is refused if
        credentials are bound.
        """
        baseline = await ApiRevisionRepository.host_baseline_revision_id(
            session, api_id, exclude=revision_id
        )
        current = (
            hosts_from_servers(await ServerRepository.list_url_specs(session, baseline))
            if baseline is not None
            else frozenset()
        )
        new = hosts_from_servers(await ServerRepository.list_url_specs(session, revision_id))
        if not needs_review(current, new):
            return
        if not await api_has_bound_credentials(
            self._ctx, vendor=vendor, name=name, version=version
        ):
            return
        raise HostChangeRequiresOperatorError(
            str(revision_id), current_hosts=sorted(current), new_hosts=sorted(new)
        )

    async def archive(
        self, vendor: str, name: str, version: str, revision_id: str, *, identity: Identity
    ) -> RevisionView:
        try:
            revision_uuid = uuid.UUID(revision_id)
        except ValueError:
            raise RevisionNotFoundError(revision_id, vendor, name, version) from None

        async with self._ctx.registry_db.transaction() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)

            revision = await ApiRevisionRepository.get_for_api(session, api.id, revision_uuid)
            if revision is None:
                raise RevisionNotFoundError(revision_id, vendor, name, version)

            archivable = (ApiRevisionState.DRAFT, ApiRevisionState.IMPORTED)
            if revision.state not in archivable:
                raise RevisionStateConflictError(
                    revision_id, revision.state, list(archivable), "archive"
                )

            now = datetime.now(UTC)
            await ApiRevisionRepository.set_state(
                session, revision_uuid, ApiRevisionState.ARCHIVED, archived_at=now
            )
            if api.current_revision_id == revision_uuid:
                await ApiRepository.clear_current_revision(session, api.id)

            refreshed = await ApiRevisionRepository.get_for_api(session, api.id, revision_uuid)
            assert refreshed is not None

            host: str | None = None
            if refreshed.servers:
                parsed = urlparse(refreshed.servers[0].url)
                host = parsed.hostname

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.ARCHIVE,
            target_type=AuditTargetType.REVISION,
            target_id=revision_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(refreshed.api_id),
            after={"state": ApiRevisionState.ARCHIVED},
            origin=identity.origin.value,
        )
        return RevisionView(
            id=refreshed.id,
            api_id=refreshed.api_id,
            vendor=vendor,
            name=name,
            version=version,
            state=refreshed.state,
            origin=refreshed.origin,
            spec_digest=refreshed.spec_digest,
            source_type=refreshed.source_type,
            source_url=refreshed.source_url,
            source_filename=refreshed.source_filename,
            submitted_by=refreshed.submitted_by,
            operation_count=refreshed.operation_count,
            host=host,
            is_current=refreshed.state in (ApiRevisionState.PUBLISHED, ApiRevisionState.IMPORTED),
            promoted_at=refreshed.promoted_at,
            archived_at=refreshed.archived_at,
            created_at=refreshed.created_at,
        )

    async def delete(
        self, vendor: str, name: str, version: str, revision_id: str, *, identity: Identity
    ) -> None:
        try:
            revision_uuid = uuid.UUID(revision_id)
        except ValueError:
            raise RevisionNotFoundError(revision_id, vendor, name, version) from None

        async with self._ctx.registry_db.transaction() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)

            revision = await ApiRevisionRepository.get_for_api(session, api.id, revision_uuid)
            if revision is None:
                raise RevisionNotFoundError(revision_id, vendor, name, version)

            if revision.state != ApiRevisionState.ARCHIVED:
                raise RevisionStateConflictError(
                    revision_id, revision.state, [ApiRevisionState.ARCHIVED], "delete"
                )

            await ApiRevisionRepository.delete(session, revision_uuid)
            await ApiRepository.apply_counts(session, api.id, revision_count_delta=-1)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.REVISION,
            target_id=revision_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(api.id),
            origin=identity.origin.value,
        )
