"""Integration tests for the worker's expired-token retention sweep (#610 audit).

Expired ``access_tokens`` / ``refresh_tokens`` rows previously accumulated
forever — the repos' ``delete_expired`` had no production caller. The worker's
retention sweep now purges rows expired for more than the grace window, while
keeping recently-expired rows (introspection / refresh-reuse forensics) and
live rows untouched. Per the repo's no-DB-mocking rule these run against the
real admin DB.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.worker import _TOKEN_RETENTION_DAYS, WorkerLoop

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_token_rows(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Ensure the token tables are empty before and after each test."""
    async with admin_db.transaction() as session:
        await session.execute(delete(AccessToken))
        await session.execute(delete(RefreshToken))
    yield
    async with admin_db.transaction() as session:
        await session.execute(delete(AccessToken))
        await session.execute(delete(RefreshToken))


def _access_token(token_hash: str, expires_at: datetime) -> AccessToken:
    return AccessToken(
        token_hash=token_hash,
        actor_id="agt_sweeptest",
        actor_type="agent",
        scopes=["apis:read"],
        token_family_id="fam_sweeptest",
        expires_at=expires_at,
        created_by="usr_test",
    )


def _refresh_token(token_hash: str, expires_at: datetime) -> RefreshToken:
    return RefreshToken(
        token_hash=token_hash,
        actor_id="agt_sweeptest",
        actor_type="agent",
        scopes=["apis:read"],
        token_family_id="fam_sweeptest",
        expires_at=expires_at,
        created_by="usr_test",
    )


async def test_sweep_purges_only_long_expired_token_rows(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    """Rows expired past the grace window are deleted; recent/live rows survive."""
    now = datetime.now(UTC)
    long_expired = now - timedelta(days=_TOKEN_RETENTION_DAYS + 1)
    recently_expired = now - timedelta(days=1)
    live = now + timedelta(hours=1)

    async with admin_db.transaction() as session:
        session.add_all(
            [
                _access_token("at-long-expired".ljust(64, "0"), long_expired),
                _access_token("at-recent-expired".ljust(64, "0"), recently_expired),
                _access_token("at-live".ljust(64, "0"), live),
                _refresh_token("rt-long-expired".ljust(64, "0"), long_expired),
                _refresh_token("rt-recent-expired".ljust(64, "0"), recently_expired),
                _refresh_token("rt-live".ljust(64, "0"), live),
            ]
        )

    worker = WorkerLoop(admin_db, JobHandlerRegistry())
    await worker._sweep_expired()

    async with admin_db.session() as session:
        access_hashes = set((await session.execute(select(AccessToken.token_hash))).scalars())
        refresh_hashes = set((await session.execute(select(RefreshToken.token_hash))).scalars())

    assert access_hashes == {
        "at-recent-expired".ljust(64, "0"),
        "at-live".ljust(64, "0"),
    }
    assert refresh_hashes == {
        "rt-recent-expired".ljust(64, "0"),
        "rt-live".ljust(64, "0"),
    }


async def test_sweep_with_no_token_rows_is_a_noop(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    """The sweep tolerates empty tables (fresh install) without raising."""
    worker = WorkerLoop(admin_db, JobHandlerRegistry())
    await worker._sweep_expired()

    async with admin_db.session() as session:
        assert (await session.execute(select(AccessToken))).scalars().first() is None
        assert (await session.execute(select(RefreshToken))).scalars().first() is None
