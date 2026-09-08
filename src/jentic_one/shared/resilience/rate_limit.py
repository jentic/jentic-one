"""Per-caller rate limiter — a policy layer over a ``RateLimitStore``.

The bucket math (refill, burst, retry-after) lives in the shared-state backend
(``token_bucket``); this class owns the *policy*: how rpm maps to a refill rate,
which key namespace callers share, and the RFC 6585 / draft ``RateLimit-*``
response headers an enforcer attaches to a ``429``.

Memory backend ⇒ per-instance limit; Redis backend ⇒ cluster-wide — the
limiter is identical either way (it only sees the ``RateLimitStore`` protocol).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from jentic_one.shared.state.backend import RateLimitStore


@dataclass(frozen=True, slots=True)
class RateLimitOutcome:
    """A limiter verdict plus the response headers an enforcer should attach."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int

    def headers(self) -> dict[str, str]:
        """Standard ``RateLimit-*`` headers (sent on both allow and deny).

        ``Retry-After`` is only meaningful on a deny; an enforcer adds it from
        :attr:`retry_after_s` when raising the ``429``.
        """
        return {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(max(0, self.remaining)),
        }


class RateLimiter:
    """Token-bucket rate limiter keyed per caller, over a ``RateLimitStore``."""

    def __init__(
        self, store: RateLimitStore, *, default_rpm: int, burst: int, namespace: str = "caller"
    ) -> None:
        self._store = store
        # The backend bucket refills in tokens/second; rpm is the operator-facing
        # knob. burst is the bucket capacity (max instantaneous spend).
        self._rate_per_s = default_rpm / 60.0
        self._burst = burst
        # Limiters sharing one store AND one namespace share buckets. The
        # default keeps the historical `rl:caller:` keyspace; a limiter whose
        # keys could collide with another limiter's (e.g. two bare-IP-keyed
        # limiters with different rate params) must pass its own namespace so
        # each route drains only its own quota.
        self._key_prefix = f"rl:{namespace}:"

    async def acquire(self, actor_id: str, *, cost: int = 1) -> RateLimitOutcome:
        """Spend ``cost`` tokens for ``actor_id``; deny (with retry-after) if dry."""
        decision = await self._store.token_bucket(
            f"{self._key_prefix}{actor_id}",
            rate=self._rate_per_s,
            burst=self._burst,
            cost=cost,
        )
        # Round up so a sub-second deficit still yields a ``Retry-After: 1`` (a
        # ``0`` would tell the client to retry immediately into the same deny).
        retry_after = math.ceil(decision.retry_after_s) if not decision.allowed else 0
        return RateLimitOutcome(
            allowed=decision.allowed,
            limit=decision.limit,
            remaining=decision.remaining,
            retry_after_s=retry_after,
        )


__all__ = ["RateLimitOutcome", "RateLimiter"]
