"""Cross-process run lock for control-side batch jobs.

The upgrade steps can run from several places at once — concurrent migration
runners, extra replicas' hooks — and their find-then-create writes are only
safe when runs never interleave. They serialise on a control-DB advisory lock
(:meth:`DatabaseSession.advisory_lock`: session-level, on a dedicated
autocommit connection, a no-op on SQLite).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from jentic_one.shared.context import Context

#: Advisory-lock keys (``pg_advisory_lock(bigint)``). Fixed constants rather
#: than ``hashtext(...)`` so an operator can find the holder in ``pg_locks``
#: (``classid``/``objid`` are the high/low 32 bits). ``0x6A6F_4B52_5452``
#: ("joKRTR") was the theme-5 key-retirement lock, retired with that job in
#: Phase 6b — do not reuse it.
UPGRADE_STEPS_LOCK_KEY = 0x6A6F_5550_4752  # "joUPGR"


@asynccontextmanager
async def hold_run_lock(ctx: Context, key: int) -> AsyncIterator[None]:
    """Hold run lock ``key`` on the control DB for the duration of the block.

    Blocks until any other holder (another replica's boot task, a concurrent
    CLI run, the migration runner) finishes, so the job bodies it guards never
    interleave.
    """
    async with ctx.control_db.advisory_lock(key):
        yield
