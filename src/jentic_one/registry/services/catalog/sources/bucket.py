"""The database-backed rate bucket of one catalog source.

Every take is one short transaction on the source's ``catalog_source_buckets``
row: lock it, refill for the elapsed time, take a token if the lane's floor
allows, write it back. Concurrent takers in other processes queue on the row
lock, so the source's ``requests_per_second`` holds across every process that
shares the database.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from jentic_one.registry.repos.catalog_source_bucket_repo import (
    BucketState,
    CatalogSourceBucketRepository,
)
from jentic_one.registry.services.catalog.sources.limits import (
    FetchLane,
    TakeResult,
    refill_and_take,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.utils import utcnow


class DbRateBucket:
    """``RateBucket`` over one ``catalog_source_buckets`` row."""

    def __init__(
        self,
        ctx: Context,
        slug: str,
        *,
        rate: float,
        burst: int,
        interactive_share: float,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._ctx = ctx
        self._slug = slug
        self._rate = rate
        self._burst = burst
        self._reserve = burst * interactive_share
        self._clock = clock

    async def ensure(self) -> None:
        """Create the row (with a full bucket) if it does not exist."""
        async with self._ctx.registry_db.transaction() as session:
            await CatalogSourceBucketRepository.ensure(
                session, self._slug, burst=self._burst, now=self._clock()
            )

    async def _locked(self, session: Any) -> BucketState:
        state = await CatalogSourceBucketRepository.lock(session, self._slug)
        if state is not None:
            return state
        now = self._clock()
        await CatalogSourceBucketRepository.ensure(session, self._slug, burst=self._burst, now=now)
        state = await CatalogSourceBucketRepository.lock(session, self._slug)
        return state or BucketState(tokens=float(self._burst), refilled_at=now, blocked_until=None)

    async def take(self, lane: FetchLane) -> TakeResult:
        floor = 0.0 if lane == "interactive" else self._reserve
        async with self._ctx.registry_db.transaction() as session:
            state = await self._locked(session)
            now = self._clock()
            blocked_for = (
                (state.blocked_until - now).total_seconds() if state.blocked_until else 0.0
            )
            tokens, result = refill_and_take(
                tokens=state.tokens,
                elapsed_s=(now - state.refilled_at).total_seconds(),
                rate=self._rate,
                burst=self._burst,
                floor=floor,
                blocked_for_s=blocked_for,
            )
            await CatalogSourceBucketRepository.write(
                session,
                self._slug,
                tokens=tokens,
                refilled_at=max(now, state.refilled_at),
                blocked_until=state.blocked_until if blocked_for > 0 else None,
            )
        return result

    async def block(self, seconds: float) -> None:
        async with self._ctx.registry_db.transaction() as session:
            state = await self._locked(session)
            until = self._clock() + timedelta(seconds=seconds)
            if state.blocked_until is not None and state.blocked_until >= until:
                return
            await CatalogSourceBucketRepository.write(
                session,
                self._slug,
                tokens=state.tokens,
                refilled_at=state.refilled_at,
                blocked_until=until,
            )
