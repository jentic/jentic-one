"""Repository for OperationURLIndex entities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, and_, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import URLIndexEntry
from jentic_one.shared.schemas import APIReference


@dataclass(frozen=True)
class LiveHostOwner:
    """A host already served by another vendor's live API revision."""

    host: str
    vendor: str
    name: str
    version: str

    def describe(self) -> str:
        """Human-readable ``host (vendor/name/version)`` fragment for error messages."""
        return f"'{self.host}' (served by '{self.vendor}/{self.name}/{self.version}')"


def describe_live_host_owners(owners: list[LiveHostOwner]) -> str:
    """One-line explanation of why a revision cannot go live on these hosts."""
    listed = ", ".join(owner.describe() for owner in owners)
    return (
        f"host already served by another vendor's live API: {listed}. A host is "
        "served by one vendor at a time; archive or delete that API's live "
        "revision first, or import this spec under the owning vendor"
    )


def _live_rows() -> Select[tuple[OperationURLIndex]]:
    """Select URL-index rows that belong to their API's live revision.

    "Live" is ``apis.current_revision_id`` — the revision promotion (or an
    auto-live import) made current. Drafts, archived and superseded revisions
    keep their rows (a ``Jentic-Revision`` pin still resolves against them) but
    never serve an unpinned lookup. Both joins are primary-key lookups.

    Rows are ordered most-recently-live first so a caller ranking equally
    specific matches from *different* live revisions can prefer the newest one
    deterministically (see ``URLLookupService._match_and_rank``).
    """
    return (
        select(OperationURLIndex)
        .join(ApiRevision, ApiRevision.id == OperationURLIndex.revision_id)
        .join(
            Api,
            and_(
                Api.id == ApiRevision.api_id,
                Api.current_revision_id == OperationURLIndex.revision_id,
            ),
        )
        .order_by(
            func.coalesce(ApiRevision.promoted_at, ApiRevision.created_at).desc(),
            OperationURLIndex.revision_id,
        )
    )


#: Namespace prefix for the host-ownership advisory lock keys, so they cannot
#: collide with other ``hashtext``-keyed advisory locks in the same database.
_HOST_LOCK_NAMESPACE = "registry.url_index.host:"


def _split_port(host: str) -> tuple[str, str | None]:
    """``(name, port)`` of a stored ``name[:port]`` host; IPv6 literals are returned whole."""
    name, sep, port = host.rpartition(":")
    if sep and port.isdigit() and ":" not in name:
        return name, port
    return host, None


def _canonical_host(host: str) -> str:
    """``host`` without a trailing dot on its name part (``a.com.:8443`` -> ``a.com:8443``)."""
    name, port = _split_port(host)
    if ":" in name:  # IPv6 literal: never carries a trailing dot
        return host
    name = name.rstrip(".")
    return f"{name}:{port}" if port else name


def _host_variants(canonical: str) -> tuple[str, ...]:
    """The stored spellings that denote ``canonical``: without and with a trailing dot."""
    name, port = _split_port(canonical)
    if ":" in name:
        return (canonical,)
    return (canonical, f"{name}.:{port}" if port else f"{name}.")


async def _concrete_hosts(session: AsyncSession, revision_id: uuid.UUID) -> list[str]:
    """Distinct concrete (non-templated, non-regex-only) hosts a revision indexes."""
    stmt = (
        select(OperationURLIndex.host)
        .distinct()
        .where(
            OperationURLIndex.revision_id == revision_id,
            OperationURLIndex.host.is_not(None),
            OperationURLIndex.host.not_like("%{%"),
        )
    )
    return [host for host in (await session.execute(stmt)).scalars().all() if host]


async def _lock_hosts(session: AsyncSession, canonical_hosts: list[str]) -> None:
    """Take a transaction-scoped advisory lock per host (Postgres only), in the given order."""
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for host in canonical_hosts:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": _HOST_LOCK_NAMESPACE + host},
        )


class UrlIndexRepository:
    """Data access layer for OperationURLIndex entities — flush-only, never commits."""

    @staticmethod
    async def delete_for_revision(session: AsyncSession, revision_id: uuid.UUID) -> None:
        await session.execute(
            delete(OperationURLIndex).where(OperationURLIndex.revision_id == revision_id)
        )
        await session.flush()

    @staticmethod
    async def get_api_reference_for_operation(
        session: AsyncSession, operation_id: str
    ) -> APIReference | None:
        """Return the ``APIReference`` for an operation, or ``None`` if unknown.

        ``name`` is always the API's canonical ``name``, never its editable
        ``display_name``: the broker matches credentials and revision pins on
        this identity, so it must not change when the display label does.
        Joins operations → api_revisions → apis.
        """
        stmt = (
            select(Api.vendor, Api.name, Api.version)
            .select_from(Operation)
            .join(ApiRevision, ApiRevision.id == Operation.revision_id)
            .join(Api, Api.id == ApiRevision.api_id)
            .where(Operation.id == operation_id)
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return None
        vendor, name, version = row
        return APIReference(vendor=vendor, name=name, version=version)

    @staticmethod
    async def upsert_entry(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        operation_id: str,
        method: str,
        entry: URLIndexEntry,
        created_by: str,
    ) -> None:
        stmt = insert(OperationURLIndex).values(
            operation_id=operation_id,
            revision_id=revision_id,
            method=method,
            host=entry.host_pattern,
            host_regex=entry.host_regex.pattern,
            path_template=entry.path_pattern,
            path_regex=entry.path_regex.pattern,
            param_names=entry.param_names,
            segment_count=entry.segment_count,
            created_by=created_by,
        )
        # The key is scoped to the revision, so a conflict can only come from two
        # operations of the *same* revision mapping to one URL — never from
        # another revision (or another API) claiming the URL.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_operation_url_index_lookup",
            set_={
                "operation_id": stmt.excluded.operation_id,
                "revision_id": stmt.excluded.revision_id,
                "host_regex": stmt.excluded.host_regex,
                "path_regex": stmt.excluded.path_regex,
                "param_names": stmt.excluded.param_names,
                "segment_count": stmt.excluded.segment_count,
            },
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def lookup_by_host(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        method: str,
        host: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find URL index entries matching exact host, method, and segment count."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.revision_id == revision_id,
                OperationURLIndex.method == method,
                OperationURLIndex.host == host,
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_live(
        session: AsyncSession,
        *,
        method: str,
        host: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find live-revision entries matching host, method, and segment count.

        Only rows of each API's current (live) revision are returned — see
        ``_live_rows``.
        """
        stmt = _live_rows().where(
            OperationURLIndex.method == method,
            OperationURLIndex.host == host,
            OperationURLIndex.segment_count == segment_count,
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_any_method(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        host: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find URL index entries matching host and segment count (any method)."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.revision_id == revision_id,
                OperationURLIndex.host == host,
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_regex(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        method: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find regex-host entries matching method and segment count."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.revision_id == revision_id,
                OperationURLIndex.method == method,
                OperationURLIndex.host.is_(None),
                OperationURLIndex.host_regex.isnot(None),
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_regex_live(
        session: AsyncSession,
        *,
        method: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find live-revision regex-host entries matching method and segment count."""
        stmt = _live_rows().where(
            OperationURLIndex.method == method,
            OperationURLIndex.host.is_(None),
            OperationURLIndex.host_regex.isnot(None),
            OperationURLIndex.segment_count == segment_count,
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def find_live_hosts_of_other_vendors(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        vendor: str,
    ) -> list[LiveHostOwner]:
        """Hosts indexed by ``revision_id`` that another vendor's live API already serves.

        Backs the host-ownership rule: a host is served by one vendor at a time,
        and the first vendor whose revision goes live on it keeps it until that
        API stops serving it (its live revision no longer indexes the host, or it
        is archived/deleted). Only live revisions own a host — a draft never
        does. Other APIs of the *same* vendor (``apis.vendor``) may share a host
        (e.g. several APIs under one gateway host). Only concrete hosts count: a
        host still carrying a ``{var}`` label (a server variable with no default)
        never matches a real request, so two self-hosted products both templated
        as ``{host}`` do not collide; regex-only rows (``host IS NULL``) are
        skipped for the same reason. Hosts are compared as stored (lower-cased,
        default port stripped by the index builder) and additionally modulo a
        trailing dot, so ``api.example.com.`` cannot sidestep an owned
        ``api.example.com``.

        **Must be called inside the transaction that makes the revision live**,
        before that write: on Postgres it first takes a transaction-scoped
        advisory lock per host (in sorted order, so concurrent callers cannot
        deadlock), which serializes two vendors going live on the same host at
        once — the second waits for the first to commit and then sees it as the
        owner. The locks are released at commit or rollback. SQLite has a single
        writer, so the lock is a no-op there.
        """
        hosts = await _concrete_hosts(session, revision_id)
        if not hosts:
            return []
        canonical = sorted({_canonical_host(host) for host in hosts})
        await _lock_hosts(session, canonical)

        candidates = sorted({variant for host in canonical for variant in _host_variants(host)})
        stmt = (
            select(OperationURLIndex.host, Api.vendor, Api.name, Api.version)
            .distinct()
            .join(Api, Api.current_revision_id == OperationURLIndex.revision_id)
            .where(
                OperationURLIndex.host.in_(candidates),
                OperationURLIndex.revision_id != revision_id,
                Api.vendor != vendor,
            )
            .order_by(OperationURLIndex.host, Api.vendor, Api.name, Api.version)
        )
        rows = (await session.execute(stmt)).all()
        return [
            LiveHostOwner(host=row.host, vendor=row.vendor, name=row.name, version=row.version)
            for row in rows
        ]

    @staticmethod
    async def lookup_by_host_regex_any_method(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find regex-host entries matching segment count (any method)."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.revision_id == revision_id,
                OperationURLIndex.host.is_(None),
                OperationURLIndex.host_regex.isnot(None),
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
