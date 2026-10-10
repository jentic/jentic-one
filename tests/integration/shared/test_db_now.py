"""``db_now`` reads the database clock as an aware UTC datetime on both backends.

The ask tier's deadlines are set and compared on this clock (filing sets
``expires_at``; decide and the expiry sweep compare against it), so a broker,
admin app and worker on different hosts agree on when an approval lapsed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.shared.db import DatabaseSession, db_now

pytestmark = pytest.mark.integration


async def test_db_now_is_aware_utc_and_current(admin_db: DatabaseSession) -> None:
    async with admin_db.session() as session:
        now = await db_now(session)
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)
