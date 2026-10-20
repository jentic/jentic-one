"""The memory backend passes the shipped ``BaseStateBackendContract``.

This repo forbids ``class Test*`` in test files, so the contract subclass is
underscore-prefixed and each inherited ``test_*`` method is driven from one
parametrized function (the shape ``tests/unit/testing/test_compliance_oss.py``
uses). A downstream suite subclasses the base as ``Test*`` instead.
"""

from __future__ import annotations

import inspect

import pytest

from jentic_one.shared.state import MemoryStateBackend, SharedStateBackend
from jentic_one.testing import BaseStateBackendContract


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _MemoryContract(BaseStateBackendContract):
    """Runs the contract on an injected clock, so expiry tests never sleep."""

    def __init__(self) -> None:
        self.clock = _Clock()

    def backend_factory(self) -> SharedStateBackend:
        return MemoryStateBackend(clock=self.clock)

    async def advance(self, seconds: float) -> None:
        self.clock.now += seconds


CONTRACT_TESTS = sorted(
    name
    for name, _ in inspect.getmembers(BaseStateBackendContract, inspect.isfunction)
    if name.startswith("test_")
)


@pytest.mark.parametrize("name", CONTRACT_TESTS)
async def test_memory_backend_meets_the_contract(name: str) -> None:
    result = getattr(_MemoryContract(), name)()
    if inspect.isawaitable(result):
        await result


def test_the_contract_covers_every_protocol_method() -> None:
    """Every ``SharedStateBackend`` method has a behavioural test in the contract."""
    methods = {
        name
        for name, _ in inspect.getmembers(SharedStateBackend, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == {"get", "set", "set_if_absent", "incr_with_ttl", "token_bucket", "aclose"}
    for method in methods:
        assert any(method in test for test in CONTRACT_TESTS), f"no contract test for {method}"


class _EvictingBackend(MemoryStateBackend):
    """A broken backend: ``set_if_absent`` always claims, so a replay is re-run."""

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        await self.set(key, value, ttl_s=ttl_s)
        return True


class _BrokenContract(BaseStateBackendContract):
    def backend_factory(self) -> SharedStateBackend:
        return _EvictingBackend()


async def test_the_contract_fails_a_backend_that_double_claims() -> None:
    with pytest.raises(AssertionError):
        await _BrokenContract().test_set_if_absent_first_claim_wins()
