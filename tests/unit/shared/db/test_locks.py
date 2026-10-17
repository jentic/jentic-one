"""Unit tests for the advisory-lock seam: key derivation and the SQLite no-op path.

The Postgres behaviour (exactly one winner across sessions) is covered by
``tests/integration/shared/db/test_try_advisory_lock.py``.
"""

from __future__ import annotations

import pytest

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.locks import advisory_lock_key
from jentic_one.shared.db.session import DatabaseSession


def test_advisory_lock_key_is_stable_and_signed_64_bit() -> None:
    key = advisory_lock_key("jentic_one.registry.catalog_update_sweep")
    # Pinned: the key must not drift across releases, or two versions running side by
    # side during a rolling deploy would stop excluding each other.
    assert key == 327221202135576437
    assert -(2**63) <= advisory_lock_key("x") < 2**63


def test_advisory_lock_key_differs_per_name() -> None:
    assert advisory_lock_key("a.one") != advisory_lock_key("a.two")


def test_advisory_lock_key_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        advisory_lock_key("")


@pytest.mark.asyncio
async def test_try_advisory_lock_is_an_always_acquiring_no_op_on_sqlite() -> None:
    """SQLite is single-process: every attempt acquires, even nested ones."""
    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=":memory:"))
    await db.connect()
    try:
        key = advisory_lock_key("tests.sqlite_no_op")
        async with db.try_advisory_lock(key) as first, db.try_advisory_lock(key) as second:
            assert first is True
            assert second is True
    finally:
        await db.close()
