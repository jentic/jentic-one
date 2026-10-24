"""Entitlement checker: cached verdicts + the grace window.

Same in-process mechanics as ``shared/release_check.py``'s ``ReleaseChecker``
(a size-1 :class:`~jentic_one.shared.cache.AsyncTTLCache`), plus the policy
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

import time

import structlog

from jentic_one.integrations.aws_marketplace.client import LicenseClient, LicenseVerdict
from jentic_one.shared.cache import AsyncTTLCache
from jentic_one.shared.config import AppConfig

_log = structlog.get_logger(__name__)


class EntitlementChecker:
    """Resolves the effective entitlement verdict, cached in-process.

    One instance is held per process (wired by the entitlement lifespan) so
    the cache, the grace clock, and the in-flight check are shared across
    the startup check and the periodic refresher. The ``client`` is injected
    so tests never touch the network.
    """

    def __init__(self, config: AppConfig, client: LicenseClient) -> None:
        self._config = config.entitlement
        self._client = client
        # The refresh interval is read from live config on every store, so a
        # reloaded config takes effect at the next check.
        self._cache: AsyncTTLCache[None, LicenseVerdict] = AsyncTTLCache(
            ttl_for=lambda _verdict: self._config.refresh_interval_seconds, max_entries=1
        )
        # The raw verdict of the latest check, for change logging.
        self._last_raw: LicenseVerdict | None = None
        # Monotonic timestamp of the last *definitive* verdict; seeded at
        # construction so a deployment that never reaches AWS still gets the
        # full grace window from boot before failing closed.
        self._last_definitive_at = time.monotonic()
        self._last_definitive = LicenseVerdict.ENTITLED

    async def current_verdict(self) -> LicenseVerdict:
        """Return the effective verdict (only ever ENTITLED/NOT_ENTITLED).

        Refreshes the raw verdict when the cache is older than
        ``refresh_interval_seconds`` (single-flight), then applies the grace
        window to any ``UNKNOWN``.
        """
        raw = await self._raw_verdict()
        return self._effective(raw)

    async def _raw_verdict(self) -> LicenseVerdict:
        return await self._cache.get_or_load(None, self._check)

    async def _check(self) -> LicenseVerdict:
        verdict = await self._client.check()
        previous = self._last_raw
        self._last_raw = verdict
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
