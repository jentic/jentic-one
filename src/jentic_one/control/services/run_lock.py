"""Cross-process run lock for control-side batch jobs.

Wraps :class:`UpgradeStepRepository`'s advisory lock in a context manager that
owns the dedicated lock session, so a job body never has to thread (or
accidentally commit) it. See ``control/repos/upgrade_step_repo.py`` for why the
lock is session-level and why it is a no-op on SQLite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from jentic_one.control.repos.upgrade_step_repo import UpgradeStepRepository
from jentic_one.shared.context import Context


@asynccontextmanager
async def hold_run_lock(ctx: Context, key: int) -> AsyncIterator[None]:
    """Hold run lock ``key`` on the control DB for the duration of the block.

    Blocks until any other holder (another replica's boot task, a concurrent
    CLI run, the migration runner) finishes, so the job bodies it guards never
    interleave.
    """
    async with ctx.control_db.session() as lock_session:
        await UpgradeStepRepository.acquire_run_lock(lock_session, key)
        try:
            yield
        finally:
            await UpgradeStepRepository.release_run_lock(lock_session, key)
