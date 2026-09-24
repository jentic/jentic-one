"""Entitlement checker: cached verdicts + the grace window.

Same in-process mechanics as ``shared/release_check.py``'s ``ReleaseChecker``
(monotonic-clock TTL cache, ``asyncio.Lock`` single-flight), plus the policy
that turns raw three-way :class:`LicenseVerdict` answers into an *effective*
two-way one:

- ``ENTITLED`` / ``NOT_ENTITLED`` are definitive — they apply immediately and
  (re)start the grace clock.
- ``UNKNOWN`` (AWS unreachable, throttled, credentials missing) holds the last
  definitive verdict for ``grace_period_seconds``; once the window is
  exhausted with no definitive answer, the checker fails closed
  (``NOT_ENTITLED``).

Never raises: an AWS failure is a logged warning and an ``UNKNOWN``, so the
license probe can never crash the app.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog

from jentic_one.integrations.aws_marketplace.client import LicenseClient, LicenseVerdict
from jentic_one.shared.config import AppConfig

_log = structlog.get_logger(__name__)


@dataclass(slots=True)
class _CacheEntry:
    """A raw verdict and when it was fetched (monotonic clock)."""

    verdict: LicenseVerdict
    checked_at: float


class EntitlementChecker:
    """Resolves the effective entitlement verdict, cached in-process.

    One instance is held per process (wired by the entitlement lifespan) so
    the cache, the grace clock, and the single-flight lock are shared across
    the startup check and the periodic refresher. The ``client`` is injected
    so tests never touch the network.
    """

    def __init__(self, config: AppConfig, client: LicenseClient) -> None:
        self._config = config.entitlement
        self._client = client
        self._entry: _CacheEntry | None = None
        # Monotonic timestamp of the last *definitive* verdict; seeded at
        # construction so a deployment that never reaches AWS still gets the
        # full grace window from boot before failing closed.
        self._last_definitive_at = time.monotonic()
        self._last_definitive = LicenseVerdict.ENTITLED
        self._lock = asyncio.Lock()

    async def current_verdict(self) -> LicenseVerdict:
        """Return the effective verdict (only ever ENTITLED/NOT_ENTITLED).

        Refreshes the raw verdict when the cache is older than
        ``refresh_interval_seconds`` (single-flight), then applies the grace
        window to any ``UNKNOWN``.
        """
        raw = await self._raw_verdict()
        return self._effective(raw)

    async def _raw_verdict(self) -> LicenseVerdict:
        ttl = self._config.refresh_interval_seconds
        now = time.monotonic()
        entry = self._entry
        if entry is not None and (now - entry.checked_at) < ttl:
            return entry.verdict

        async with self._lock:
            entry = self._entry
            now = time.monotonic()
            if entry is not None and (now - entry.checked_at) < ttl:
                return entry.verdict
            verdict = await self._client.check()
            previous = self._entry.verdict if self._entry is not None else None
            self._entry = _CacheEntry(verdict=verdict, checked_at=time.monotonic())
            if verdict is not LicenseVerdict.UNKNOWN:
                self._last_definitive = verdict
                self._last_definitive_at = time.monotonic()
            if previous is not verdict:
                _log.info(
                    "entitlement.verdict_changed",
                    previous=previous.value if previous else None,
                    verdict=verdict.value,
                )
            return verdict

    def _effective(self, raw: LicenseVerdict) -> LicenseVerdict:
        if raw is not LicenseVerdict.UNKNOWN:
            return raw
        in_grace = (time.monotonic() - self._last_definitive_at) < self._config.grace_period_seconds
        if in_grace:
            # Inside the window an outage holds the last definitive answer —
            # which for a previously not-entitled deployment stays locked out.
            return (
                LicenseVerdict.ENTITLED
                if self._last_definitive is LicenseVerdict.ENTITLED
                else LicenseVerdict.NOT_ENTITLED
            )
        return LicenseVerdict.NOT_ENTITLED
