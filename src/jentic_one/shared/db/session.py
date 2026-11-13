"""Database engine and session management."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import structlog
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import (
    DataError,
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InvalidRequestError,
    OperationalError,
)
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.backends import (
    configure_idle_gated_pre_ping,
    configure_sqlite_pragmas,
    enable_sqlite_manual_begin,
    get_backend,
)
from jentic_one.shared.db.backends.base import DatabaseBackend
from jentic_one.shared.db.connectivity import is_connection_loss, redact_sql
from jentic_one.shared.db.errors import (
    DatabaseCommitOutcomeUnknownError,
    DatabaseConsistencyError,
    DatabaseDataError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)
from jentic_one.shared.db.read_only import install_read_only_guard

T = TypeVar("T")

logger = structlog.get_logger(__name__)

# ``Session.info`` key set while a commit is in flight and cleared once it
# returns, so a failure raised by a manual ``session.commit()`` inside a
# ``transaction()`` body is told apart from one raised by an ordinary statement.
_COMMIT_IN_FLIGHT = "jentic_commit_in_flight"


class _CommitTrackingSession(Session):
    """ORM session that flags the window in which a commit is running."""

    def commit(self) -> None:
        self.info[_COMMIT_IN_FLIGHT] = True
        super().commit()
        self.info.pop(_COMMIT_IN_FLIGHT, None)


def _connection_lost_error(sess: AsyncSession, exc: BaseException) -> DatabaseUnavailableError:
    """Map a connection lost in a ``transaction()`` body to its domain error.

    A loss while a manual ``session.commit()`` was running leaves the outcome
    unknown, exactly like a loss at the context manager's own commit, so it
    raises :class:`DatabaseCommitOutcomeUnknownError` and is never replayed.
    """
    if sess.info.pop(_COMMIT_IN_FLIGHT, False):
        return _commit_outcome_unknown(exc)
    return _unavailable(exc)


def _is_data_error(exc: DBAPIError) -> bool:
    """True when a DBAPI error is a SQLSTATE class-22 data exception.

    A value too long for a column raises ``StringDataRightTruncationError``.
    SQLAlchemy's asyncpg dialect maps every ``PostgresError`` that is not an
    integrity violation to the base ``DBAPIError`` (not ``DataError``), so we
    fall back to the SQLSTATE: class ``22`` is *data exception* (truncation,
    numeric out of range, invalid text representation, …). ``psycopg`` exposes
    it via ``pgcode``; asyncpg via ``sqlstate``.
    """
    if isinstance(exc, DataError):
        return True
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return bool(sqlstate) and str(sqlstate).startswith("22")


def _unavailable(exc: BaseException) -> DatabaseUnavailableError:
    """Wrap a transient DB failure, dropping SQLAlchemy's ``[SQL: …]`` tail."""
    return DatabaseUnavailableError(redact_sql(str(exc)))


def _commit_outcome_unknown(exc: BaseException) -> DatabaseCommitOutcomeUnknownError:
    """Wrap a connection lost at commit, dropping SQLAlchemy's ``[SQL: …]`` tail."""
    return DatabaseCommitOutcomeUnknownError(redact_sql(str(exc)))


async def _rollback_after_connection_loss(sess: AsyncSession) -> None:
    """Best-effort rollback on a session whose connection is already gone.

    A rollback on a dead connection can itself raise; that secondary error
    must not replace the connection-loss error the caller maps and retries.
    """
    try:
        await sess.rollback()
    except Exception as exc:
        logger.debug("rollback_after_connection_loss_failed", error=redact_sql(str(exc)))


def get_database_url(config: DatabaseConfig) -> URL:
    """Build an async SQLAlchemy database URL from a DatabaseConfig."""
    return get_backend(config).make_url(config)


async def _release_advisory_lock(conn: AsyncConnection, key: int) -> None:
    """Best-effort ``pg_advisory_unlock`` on the lock's dedicated connection.

    A failing unlock (the connection was lost, or the releasing task was
    cancelled again mid-unlock) discards the connection instead of handing one
    that may still hold the lock back to the pool; closing it releases the
    lock server-side. A cancellation is re-raised after the discard.
    """
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    except BaseException as exc:
        logger.warning("advisory_lock_release_failed", key=key, error=repr(exc))
        await conn.invalidate()
        if not isinstance(exc, Exception):
            raise


async def _await_shielded[R](coro: Coroutine[Any, Any, R]) -> R:
    """Await ``coro`` to completion before propagating task cancellation."""
    task = asyncio.create_task(coro)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        if not task.cancelled():
            _ = task.exception()
        raise asyncio.CancelledError
    return task.result()


class _RefCountedKeyLock:
    """Per-key asyncio lock tracked by waiter count so idle keys are evicted."""

    __slots__ = ("lock", "waiters")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.waiters = 0


class _SharedAdvisoryLockMultiplexer:
    """Multiplexes non-blocking session-level Postgres advisory locks on one connection.

    A single PostgreSQL backend in ``AUTOCOMMIT`` mode can hold multiple
    distinct 64-bit advisory lock keys simultaneously while remaining ``idle``
    (never ``idle in transaction``). Hosting those locks on a dedicated
    single-connection engine keeps every slot of the main session ``QueuePool``
    available for short reads and writes, preventing pool exhaustion and nested
    checkout deadlocks under concurrent long-running operations.

    In-process callers for the same ``key`` are excluded via ``_held_keys`` so
    PostgreSQL's per-session advisory-lock re-entrancy never grants the same
    key twice within one ``DatabaseSession``.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._io_lock = asyncio.Lock()
        self._raw_conn: AsyncConnection | None = None
        self._autocommit_conn: AsyncConnection | None = None
        self._held_keys: set[int] = set()

    async def acquire(self, key: int) -> bool:
        task = asyncio.create_task(self._acquire_inner(key))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            exc = None if task.cancelled() else task.exception()
            if exc is None and task.result():
                await _await_shielded(self._release_inner(key))
            raise asyncio.CancelledError
        return task.result()

    async def release(self, key: int) -> None:
        await _await_shielded(self._release_inner(key))

    async def close(self) -> None:
        async with self._io_lock:
            await self._discard_connection()
        await self._engine.dispose()

    async def _ensure_connection(self) -> AsyncConnection:
        if self._autocommit_conn is not None:
            return self._autocommit_conn
        raw = self._engine.connect()
        await raw.start()
        try:
            autocommit = await raw.execution_options(isolation_level="AUTOCOMMIT")
        except BaseException:
            with contextlib.suppress(Exception):
                await raw.close()
            raise
        self._raw_conn = raw
        self._autocommit_conn = autocommit
        return autocommit

    async def _checkin_connection(self) -> None:
        raw = self._raw_conn
        self._raw_conn = None
        self._autocommit_conn = None
        if raw is not None:
            try:
                await raw.close()
            except Exception:
                with contextlib.suppress(Exception):
                    await raw.invalidate()

    async def _discard_connection(self) -> None:
        raw = self._raw_conn
        self._raw_conn = None
        self._autocommit_conn = None
        self._held_keys.clear()
        if raw is not None:
            with contextlib.suppress(Exception):
                await raw.invalidate()
            with contextlib.suppress(Exception):
                await raw.close()

    async def _acquire_inner(self, key: int) -> bool:
        async with self._io_lock:
            if key in self._held_keys:
                return False
            for attempt in range(2):
                try:
                    conn = await self._ensure_connection()
                    acquired = bool(
                        (
                            await conn.execute(
                                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                            )
                        ).scalar_one()
                    )
                except DBAPIError as exc:
                    await self._discard_connection()
                    if is_connection_loss(exc):
                        if attempt == 0:
                            continue
                        raise _unavailable(exc) from exc
                    raise
                except Exception:
                    await self._discard_connection()
                    raise

                if acquired:
                    self._held_keys.add(key)
                    return True
                if not self._held_keys:
                    await self._checkin_connection()
                return False
            return False

    async def _release_inner(self, key: int) -> None:
        async with self._io_lock:
            if key not in self._held_keys:
                return
            self._held_keys.discard(key)
            conn = self._autocommit_conn
            if conn is None:
                return
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            except Exception as exc:
                logger.warning("advisory_lock_release_failed", key=key, error=repr(exc))
                await self._discard_connection()
                return
            if not self._held_keys:
                await self._checkin_connection()


class DatabaseSession:
    """Manages a SQLAlchemy async engine and session factory for a single database."""

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._backend = get_backend(config)
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._is_sqlite = self._backend.dialect_name == "sqlite"
        self._keyed_locks: dict[str, _RefCountedKeyLock] = {}
        self._lock_mux: _SharedAdvisoryLockMultiplexer | None = None

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
        else:
            configure_idle_gated_pre_ping(
                self._engine,
                idle_s=self._config.pool_pre_ping_idle_s,
            )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            sync_session_class=_CommitTrackingSession,
        )

    async def close(self) -> None:
        """Dispose the engine and release all connections."""
        if self._lock_mux is not None:
            await self._lock_mux.close()
            self._lock_mux = None
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield an async session, ensuring cleanup on exit.

        A lost Postgres connection (see
        :func:`~jentic_one.shared.db.connectivity.is_connection_loss`) raises
        :class:`DatabaseUnavailableError` so web surfaces answer a retryable
        503; any other exception propagates unchanged.
        """
        factory = self.session_factory
        async with factory() as sess:
            try:
                yield sess
            except DBAPIError as exc:
                if is_connection_loss(exc):
                    raise _unavailable(exc) from exc
                raise
            finally:
                await sess.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a session inside a transaction that commits on clean exit.

        On IntegrityError (in the body or at commit): rolls back and raises
        DatabaseIntegrityError.
        On DataError (e.g. a value too long for its column): rolls back and
        raises DatabaseDataError.
        On OperationalError/DisconnectionError, or any DBAPI error that means the
        Postgres connection was lost (``is_connection_loss`` — asyncpg surfaces
        a dropped or terminated backend as a bare ``DBAPIError``/
        ``InterfaceError``, not ``OperationalError``): raises
        DatabaseUnavailableError.
        A connection lost at commit raises DatabaseCommitOutcomeUnknownError
        (a DatabaseUnavailableError) instead: the commit may have landed. This
        also covers a manual ``session.commit()`` inside the body.
        A failed commit is not retried: the body has already run, and only
        :meth:`run_in_transaction` can replay it on a fresh session.
        On any other exception: rolls back and re-raises unchanged.

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
                if is_connection_loss(exc):
                    await _rollback_after_connection_loss(sess)
                    raise _connection_lost_error(sess, exc) from exc
                await sess.rollback()
                raise _unavailable(exc) from exc
            except InvalidRequestError as exc:
                # Belt-and-braces for an accidental async lazy load (MissingGreenlet
                # is an InvalidRequestError) on a stale/detached ORM instance: map it
                # to a known domain error so it surfaces as a logged 500 with a
                # generic client message instead of an opaque traceback. See #642.
                await sess.rollback()
                raise DatabaseConsistencyError(str(exc)) from exc
            except DBAPIError as exc:
                # A lost connection (no pre-ping, or one dropped mid-request)
                # is transient: map it so run_in_transaction retries on a fresh
                # connection. A value that does not fit its column (e.g. a
                # string longer than the VARCHAR length) is a client-fixable
                # input problem, not a server fault. asyncpg surfaces both as a
                # bare DBAPIError, so match on the cause chain / SQLSTATE
                # (#690). Anything else propagates unchanged.
                if is_connection_loss(exc):
                    await _rollback_after_connection_loss(sess)
                    raise _connection_lost_error(sess, exc) from exc
                await sess.rollback()
                if _is_data_error(exc):
                    raise DatabaseDataError(str(exc)) from exc
                raise
            except BaseException:
                await sess.rollback()
                raise
            else:
                try:
                    await sess.commit()
                except IntegrityError as exc:
                    # A constraint can surface at commit rather than inside the
                    # body (deferred constraints, or backends/flush strategies
                    # that defer the write). Map it to the same clean error as
                    # an in-body IntegrityError so callers see one contract
                    # regardless of when the violation lands.
                    await sess.rollback()
                    raise DatabaseIntegrityError(str(exc)) from exc
                except (OperationalError, DisconnectionError) as exc:
                    if is_connection_loss(exc):
                        # Same unknown outcome as the DBAPIError branch below.
                        await _rollback_after_connection_loss(sess)
                        raise _commit_outcome_unknown(exc) from exc
                    # Never retried here: the rollback discards the body's
                    # writes, so a second commit would commit an empty
                    # transaction and report success for work that was lost
                    # (e.g. an audit event the caller relies on being durable).
                    # Replaying the body is ``run_in_transaction``'s job.
                    await sess.rollback()
                    raise _unavailable(exc) from exc
                except DBAPIError as exc:
                    # A connection lost at commit leaves the outcome unknown:
                    # the server may have applied the COMMIT before the
                    # connection dropped. Surface it at once as
                    # DatabaseCommitOutcomeUnknownError, which
                    # run_in_transaction does not replay. A too-long value can
                    # also surface at commit (deferred flush); map data
                    # exceptions to the same clean error as the in-body path
                    # and re-raise anything else (#690).
                    if is_connection_loss(exc):
                        await _rollback_after_connection_loss(sess)
                        raise _commit_outcome_unknown(exc) from exc
                    await sess.rollback()
                    if _is_data_error(exc):
                        raise DatabaseDataError(str(exc)) from exc
                    raise

    @asynccontextmanager
    async def advisory_lock(self, key: int) -> AsyncGenerator[None, None]:
        """Hold a cross-process, **session-level** advisory lock for the block.

        Postgres only; a no-op on SQLite, which has no advisory locks and
        serialises writers per transaction instead (``BEGIN IMMEDIATE``).

        The lock lives on a dedicated connection in AUTOCOMMIT mode, so it
        spans the block's own transactions (a transaction-scoped lock would
        release at the first commit) without leaving the lock connection
        *idle in transaction* — managed Postgres commonly kills such
        connections (``idle_in_transaction_session_timeout``), silently
        dropping the lock mid-run. A crashed process releases the lock when
        its connection closes.

        Release is best-effort: a lock connection lost mid-block has already
        released the lock server-side, so a failing unlock is logged and the
        connection discarded rather than raised over the block's own result.
        Session-level locks need a real session: a transaction-mode pooler
        (pgbouncer ``pool_mode=transaction``) in front of the database cannot
        hold them.
        """
        if self._is_sqlite:
            yield
            return
        async with self.engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
            try:
                yield
            finally:
                await _release_advisory_lock(conn, key)

    @asynccontextmanager
    async def try_advisory_lock(self, key: int) -> AsyncGenerator[bool, None]:
        """Try to take a cross-process, session-level advisory lock without waiting.

        Yields ``True`` when this caller holds the lock for the block and
        ``False`` when another session already holds it — the caller decides
        what to do then (typically: skip, someone else is doing the work). The
        lock is released on exit when it was acquired.

        Single-flight counterpart of :meth:`advisory_lock`, with the same
        connection model on Postgres (``pg_try_advisory_lock`` on a dedicated
        AUTOCOMMIT connection, so the lock spans the block's own transactions
        and never leaves a connection idle in a transaction; best-effort
        release). Two acquisitions from the same process use different
        connections, so they exclude each other just like two processes do.

        On SQLite there is one process per database, so this is a no-op that
        always yields ``True``; callers that also need in-process exclusion
        must hold their own ``asyncio.Lock``.
        """
        if self._is_sqlite:
            yield True
            return
        async with self.engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            acquired = bool(
                (
                    await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
                ).scalar_one()
            )
            try:
                yield acquired
            finally:
                if acquired:
                    await _release_advisory_lock(conn, key)

    @asynccontextmanager
    async def keyed_lock(self, key: str) -> AsyncGenerator[bool, None]:
        """Serialise in-process callers for ``key`` with automatic idle eviction.

        Yields ``True`` when the caller queued behind an active holder and
        ``False`` when it acquired immediately. Entries exist only while at
        least one task holds or awaits ``key``, bounding memory to active
        concurrency.
        """
        entry = self._keyed_locks.get(key)
        if entry is None:
            entry = _RefCountedKeyLock()
            self._keyed_locks[key] = entry
        waited = entry.lock.locked()
        entry.waiters += 1
        try:
            async with entry.lock:
                yield waited
        finally:
            entry.waiters -= 1
            if entry.waiters == 0 and self._keyed_locks.get(key) is entry:
                del self._keyed_locks[key]

    def _ensure_lock_mux(self) -> _SharedAdvisoryLockMultiplexer:
        if self._engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        if self._lock_mux is None:
            lock_cfg = self._config.model_copy(update={"pool_max": 1})
            url = self._backend.make_url(lock_cfg)
            kwargs = self._backend.engine_kwargs(lock_cfg)
            kwargs["max_overflow"] = 0
            lock_engine = create_async_engine(url, **kwargs)
            configure_idle_gated_pre_ping(
                lock_engine,
                idle_s=self._config.pool_pre_ping_idle_s,
            )
            self._lock_mux = _SharedAdvisoryLockMultiplexer(lock_engine)
        return self._lock_mux

    @asynccontextmanager
    async def try_shared_advisory_lock(self, key: int) -> AsyncGenerator[bool, None]:
        """Try to take a non-blocking session-level advisory lock on a shared channel.

        Multiplexes distinct 64-bit advisory lock keys onto a single dedicated
        ``AUTOCOMMIT`` connection per :class:`DatabaseSession` so concurrent
        long-running holders (such as OAuth token refreshes across distinct
        credentials) never occupy or starve the main session ``QueuePool``.
        Yields ``True`` on acquisition and ``False`` without waiting when
        held by another caller or process. On SQLite this yields ``True``.
        """
        if self._is_sqlite:
            yield True
            return
        mux = self._ensure_lock_mux()
        acquired = await mux.acquire(key)
        try:
            yield acquired
        finally:
            if acquired:
                await mux.release(key)

    async def run_in_transaction(
        self,
        fn: Callable[[AsyncSession], Awaitable[T]],
        *,
        retries: int = 2,
        backoff_s: float = 0.05,
    ) -> T:
        """Run ``fn(session)`` inside a transaction, retrying transient failures.

        Unlike :meth:`transaction` — a single-``yield`` context manager that
        cannot replay its body, so never retries — this re-invokes ``fn`` on a
        *fresh* session when a
        transient failure (``OperationalError``/``DisconnectionError``, e.g. a
        SQLite ``database is locked``, or a lost Postgres connection) is raised
        inside ``fn`` or by a definite commit failure. It waits ``backoff_s``
        between attempts and gives up after ``retries`` retries (so up to
        ``retries + 1`` total attempts), raising
        :class:`DatabaseUnavailableError`. A lost connection invalidates the
        pool, so the retry checks out a fresh connection.

        A connection lost at commit is never replayed: the commit may already
        have applied, so :class:`DatabaseCommitOutcomeUnknownError` (still a
        503 at the edge) propagates after the first attempt. The same holds
        for a manual ``session.commit()`` inside ``fn``.

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
            except DatabaseCommitOutcomeUnknownError:
                raise
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

    @asynccontextmanager
    async def read_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a read-only session whose statements run in autocommit.

        Built on :meth:`session`: the body runs inside it, so this method
        shares its cleanup and any error mapping it applies.

        Each statement runs as its own implicit transaction, so the driver sends
        no ``BEGIN`` before the first statement and no ``ROLLBACK`` at close —
        a single ``SELECT`` costs one round trip instead of three, excluding the
        ``pool_pre_ping`` checkout ping. SQLAlchemy still fires its logical
        ``begin``/``rollback`` events; the driver turns them into no-ops because
        the connection is in autocommit.

        Use it only for read-only sessions whose statements need no shared
        snapshot. At ``READ COMMITTED`` (the Postgres server default; the
        application sets no isolation level) each statement of a multi-statement
        transaction takes its own snapshot anyway, so a read-only session sees
        the same data with or without ``BEGIN``. A server, database or role
        whose ``default_transaction_isolation`` is stricter (``REPEATABLE READ``
        or ``SERIALIZABLE``) gives :meth:`session` one snapshot for the whole
        session; here each statement still gets its own. Reads that must see
        one snapshot use :meth:`transaction`.

        Writes raise :class:`~sqlalchemy.exc.InvalidRequestError` instead of
        committing: there is no transaction to roll back, so a flushed change
        would be durable at once. The guard refuses an ORM flush with pending
        changes (including an autoflush before a query) and any
        ``INSERT``/``UPDATE``/``DELETE`` statement run through
        ``Session.execute`` (ORM or Core DML, including DML nested in a Core
        CTE, or textual SQL whose first verb after any comments is
        ``INSERT``/``UPDATE``/``DELETE``/``MERGE``). It runs client-side and
        costs no round trip. DML inside a textual CTE, and SQL sent below the
        session (e.g. ``Connection.exec_driver_sql``), are not inspected.

        Autocommit covers the session's first transaction only: it is applied
        to the connection that transaction checks out. A ``commit()`` or
        ``rollback()`` inside the body ends it, and the next statement autobegins
        on a fresh checkout at the default level. Never end the transaction
        inside the body.

        On SQLite every pooled connection is already in driver autocommit
        (``enable_sqlite_manual_begin``), so reads emit no ``BEGIN`` and the
        isolation level is left alone. Setting ``AUTOCOMMIT`` there would make
        SQLAlchemy reset the connection to its detected default on checkin,
        which turns pysqlite's implicit ``BEGIN`` back on for every later user
        of that pooled connection.

        The autocommit level is applied after pool checkout, so a
        ``pool_pre_ping`` ping still runs at the connection's default level. On
        checkin SQLAlchemy restores that default; asyncpg then opens the next
        transaction on that pooled connection with ``BEGIN ISOLATION LEVEL
        <default>`` (e.g. ``READ COMMITTED``) rather than a bare ``BEGIN``.
        """
        async with self.session() as sess:
            install_read_only_guard(sess)
            if not self._is_sqlite:
                await sess.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
            yield sess

    @asynccontextmanager
    async def read_snapshot(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a read-only session whose statements all see one snapshot.

        The counterpart to :meth:`read_session` for a read that fans a single
        answer out over several statements. At ``READ COMMITTED`` every
        statement takes its own snapshot, so a commit landing between two of
        them leaves the parts of one response describing different instants —
        a total that disagrees with the sum of its own buckets. On Postgres
        this opens the transaction at ``REPEATABLE READ``, which pins the
        snapshot taken by the first statement for every later one.

        :meth:`transaction` also gives one snapshot under a stricter level, but
        it is shaped for writes: it commits on clean exit and on SQLite takes
        the write lock up front with ``BEGIN IMMEDIATE``. Here nothing is
        written, so the transaction is rolled back at the end, and SQLite uses
        ``BEGIN DEFERRED`` to take a read snapshot while WAL readers and the
        writer stay concurrent.

        Writes raise :class:`~sqlalchemy.exc.InvalidRequestError` through the
        same guard :meth:`read_session` installs. Never commit or roll back
        inside the body: either ends the snapshot, and the next statement
        autobegins a new one at the connection's default level.
        """
        async with self.session() as sess:
            install_read_only_guard(sess)
            if self._is_sqlite:
                # isolation_level=None disabled the DBAPI's implicit BEGIN, so
                # the read transaction starts explicitly. DEFERRED takes no lock
                # until the first read, unlike transaction()'s IMMEDIATE.
                conn = await sess.connection()
                await conn.exec_driver_sql("BEGIN DEFERRED")
            else:
                await sess.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            try:
                yield sess
            finally:
                # Ends the snapshot. Nothing was written, so there is nothing to
                # commit, and a rollback is correct on the error path too.
                await sess.rollback()
