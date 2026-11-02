"""Unit tests for the bounded recent-key window (#1960)."""

from __future__ import annotations

import pytest

from jentic_one.shared.cache import RecentKeys


class _Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


def test_accepts_once_per_window() -> None:
    clock = _Clock()
    window: RecentKeys[str] = RecentKeys(ttl_seconds=10, max_keys=8, clock=clock)

    assert window.try_accept("k") is True
    assert window.try_accept("k") is False
    clock.now += 10
    assert window.try_accept("k") is True


def test_rejected_attempts_do_not_extend_the_window() -> None:
    clock = _Clock()
    window: RecentKeys[str] = RecentKeys(ttl_seconds=10, max_keys=8, clock=clock)
    window.try_accept("k")

    clock.now += 9
    assert window.try_accept("k") is False
    clock.now += 1
    assert window.try_accept("k") is True


def test_bounded_by_max_keys() -> None:
    window: RecentKeys[int] = RecentKeys(ttl_seconds=60, max_keys=3)
    for n in range(10):
        assert window.try_accept(n) is True

    assert len(window) == 3
    # The oldest keys were evicted, so they are accepted again.
    assert window.try_accept(0) is True
    assert window.try_accept(9) is False


def test_release_frees_the_slot() -> None:
    window: RecentKeys[str] = RecentKeys(ttl_seconds=60, max_keys=8)
    window.try_accept("k")
    window.release("k")
    assert window.try_accept("k") is True
    window.clear()
    assert len(window) == 0


def test_rejects_non_positive_bound() -> None:
    with pytest.raises(ValueError, match="max_keys"):
        RecentKeys[str](ttl_seconds=1, max_keys=0)
