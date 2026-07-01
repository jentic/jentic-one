"""Database engine and session management."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

import structlog
from sqlalchemy.engine import URL
from sqlalchemy.exc import DisconnectionError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.backends import (
    configure_sqlite_pragmas,
    enable_sqlite_manual_begin,
    get_backend,
)
from jentic_one.shared.db.backends.base import DatabaseBackend
from jentic_one.shared.db.errors import DatabaseIntegrityError, DatabaseUnavailableError

T = TypeVar("T")

logger = structlog.get_logger(__name__)


def get_database_url(config: DatabaseConfig) -> URL:
    """Build an async SQLAlchemy database URL from a DatabaseConfig."""
    return get_backend(config).make_url(config)


class DatabaseSession:
    """Manages a SQLAlchemy async engine and session factory for a single database."""

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._backend = get_backend(config)
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._is_sqlite = self._backend.dialect_name == "sqlite"

    @property
    def engine(self) -> AsyncEngine:
        """Access the underlying async engine. Raises if not connected."""
        if self._engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._engine

    @property
    def backend(self) -> DatabaseBackend:
        """The database backend selected for this session's configuration."""
        return self._backend

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Access the session factory. Raises if not connected."""
        if self._session_factory is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._session_factory

    async def connect(self) -> None:
        """Create the async engine and session factory."""
        url = self._backend.make_url(self._config)
        self._engine = create_async_engine(url, **self._backend.engine_kwargs(self._config))
        if self._backend.dialect_name == "sqlite":
            configure_sqlite_pragmas(
                self._engine,
                journal_mode=self._config.journal_mode,
                busy_timeout_ms=self._config.busy_timeout_ms,
            )
            enable_sqlite_manual_begin(self._engine)
        self._session_factory = async_sessionmaker(bind=self._engine, expire_on_commit=False)

    async def close(self) -> None:
        """Dispose the engine and release all connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield an async session, ensuring cleanup on exit."""
        factory = self.session_factory
        async with factory() as sess:
            try:
                yield sess
            finally:
                await sess.close()

    @asynccontextmanager
    async def transaction(self, *, retries: int = 1) -> AsyncGenerator[AsyncSession, None]:
        """Yield a session inside a transaction that commits on clean exit.

        On IntegrityError (in the body or at commit): rolls back and raises
        DatabaseIntegrityError.
        On OperationalError/DisconnectionError: raises DatabaseUnavailableError.
        On any other exception: rolls back and re-raises unchanged.

        The *retries* parameter controls how many times a transient error during
        commit is retried before giving up (default 1 retry).

        On SQLite this opens the transaction with ``BEGIN IMMEDIATE`` so the
        write lock is taken up front (see ``enable_sqlite_manual_begin``): it
        avoids the read→write upgrade that raises ``SQLITE_BUSY_SNAPSHOT``, and
        it is scoped to this write path only — reads via :meth:`session` stay in
        autocommit so WAL's reader/writer concurrency is preserved.
        """
        factory = self.session_factory
        async with factory() as sess:
            try:
                if self._is_sqlite:
                    # isolation_level=None disabled the DBAPI's implicit BEGIN,
                    # so start the write transaction explicitly and eagerly
                    # acquire the write lock. busy_timeout governs the wait; a
                    # timeout surfaces here as OperationalError and is mapped to
                    # DatabaseUnavailableError (and retried via run_in_transaction).
                    conn = await sess.connection()
                    await conn.exec_driver_sql("BEGIN IMMEDIATE")
                yield sess
            except IntegrityError as exc:
                await sess.rollback()
                raise DatabaseIntegrityError(str(exc)) from exc
            except (OperationalError, DisconnectionError) as exc:
                await sess.rollback()
                raise DatabaseUnavailableError(str(exc)) from exc
            except BaseException:
                await sess.rollback()
                raise
            else:
                commit_attempts = 0
                while True:
                    try:
                        await sess.commit()
                        return
                    except IntegrityError as exc:
                        # A constraint can surface at commit rather than inside the
                        # body (deferred constraints, or backends/flush strategies
                        # that defer the write). Map it to the same clean error as
                        # an in-body IntegrityError so callers see one contract
                        # regardless of when the violation lands.
                        await sess.rollback()
                        raise DatabaseIntegrityError(str(exc)) from exc
                    except (OperationalError, DisconnectionError) as exc:
                        await sess.rollback()
                        commit_attempts += 1
                        if commit_attempts > retries:
                            raise DatabaseUnavailableError(str(exc)) from exc
                        logger.warning(
                            "transient_commit_error_retrying",
                            attempt=commit_attempts,
                            max_retries=retries,
                        )

    async def run_in_transaction(
        self,
        fn: Callable[[AsyncSession], Awaitable[T]],
        *,
        retries: int = 2,
        backoff_s: float = 0.05,
    ) -> T:
        """Run ``fn(session)`` inside a transaction, retrying transient failures.

        Unlike :meth:`transaction` — a single-``yield`` context manager that can
        only retry at commit — this re-invokes ``fn`` on a *fresh* session when a
        transient failure (``OperationalError``/``DisconnectionError``, e.g. a
        SQLite ``database is locked``) is raised either inside ``fn`` or at
        commit. It waits ``backoff_s`` between attempts and gives up after
        ``retries`` retries (so up to ``retries + 1`` total attempts), raising
        :class:`DatabaseUnavailableError`.

        ``IntegrityError`` (→ :class:`DatabaseIntegrityError`) and every other
        exception propagate immediately without retry, matching the
        :meth:`transaction` contract. ``fn`` must be idempotent across attempts:
        it may run more than once.
        """
        attempt = 0
        while True:
            try:
                async with self.transaction() as sess:
                    result = await fn(sess)
                return result
            except DatabaseUnavailableError:
                if attempt >= retries:
                    raise
                attempt += 1
                logger.warning(
                    "transient_transaction_retrying",
                    attempt=attempt,
                    max_retries=retries,
                )
                if backoff_s > 0:
                    await asyncio.sleep(backoff_s * attempt)
