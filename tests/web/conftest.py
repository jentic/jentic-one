"""Shared web test fixtures using real services backed by real databases.

Loop-safety note
----------------
The web tests drive a synchronous Starlette ``TestClient``, which runs the ASGI
app (and therefore every request handler's DB call) on its own blocking-portal
event loop in a background thread. The ``web_context`` fixture, the data-seeding
fixtures, and async test bodies, by contrast, run on the pytest-asyncio loop.

asyncpg connections are bound to the event loop that opened them. With a normal
connection pool, a connection opened on the portal loop during a request gets
returned to the pool and may later be touched (reused or disposed) from the
pytest-asyncio loop, raising ``Task ... attached to a different loop`` /
``Event loop is closed`` at teardown.

The fix is to give the test engines a :class:`~sqlalchemy.pool.NullPool` so no
connection is ever retained between operations: each session opens a fresh
connection on whatever loop is currently running and closes it immediately,
and ``engine.dispose()`` has nothing pooled to close on a foreign loop. This is
applied to the whole ``tests/web`` package via the autouse, package-scoped
``_force_nullpool`` fixture below, so any future web test is loop-safe by
default without per-test plumbing. The patch is reverted when the package
finishes, so tests outside ``tests/web`` that run later in the same session
(e.g. integration tests that assert on ``pool.checkedout()``) get their normal
pooled engines.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

import jentic_one.shared.db.session as _session_module
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserRepository
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from tests.harness.postgres import migrate_to_head, postgres_config, prepare_xdist_database


@asynccontextmanager
async def noop_lifespan(_app: object) -> AsyncGenerator[None]:
    """No-op lifespan for test apps — the web_context fixture manages DB lifecycle."""
    yield


@asynccontextmanager
async def seeded_jwt_user(ctx: Context, user_id: str) -> AsyncGenerator[str]:
    """Seed an active ``users`` row for a test-minted login JWT's ``sub``; delete it after.

    ``verify_token`` re-checks the user row on every request (#1139), so a
    hand-minted user JWT only authenticates when its subject exists and is
    active. The row is seeded with the exact id the test's claims carry; a row
    a crashed earlier run left behind under that id is cleared first.
    """
    async with ctx.admin_db.session() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await UserRepository.create(
            session,
            id=user_id,
            email=f"{user_id}@jwt-subject.test",
            first_name="Jwt",
            last_name="Subject",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await session.commit()
    try:
        yield user_id
    finally:
        async with ctx.admin_db.session() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()


@pytest.fixture(scope="session")
def web_config() -> AppConfig:
    """AppConfig pointing at the local Docker Postgres fixture (port from ``JENTIC_PG_PORT``)."""
    return postgres_config()


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(
    web_config: AppConfig, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Bring every database up to head once per session (per-worker DB under xdist)."""
    prepare_xdist_database(tmp_path_factory.getbasetemp().parent)
    migrate_to_head(web_config)
    yield


@pytest.fixture(scope="package", autouse=True)
def _force_nullpool() -> Iterator[None]:
    """Make every test ``DatabaseSession`` engine use ``NullPool``.

    Patches the ``create_async_engine`` symbol used by
    ``jentic_one.shared.db.session`` so all engines built during web tests get
    ``poolclass=NullPool``. This is what keeps the sync ``TestClient`` portal
    loop and the pytest-asyncio loop from ever sharing a pooled asyncpg
    connection (see module docstring). Package-scoped: pytest binds it to the
    package of this conftest, ``tests/web``, sub-packages included, so it is set
    up once per contiguous run of ``tests/web`` tests. It is reverted when that
    run ends, not at session end, so it never leaks into tests that run after
    it. If the command line splits ``tests/web`` around other tests, it is
    reverted before them and applied again afterwards.
    """
    original = create_async_engine

    def _nullpool_engine(url: str | URL, **kwargs: object) -> AsyncEngine:
        # NullPool opens/closes a fresh connection per checkout, so pool sizing
        # is meaningless and ``pool_size`` would raise with NullPool.
        kwargs.pop("pool_size", None)
        kwargs["poolclass"] = NullPool
        return original(url, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_session_module, "create_async_engine", _nullpool_engine)
        yield


@pytest.fixture()
async def web_context(web_config: AppConfig) -> AsyncGenerator[Context, None]:
    """Connected Context for web tests."""
    async with Context(web_config) as ctx:
        yield ctx
