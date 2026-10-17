"""Suite-wide fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jentic_one.shared.dns_cache import clear_resolution_cache

pytest_plugins = ("tests.harness.logging_isolation",)


@pytest.fixture(autouse=True)
def _isolated_resolution_cache() -> Iterator[None]:
    """Start and end every test with an empty process-wide DNS answer cache.

    Tests swap ``socket.getaddrinfo`` for canned answers; a cached answer from
    one test must never satisfy a lookup in the next.
    """
    clear_resolution_cache()
    yield
    clear_resolution_cache()
