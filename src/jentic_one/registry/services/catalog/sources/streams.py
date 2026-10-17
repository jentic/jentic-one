"""Ready-made ``ListStream`` implementations for full listings."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from jentic_one.registry.services.catalog.sources.protocol import EntryChange, ListSummary


class FullListStream:
    """A complete listing produced by one awaitable that loads every change.

    ``is_full`` is ``True`` before iteration starts. The loader runs on first
    iteration; ``summary()`` raises ``RuntimeError`` until the iterator has been
    exhausted. ``watermark`` derives the summary's watermark from the changes.
    """

    is_full: bool = True

    def __init__(
        self,
        load: Callable[[], Awaitable[Sequence[EntryChange]]],
        *,
        watermark: Callable[[Sequence[EntryChange]], str],
    ) -> None:
        self._load = load
        self._watermark = watermark
        self._summary: ListSummary | None = None

    def __aiter__(self) -> AsyncIterator[EntryChange]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[EntryChange]:
        changes = await self._load()
        for change in changes:
            yield change
        self._summary = ListSummary(watermark=self._watermark(changes), is_full=self.is_full)

    async def summary(self) -> ListSummary:
        if self._summary is None:
            raise RuntimeError("ListStream.summary() called before the listing was exhausted")
        return self._summary
