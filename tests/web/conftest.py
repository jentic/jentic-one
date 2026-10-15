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
applied process-wide for the whole ``tests/web`` package via the autouse
``_force_nullpool`` fixture below, so any future web test is loop-safe by
default without per-test plumbing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager

import pytest
from alembic import command
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

import jentic_one.shared.db.session as _session_module
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from tests.harness.postgres import DB_NAMES, alembic_config_for, ensure_schema, postgres_config


@asynccontextmanager
async def noop_lifespan(_app: object) -> AsyncGenerator[None]:
    """No-op lifespan for test apps — the web_context fixture manages DB lifecycle."""
    yield


@pytest.fixture(scope="session")
def web_config() -> AppConfig:
    """AppConfig pointing at the local Docker Postgres fixture (port from ``JENTIC_PG_PORT``)."""
    return postgres_config()


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(web_config: AppConfig) -> Iterator[None]:
    """Bring every database up to head once per session."""

    async def _ensure_all_schemas() -> None:
        for name in DB_NAMES:
            await ensure_schema(getattr(web_config.databases, name))

    asyncio.run(_ensure_all_schemas())

    for name in DB_NAMES:
        cfg = alembic_config_for(name, getattr(web_config.databases, name))
        command.upgrade(cfg, "head")
    yield


@pytest.fixture(scope="session", autouse=True)
def _force_nullpool() -> Iterator[None]:
    """Make every test ``DatabaseSession`` engine use ``NullPool``.

    Patches the ``create_async_engine`` symbol used by
    ``jentic_one.shared.db.session`` so all engines built during web tests get
    ``poolclass=NullPool``. This is what keeps the sync ``TestClient`` portal
    loop and the pytest-asyncio loop from ever sharing a pooled asyncpg
    connection (see module docstring). Applies to the whole ``tests/web``
    package and is reverted at session end.
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
