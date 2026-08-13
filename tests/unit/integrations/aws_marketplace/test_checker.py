"""Unit tests for the entitlement checker (``checker.py``).

Covers the TTL cache + single-flight (the ``ReleaseChecker`` mechanics), and
the grace-window policy that turns raw three-way verdicts into effective
two-way ones. Time is advanced by aging the checker's monotonic bookkeeping
directly — patching ``time.monotonic`` would also skew the event loop's clock.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

import pytest

from jentic_one.integrations.aws_marketplace.checker import EntitlementChecker
from jentic_one.integrations.aws_marketplace.client import LicenseVerdict
from jentic_one.shared.config import AppConfig


class _StubClient:
    """LicenseClient returning a scripted sequence of verdicts."""

    def __init__(self, *verdicts: LicenseVerdict) -> None:
        self._verdicts = itertools.chain(verdicts, itertools.repeat(verdicts[-1]))
        self.calls = 0
        self._gate: asyncio.Event | None = None

    def hold(self) -> asyncio.Event:
        """Make ``check`` block until the returned event is set."""
        self._gate = asyncio.Event()
        return self._gate

    async def check(self) -> LicenseVerdict:
        self.calls += 1
        if self._gate is not None:
            await self._gate.wait()
        return next(self._verdicts)


def _checker(
    sample_config_dict: dict[str, Any],
    client: _StubClient,
    **entitlement: Any,
) -> EntitlementChecker:
    cfg = dict(sample_config_dict)
    cfg["entitlement"] = {"enabled": True, "product_code": "prod-abc", **entitlement}
    return EntitlementChecker(AppConfig.model_validate(cfg), client=client)


def _age_cache(checker: EntitlementChecker, seconds: float) -> None:
    """Age the raw-verdict cache so the next read refreshes."""
    entry = checker._entry
    assert entry is not None
    entry.checked_at -= seconds


def _age_grace(checker: EntitlementChecker, seconds: float) -> None:
    """Age the last-definitive clock so the grace window elapses."""
    checker._last_definitive_at -= seconds


@pytest.mark.asyncio
async def test_entitled_passthrough(sample_config_dict: dict[str, Any]) -> None:
    client = _StubClient(LicenseVerdict.ENTITLED)
    checker = _checker(sample_config_dict, client)

    assert await checker.current_verdict() is LicenseVerdict.ENTITLED
    assert client.calls == 1


@pytest.mark.asyncio
async def test_not_entitled_is_immediate(sample_config_dict: dict[str, Any]) -> None:
    """A definitive NOT_ENTITLED locks immediately — grace never applies."""
    client = _StubClient(LicenseVerdict.NOT_ENTITLED)
    checker = _checker(sample_config_dict, client, grace_period_seconds=86400)

    assert await checker.current_verdict() is LicenseVerdict.NOT_ENTITLED


@pytest.mark.asyncio
async def test_ttl_cache_hit(sample_config_dict: dict[str, Any]) -> None:
    client = _StubClient(LicenseVerdict.ENTITLED)
    checker = _checker(sample_config_dict, client, refresh_interval_seconds=3600)

    await checker.current_verdict()
    await checker.current_verdict()

    assert client.calls == 1


@pytest.mark.asyncio
async def test_expired_cache_refreshes(sample_config_dict: dict[str, Any]) -> None:
    client = _StubClient(LicenseVerdict.ENTITLED, LicenseVerdict.NOT_ENTITLED)
    checker = _checker(sample_config_dict, client, refresh_interval_seconds=3600)

    assert await checker.current_verdict() is LicenseVerdict.ENTITLED
    _age_cache(checker, 3601.0)
    assert await checker.current_verdict() is LicenseVerdict.NOT_ENTITLED
    assert client.calls == 2


@pytest.mark.asyncio
async def test_single_flight_coalesces_concurrent_checks(
    sample_config_dict: dict[str, Any],
) -> None:
    client = _StubClient(LicenseVerdict.ENTITLED)
    gate = client.hold()
    checker = _checker(sample_config_dict, client)

    first = asyncio.create_task(checker.current_verdict())
    second = asyncio.create_task(checker.current_verdict())
    await asyncio.sleep(0)  # both callers reach the lock
    gate.set()

    assert await first is LicenseVerdict.ENTITLED
    assert await second is LicenseVerdict.ENTITLED
    assert client.calls == 1


@pytest.mark.asyncio
async def test_unknown_inside_grace_holds_entitled(
    sample_config_dict: dict[str, Any],
) -> None:
    client = _StubClient(LicenseVerdict.UNKNOWN)
    checker = _checker(sample_config_dict, client, grace_period_seconds=86400)

    # No definitive answer yet, but the grace clock was seeded at construction.
    assert await checker.current_verdict() is LicenseVerdict.ENTITLED


@pytest.mark.asyncio
async def test_unknown_after_grace_fails_closed(
    sample_config_dict: dict[str, Any],
) -> None:
    client = _StubClient(LicenseVerdict.UNKNOWN)
    checker = _checker(sample_config_dict, client, grace_period_seconds=100)

    assert await checker.current_verdict() is LicenseVerdict.ENTITLED
    _age_grace(checker, 101.0)
    assert await checker.current_verdict() is LicenseVerdict.NOT_ENTITLED


@pytest.mark.asyncio
async def test_unknown_inside_grace_holds_prior_lockout(
    sample_config_dict: dict[str, Any],
) -> None:
    """Grace never un-locks: UNKNOWN after a definitive NOT_ENTITLED stays locked."""
    client = _StubClient(LicenseVerdict.NOT_ENTITLED, LicenseVerdict.UNKNOWN)
    checker = _checker(sample_config_dict, client, grace_period_seconds=86400)

    assert await checker.current_verdict() is LicenseVerdict.NOT_ENTITLED
    _age_cache(checker, checker._config.refresh_interval_seconds + 1.0)
    assert await checker.current_verdict() is LicenseVerdict.NOT_ENTITLED
    assert client.calls == 2  # the second read really did refresh (to UNKNOWN)


@pytest.mark.asyncio
async def test_recovery_after_lockout(sample_config_dict: dict[str, Any]) -> None:
    client = _StubClient(LicenseVerdict.NOT_ENTITLED, LicenseVerdict.ENTITLED)
    checker = _checker(sample_config_dict, client)

    assert await checker.current_verdict() is LicenseVerdict.NOT_ENTITLED
    _age_cache(checker, checker._config.refresh_interval_seconds + 1.0)
    assert await checker.current_verdict() is LicenseVerdict.ENTITLED
