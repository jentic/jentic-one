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
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

import jentic_one.shared.db.session as _session_module
from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import get_database_url


@asynccontextmanager
async def noop_lifespan(_app: object) -> AsyncGenerator[None]:
    """No-op lifespan for test apps — the web_context fixture manages DB lifecycle."""
    yield


_DB_NAMES: tuple[str, ...] = ("registry", "admin", "control")

# A deterministic 32-byte AES key (base64). Web tests that create credentials
# need a non-empty encryption keyset or the Context boot fails with
# "EncryptionConfig.entries must not be empty".
_TEST_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.fixture(scope="session")
def web_config() -> AppConfig:
    """AppConfig pointing at local Docker PostgreSQL instance."""
    return AppConfig.model_validate(
        {
            "databases": {
                "registry": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "jentic",
                    "user": "registry_user",
                    "password": "registry_pass",
                    "pool_max": 2,
                    "schema_name": "registry",
                },
                "admin": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "jentic",
                    "user": "admin_user",
                    "password": "admin_pass",
                    "pool_max": 2,
                    "schema_name": "admin",
                },
                "control": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "jentic",
                    "user": "control_user",
                    "password": "control_pass",
                    "pool_max": 2,
                    "schema_name": "control",
                },
            },
            "credentials": {
                "encryption": {
                    "active_id": "v1",
                    "entries": [{"id": "v1", "material": _TEST_ENCRYPTION_KEY}],
                },
            },
        }
    )


def _alembic_config_for(db_name: str, db_config: DatabaseConfig) -> AlembicConfig:
    cfg = AlembicConfig("alembic.ini", ini_section=db_name)
    cfg.set_section_option(
        db_name, "sqlalchemy.url", get_database_url(db_config).render_as_string(hide_password=False)
    )
    cfg.set_section_option(db_name, "schema_name", db_config.schema_name)
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(web_config: AppConfig) -> Iterator[None]:
    """Bring every database up to head once per session."""

    async def _ensure_all_schemas() -> None:
        for name in _DB_NAMES:
            db_cfg = getattr(web_config.databases, name)
            superuser_cfg = db_cfg.model_copy(
                update={"user": "postgres", "password": SecretStr("postgres")}
            )
            url = get_database_url(superuser_cfg)
            engine = create_async_engine(url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{db_cfg.schema_name}"'))
            finally:
                await engine.dispose()

    asyncio.run(_ensure_all_schemas())

    for name in _DB_NAMES:
        cfg = _alembic_config_for(name, getattr(web_config.databases, name))
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
