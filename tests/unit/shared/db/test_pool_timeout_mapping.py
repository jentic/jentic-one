"""Unit tests for SQLAlchemy connection-pool checkout timeout mapping (#1304).

Pins that a ``QueuePool`` checkout timeout (``sqlalchemy.exc.TimeoutError``) on
``DatabaseSession`` translates to a retryable ``DatabaseUnavailableError`` with
internal pool sizing redacted (CWE-209), while caller-side ``TimeoutError``
(``asyncio.timeout``) and non-checkout errors inside session bodies propagate
unchanged, and HTTP handlers answer ``503`` with ``Retry-After``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog.testing
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from jentic_one.admin.web.errors import database_error_handler as admin_handler
from jentic_one.auth.web.errors import database_error_handler as auth_handler
from jentic_one.broker.web.errors import handle_database_unavailable as broker_handler
from jentic_one.control.web.errors import database_error_handler as control_handler
from jentic_one.mcp.app import _database_unavailable as mcp_unavailable
from jentic_one.registry.web.errors import service_error_handler as registry_handler
from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.backends import configure_sqlite_pragmas, enable_sqlite_manual_begin
from jentic_one.shared.db.connectivity import is_connection_loss
from jentic_one.shared.db.errors import (
    DatabaseCommitOutcomeUnknownError,
    DatabaseUnavailableError,
)
from jentic_one.shared.db.session import DatabaseSession


@pytest.fixture()
async def constrained_sqlite_db(tmp_path: Path) -> AsyncGenerator[DatabaseSession, None]:
    """A real file-backed SQLite ``DatabaseSession`` with a 1-slot ``QueuePool``."""
    cfg = DatabaseConfig(backend="sqlite", path=str(tmp_path / "pool.db"))
    db = DatabaseSession(cfg)
    await db.connect()
    # Replace the default engine pool with a constrained QueuePool (size=1,
    # max_overflow=0, timeout=0.05s) while keeping the session factory wired
    # identically to DatabaseSession.connect().
    await db.engine.dispose()
    url = db.backend.make_url(cfg)
    engine = create_async_engine(
        url,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.05,
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_pragmas(
        engine, journal_mode=cfg.journal_mode, busy_timeout_ms=cfg.busy_timeout_ms
    )
    enable_sqlite_manual_begin(engine)
    db._engine = engine
    db.session_factory.configure(bind=engine)
    async with db.transaction() as sess:
        await sess.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY)"))
    try:
        yield db
    finally:
        await db.close()


async def _exercise_scope(db: DatabaseSession, scope: str) -> None:
    if scope == "session":
        async with db.session() as sess:
            await sess.execute(text("SELECT 1"))
    elif scope == "read_session":
        async with db.read_session() as sess:
            await sess.execute(text("SELECT 1"))
    elif scope == "read_snapshot":
        async with db.read_snapshot() as sess:
            await sess.execute(text("SELECT 1"))
    elif scope == "transaction":
        async with db.transaction() as sess:
            await sess.execute(text("INSERT INTO items (id) VALUES (1)"))
    else:
        raise AssertionError(f"unknown scope {scope!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["session", "read_session", "read_snapshot", "transaction"])
async def test_pool_checkout_timeout_maps_to_redacted_unavailable(
    constrained_sqlite_db: DatabaseSession, scope: str
) -> None:
    """Exhausting ``QueuePool`` translates ``sqlalchemy.exc.TimeoutError`` to
    ``DatabaseUnavailableError``."""
    db = constrained_sqlite_db
    async with db.session() as holder:
        await holder.execute(text("SELECT 1"))
        with pytest.raises(DatabaseUnavailableError) as caught:
            await _exercise_scope(db, scope)

    exc = caught.value
    assert not isinstance(exc, DatabaseCommitOutcomeUnknownError)
    assert isinstance(exc.__cause__, SATimeoutError)
    assert not is_connection_loss(exc)
    assert "QueuePool" not in str(exc)
    assert "overflow" not in str(exc)
    assert "sqlalche.me" not in str(exc)

    # Once the holder releases the connection, the pool serves the next caller cleanly.
    async with db.session() as recovered:
        assert (await recovered.execute(text("SELECT 1"))).scalar_one() == 1


@pytest.mark.asyncio
async def test_run_in_transaction_retries_pool_checkout_timeout_then_succeeds(
    constrained_sqlite_db: DatabaseSession,
) -> None:
    """``run_in_transaction`` retries a pool-checkout timeout and succeeds once a slot frees."""
    db = constrained_sqlite_db
    release_holder = asyncio.Event()
    holding = asyncio.Event()

    async def _hold_briefly() -> None:
        async with db.session() as holder:
            await holder.execute(text("SELECT 1"))
            holding.set()
            await release_holder.wait()

    async def _free_after_first_timeout() -> None:
        await holding.wait()
        await asyncio.sleep(0.07)
        release_holder.set()

    calls = 0

    async def _work(sess: AsyncSession) -> int:
        nonlocal calls
        calls += 1
        return int((await sess.execute(text("SELECT 1"))).scalar_one())

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_hold_briefly())
        tg.create_task(_free_after_first_timeout())
        await holding.wait()
        result = await db.run_in_transaction(_work, retries=2, backoff_s=0.03)

    assert result == 1
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["session", "read_session", "read_snapshot", "transaction"])
@pytest.mark.parametrize(
    "caller_exc_factory",
    [
        lambda: TimeoutError("caller deadline expired"),
        lambda: ValueError("ordinary caller error"),
        lambda: SATimeoutError("synthetic non-checkout sqlalchemy timeout"),
    ],
    ids=["builtin-timeout", "value-error", "caller-sa-timeout"],
)
async def test_caller_body_exceptions_propagate_unchanged(
    constrained_sqlite_db: DatabaseSession,
    scope: str,
    caller_exc_factory: Any,
) -> None:
    """Exceptions raised inside the caller body never map to ``DatabaseUnavailableError``."""
    db = constrained_sqlite_db
    target_exc = caller_exc_factory()

    with pytest.raises(type(target_exc)) as caught:
        if scope == "session":
            async with db.session() as sess:
                await sess.execute(text("SELECT 1"))
                raise target_exc
        elif scope == "read_session":
            async with db.read_session() as sess:
                await sess.execute(text("SELECT 1"))
                raise target_exc
        elif scope == "read_snapshot":
            async with db.read_snapshot() as sess:
                await sess.execute(text("SELECT 1"))
                raise target_exc
        else:
            async with db.transaction() as sess:
                await sess.execute(text("SELECT 1"))
                raise target_exc

    assert caught.value is target_exc


def _request(path: str = "/items") -> MagicMock:
    req = MagicMock(spec=Request)
    req.url.path = path
    return req


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [admin_handler, auth_handler, control_handler, registry_handler, broker_handler],
    ids=["admin", "auth", "control", "registry", "broker"],
)
async def test_web_handlers_answer_503_with_retry_after_and_no_pool_internals(
    constrained_sqlite_db: DatabaseSession,
    handler: Any,
) -> None:
    """HTTP error handlers render pool exhaustion as sanitized 503 + ``Retry-After``."""
    db = constrained_sqlite_db
    async with db.session() as holder:
        await holder.execute(text("SELECT 1"))
        try:
            async with db.session() as waiter:
                await waiter.execute(text("SELECT 1"))
        except DatabaseUnavailableError as caught:
            pool_exc = caught
        else:  # pragma: no cover
            pytest.fail("expected DatabaseUnavailableError on exhausted pool")

    with structlog.testing.capture_logs() as logs:
        response = await handler(_request("/items"), pool_exc)

    assert response.status_code == 503
    assert "Retry-After" in response.headers
    assert int(response.headers["Retry-After"]) >= 1
    raw_body = bytes(response.body).decode()
    body = json.loads(raw_body)
    assert body["type"] == "database_unavailable"
    assert "QueuePool" not in raw_body
    assert "overflow" not in raw_body
    rendered_logs = repr(logs)
    assert "QueuePool" not in rendered_logs
    assert all("exc_info" not in entry for entry in logs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [admin_handler, auth_handler, control_handler, registry_handler, broker_handler],
    ids=["admin", "auth", "control", "registry", "broker"],
)
async def test_commit_outcome_unknown_omits_retry_after_header(handler: Any) -> None:
    """A commit-outcome-unknown 503 never sets ``Retry-After``, preventing blind replay."""
    response = await handler(
        _request("/items"), DatabaseCommitOutcomeUnknownError("connection is closed")
    )
    assert response.status_code == 503
    assert "Retry-After" not in response.headers


def test_mcp_database_unavailable_includes_retry_after() -> None:
    """The MCP gate's 503 carries ``Retry-After`` on both POST and HEAD."""
    post_resp = mcp_unavailable("POST")
    head_resp = mcp_unavailable("HEAD")
    assert post_resp.status_code == 503
    assert head_resp.status_code == 503
    assert int(post_resp.headers["Retry-After"]) >= 1
    assert int(head_resp.headers["Retry-After"]) >= 1
