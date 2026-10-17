"""Shared fixtures for catalog source sync integration tests (real registry DB)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy import delete, select

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.core.schema.catalog_staged_entries import CatalogStagedEntry
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun
from jentic_one.registry.services.catalog.sources.protocol import (
    EntryChange,
    ListSummary,
    SourceEntry,
    Upserted,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

CtxFactory = Callable[..., AbstractAsyncContextManager[Context]]

#: Vendors of the ``apis`` rows these tests create (and wipe).
TEST_VENDORS = ("sync-test.io",)


async def wipe_catalog_sources(db: DatabaseSession) -> None:
    async with db.session() as session:
        for model in (CatalogStagedEntry, CatalogEntryRow, CatalogSyncRun, CatalogSourceRow):
            await session.execute(delete(model))
        await session.execute(delete(CatalogSnapshot))
        ids = select(Api.id).where(Api.vendor.in_(TEST_VENDORS))
        await session.execute(delete(ApiRevision).where(ApiRevision.api_id.in_(ids)))
        await session.execute(delete(Api).where(Api.vendor.in_(TEST_VENDORS)))
        await session.commit()


def entry(external_id: str, hint: str | None = None, **kw: Any) -> SourceEntry:
    return SourceEntry(
        external_id=external_id,
        catalog_api_id_hint=hint if hint is not None else external_id,
        title=kw.pop("title", None),
        version=kw.pop("version", None),
        spec_ref=kw.pop("spec_ref", {"url": f"https://specs.example/{external_id}.json"}),
        human_url=kw.pop("human_url", None),
        tags=tuple(kw.pop("tags", ())),
        content_hint=kw.pop("content_hint", None),
    )


class ScriptedStream:
    """A ``ListStream`` over fixed changes; can raise midway or pause on a gate."""

    def __init__(
        self,
        changes: Sequence[EntryChange],
        *,
        is_full: bool = True,
        raise_after: int | None = None,
        gate: Any = None,
    ) -> None:
        self.is_full = is_full
        self._changes = list(changes)
        self._raise_after = raise_after
        self._gate = gate
        self._done = False

    def __aiter__(self) -> AsyncIterator[EntryChange]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[EntryChange]:
        if self._gate is not None:
            await self._gate.wait()
        for index, change in enumerate(self._changes):
            if self._raise_after is not None and index == self._raise_after:
                raise RuntimeError("listing broke midway")
            yield change
        self._done = True

    async def summary(self) -> ListSummary:
        if not self._done:
            raise RuntimeError("not exhausted")
        return ListSummary(watermark=f"w{len(self._changes)}", is_full=self.is_full)


def full(*external_ids: str, **hints: str) -> ScriptedStream:
    return ScriptedStream([Upserted(entry(e, hints.get(e))) for e in external_ids])


def git_source(slug: str, **kw: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "kind": "git-manifest",
        "settings": {"url": f"https://{slug}.example/apis.json"},
        **kw,
    }
