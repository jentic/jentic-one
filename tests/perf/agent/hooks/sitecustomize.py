"""Perf-suite trace hook for the *traced* broker (test-only; never in the product).

Mounted read-only at ``/perf-hooks`` and activated only when compose sets
``PYTHONPATH=/perf-hooks`` and ``PERF_TRACE`` (comma list: ``sql``, ``stages``).
Python imports ``sitecustomize`` at startup, before any ``jentic_one`` module,
so the meta-path finder below sees every target module load and patches it
right after it executes — before any ``from x import y`` binds the original.

Events are appended as JSONL to ``$PERF_TRACE_OUT/<pid>.jsonl``; every event
carries the broker ``request_id`` (``jentic_one.shared.logging.request_id_ctx``)
and a ``perf_counter`` timestamp. The host-side analysers live in
``tests/perf/agent/``.

* ``sql``: SQLAlchemy engine events (statements with rows/ms/params, begin /
  commit / rollback), pool checkout/checkin, asyncpg ``do_ping`` (pre-ping), and
  the issuing ``jentic_one`` callsite. Callsites come from a contextvar set by
  wrappers on ``DatabaseSession.session/transaction`` and ``AsyncSession``
  methods — SQLAlchemy runs ORM code in greenlets that inherit the context but
  not the caller's frames, so the stack alone cannot name the caller.
* ``stages``: wall time of selected functions (coroutines, sync functions and
  ``@asynccontextmanager`` factories, the latter split into ``.enter`` and
  ``.body``), plus a 10 ms event-loop lag sampler.

Imports: stdlib only at module level; everything else via
``importlib.import_module`` (``tests/arch/test_no_inline_imports.py`` scans this file).
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import functools
import importlib
import importlib.abc
import importlib.machinery
import inspect
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Sequence
from types import FrameType, ModuleType
from typing import Any

TRACE = {t.strip() for t in os.environ.get("PERF_TRACE", "").split(",") if t.strip()}
OUT_DIR = os.environ.get("PERF_TRACE_OUT", "/out/trace")

#: module -> qualnames to time (``stages``). Extend with PERF_TRACE_FUNCS.
DEFAULT_STAGES: dict[str, tuple[str, ...]] = {
    "jentic_one.broker.web.deps": ("require_broker_identity",),
    "jentic_one.broker.core.token_validation": ("CachedTokenValidator.validate",),
    "jentic_one.broker.web.routers.execute": ("_handle", "_persist_streaming_outcome"),
    "jentic_one.broker.services.discovery": ("discover",),
    "jentic_one.broker.services.execution.authorization": ("authorize_execution",),
    "jentic_one.broker.services.credentials.resolver": ("CredentialResolver.resolve",),
    "jentic_one.shared.url_validation": (
        "validate_upstream_url",
        "validate_upstream_url_async",
    ),
    "jentic_one.shared.egress": ("resolve_and_validate", "resolve_and_validate_async"),
    "jentic_one.broker.adapters.runners.http": ("HttpRunner.stream",),
    "jentic_one.broker.web.streaming": ("open_streaming_response",),
    "jentic_one.shared.executions.ingest": ("record_execution",),
    "jentic_one.shared.events": ("emit_event",),
}

_callsite: contextvars.ContextVar[str] = contextvars.ContextVar("perf_callsite", default="")
_lock = threading.Lock()
_queue: collections.deque[dict[str, Any]] = collections.deque()
_writer_started = threading.Event()
_rid_var: contextvars.ContextVar[str] | None = None
_lag_started = False
_lag_task: asyncio.Task[None] | None = None
_SKIP_PATHS = ("/jentic_one/shared/db/", "/perf-hooks/")
_PKG_RE = re.compile(r".*/(jentic_one/.*)$")


# --------------------------------------------------------------------------- output


def _emit(event: dict[str, Any]) -> None:
    """Queue an event; a daemon thread serialises + writes it off the event loop.

    Synchronous per-event writes to a Docker Desktop bind mount cost ~ms each on
    the loop and distorted the very latencies being measured.
    """
    event["t"] = time.perf_counter()
    event["rid"] = _request_id()
    _queue.append(event)
    if not _writer_started.is_set():
        with _lock:
            if not _writer_started.is_set():
                threading.Thread(target=_writer, name="perf-hooks-writer", daemon=True).start()
                _writer_started.set()


def _writer() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{os.getpid()}.jsonl")
    with open(path, "a") as out:
        while True:
            time.sleep(0.2)
            batch: list[str] = []
            while _queue:
                batch.append(json.dumps(_queue.popleft(), default=str))
            if batch:
                out.write("\n".join(batch) + "\n")
                out.flush()


def _request_id() -> str:
    global _rid_var
    if _rid_var is None:
        mod = sys.modules.get("jentic_one.shared.logging")
        if mod is None:
            return ""
        _rid_var = mod.request_id_ctx
    return _rid_var.get()


def _caller(skip: int = 2, depth: int = 3) -> str:
    """The nearest ``jentic_one`` frames outside ``shared/db`` (``a.py:12 f <- b.py:9 g``)."""
    frame: FrameType | None = sys._getframe(skip)
    out: list[str] = []
    while frame is not None and len(out) < depth:
        path = frame.f_code.co_filename
        if "/jentic_one/" in path and not any(s in path for s in _SKIP_PATHS):
            m = _PKG_RE.match(path)
            rel = f"src/{m.group(1)}" if m else path
            out.append(f"{rel}:{frame.f_lineno} {frame.f_code.co_name}")
        frame = frame.f_back
    return " <- ".join(out)


# --------------------------------------------------------------------------- sql


def _plane_of_dbapi(dbapi_conn: Any) -> str:
    raw = getattr(dbapi_conn, "_connection", None)  # asyncpg.Connection under the adapter
    params = getattr(raw, "_params", None)
    user = str(getattr(params, "user", "") or "")
    return user.removesuffix("_user") or "?"


def _plane_of(conn: Any) -> str:
    try:
        return str(conn.engine.url.username or "?").removesuffix("_user")
    except AttributeError:
        return "?"


def _param_types(params: Any) -> list[str]:
    seq: Sequence[Any] = params if isinstance(params, (list, tuple)) else []
    return [type(p).__name__ for p in seq]


def _param_values(params: Any) -> list[Any]:
    seq: Sequence[Any] = params if isinstance(params, (list, tuple)) else []
    out: list[Any] = []
    for p in seq:
        if p is None or isinstance(p, (bool, int, float)):
            out.append(p)
        else:
            out.append(str(p)[:200])
    return out


def _install_sql() -> None:
    event = importlib.import_module("sqlalchemy.event")
    engine_mod = importlib.import_module("sqlalchemy.engine")
    pool_mod = importlib.import_module("sqlalchemy.pool")
    engine_cls = engine_mod.Engine

    def before(conn: Any, cursor: Any, statement: str, params: Any, ctx: Any, many: bool) -> None:
        conn.info.setdefault("perf_t0", []).append(time.perf_counter())

    def after(conn: Any, cursor: Any, statement: str, params: Any, ctx: Any, many: bool) -> None:
        stack = conn.info.get("perf_t0") or [time.perf_counter()]
        t0 = stack.pop()
        # SELECTs: the asyncpg adapter prefetches into ``_rows``; DML has no
        # result set (``description`` is None), so use ``rowcount`` there.
        prefetched = getattr(cursor, "_rows", None)
        if getattr(cursor, "description", None) is None or prefetched is None:
            n_rows = getattr(cursor, "rowcount", -1)
        else:
            n_rows = len(prefetched)
        _emit(
            {
                "ev": "sql",
                "plane": _plane_of(conn),
                "sql": statement,
                "ms": round((time.perf_counter() - t0) * 1000, 3),
                "rows": n_rows,
                "ptypes": _param_types(params),
                "params": _param_values(params),
                "site": _callsite.get() or _caller(),
                "conn": id(conn.connection.dbapi_connection),
            }
        )

    def tx(name: str) -> Callable[[Any], None]:
        def handler(conn: Any) -> None:
            _emit(
                {
                    "ev": name,
                    "plane": _plane_of(conn),
                    "site": _callsite.get(),
                    "conn": id(conn.connection.dbapi_connection),
                }
            )

        return handler

    def pool_ev(name: str) -> Callable[..., None]:
        def handler(dbapi_conn: Any, *_: Any) -> None:
            _emit(
                {
                    "ev": name,
                    "plane": _plane_of_dbapi(dbapi_conn),
                    "site": _callsite.get(),
                    "conn": id(dbapi_conn),
                }
            )

        return handler

    event.listen(engine_cls, "before_cursor_execute", before)
    event.listen(engine_cls, "after_cursor_execute", after)
    for name in ("begin", "commit", "rollback"):
        event.listen(engine_cls, name, tx(name))
    event.listen(pool_mod.Pool, "checkout", pool_ev("checkout"))
    event.listen(pool_mod.Pool, "checkin", pool_ev("checkin"))


def _patch_ping(mod: ModuleType) -> None:
    dialect = mod.PGDialect_asyncpg
    original = dialect.do_ping

    @functools.wraps(original)
    def do_ping(self: Any, dbapi_connection: Any) -> Any:
        t0 = time.perf_counter()
        try:
            return original(self, dbapi_connection)
        finally:
            _emit(
                {
                    "ev": "ping",
                    "plane": _plane_of_dbapi(dbapi_connection),
                    "ms": round((time.perf_counter() - t0) * 1000, 3),
                    "site": _callsite.get(),
                    "conn": id(dbapi_connection),
                }
            )

    dialect.do_ping = do_ping


def _with_callsite_cm(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an asynccontextmanager factory so its body runs under the caller's site."""

    @functools.wraps(original)
    def factory(*args: Any, **kwargs: Any) -> Any:
        return _SiteCM(original(*args, **kwargs), _caller())

    return factory


class _SiteCM:
    def __init__(self, inner: Any, site: str) -> None:
        self._inner = inner
        self._site = site
        self._token: contextvars.Token[str] | None = None

    async def __aenter__(self) -> Any:
        self._token = _callsite.set(self._site)
        return await self._inner.__aenter__()

    async def __aexit__(self, *exc: Any) -> Any:
        try:
            return await self._inner.__aexit__(*exc)
        finally:
            if self._token is not None:
                _callsite.reset(self._token)


def _with_callsite_coro(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    async def method(*args: Any, **kwargs: Any) -> Any:
        token = _callsite.set(_caller())
        try:
            return await original(*args, **kwargs)
        finally:
            _callsite.reset(token)

    return method


def _patch_db_session(mod: ModuleType) -> None:
    cls = mod.DatabaseSession
    for name in ("session", "transaction"):
        setattr(cls, name, _with_callsite_cm(getattr(cls, name)))
    async_session = importlib.import_module("sqlalchemy.ext.asyncio").AsyncSession
    for name in ("execute", "scalars", "scalar", "get", "flush", "commit"):
        setattr(async_session, name, _with_callsite_coro(getattr(async_session, name)))
    _install_sql()


# --------------------------------------------------------------------------- stages


def _stage_event(name: str, t0: float, **extra: Any) -> None:
    _emit({"ev": "stage", "name": name, "ms": round((time.perf_counter() - t0) * 1000, 3), **extra})


def _ensure_lag_sampler() -> None:
    global _lag_started, _lag_task
    if _lag_started:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _lag_started = True
    _lag_task = loop.create_task(_lag_sampler())


async def _lag_sampler(interval: float = 0.01) -> None:
    """Every 10 ms, record how late the loop woke us (= time something blocked it)."""
    samples: list[float] = []
    window = time.perf_counter()
    while True:
        t0 = time.perf_counter()
        await asyncio.sleep(interval)
        samples.append(round(max(0.0, (time.perf_counter() - t0 - interval) * 1000), 2))
        if time.perf_counter() - window >= 1.0:
            _emit({"ev": "lag", "samples_ms": samples})
            samples, window = [], time.perf_counter()


class _StageCM:
    """Times an ``@asynccontextmanager`` result: ``.enter`` and ``.body`` separately."""

    def __init__(self, inner: Any, name: str) -> None:
        self._inner = inner
        self._name = name
        self._t_body = 0.0

    async def __aenter__(self) -> Any:
        t0 = time.perf_counter()
        try:
            return await self._inner.__aenter__()
        finally:
            _stage_event(f"{self._name}.enter", t0)
            self._t_body = time.perf_counter()

    async def __aexit__(self, *exc: Any) -> Any:
        try:
            return await self._inner.__aexit__(*exc)
        finally:
            _stage_event(f"{self._name}.body", self._t_body)


def _stage_wrapper(original: Callable[..., Any], name: str) -> Callable[..., Any]:
    target = getattr(original, "__wrapped__", None)
    if target is not None and inspect.isasyncgenfunction(target):

        @functools.wraps(original)
        def cm_factory(*args: Any, **kwargs: Any) -> Any:
            _ensure_lag_sampler()
            return _StageCM(original(*args, **kwargs), name)

        return cm_factory
    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def coro(*args: Any, **kwargs: Any) -> Any:
            _ensure_lag_sampler()
            t0 = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            finally:
                _stage_event(name, t0)

        return coro

    @functools.wraps(original)
    def sync(*args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            _stage_event(name, t0)

    return sync


def _retarget_depends(mod: ModuleType, original: Any, wrapper: Any) -> None:
    """Point module-level ``Annotated[..., Depends(original)]`` aliases at *wrapper*.

    ``Depends`` is a frozen dataclass captured when the alias is defined (during
    the module's own exec), so replacing the module attribute alone is not enough.
    """
    for value in list(vars(mod).values()):
        for meta in getattr(value, "__metadata__", ()) or ():
            if getattr(meta, "dependency", None) is original:
                object.__setattr__(meta, "dependency", wrapper)


def _patch_stages(mod: ModuleType, qualnames: Sequence[str]) -> None:
    for qual in qualnames:
        owner: Any = mod
        *parents, attr = qual.split(".")
        try:
            for p in parents:
                owner = getattr(owner, p)
            original = inspect.getattr_static(owner, attr)
        except AttributeError:
            sys.stderr.write(f"perf-hooks: stage {mod.__name__}:{qual} not found, skipped\n")
            continue
        is_static = isinstance(original, staticmethod)
        func = original.__func__ if is_static else original
        wrapper = _stage_wrapper(func, f"{mod.__name__.removeprefix('jentic_one.')}:{qual}")
        setattr(owner, attr, staticmethod(wrapper) if is_static else wrapper)
        _retarget_depends(mod, func, wrapper)


def _stage_targets() -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {m: list(q) for m, q in DEFAULT_STAGES.items()}
    for item in os.environ.get("PERF_TRACE_FUNCS", "").split(","):
        mod, _, qual = item.strip().partition(":")
        if mod and qual:
            full = mod if mod.startswith("jentic_one") else f"jentic_one.{mod}"
            targets.setdefault(full, []).append(qual)
    return targets


# --------------------------------------------------------------------------- import hook


class _PatchingLoader(importlib.abc.Loader):
    def __init__(self, inner: importlib.abc.Loader, patch: Callable[[ModuleType], None]) -> None:
        self._inner = inner
        self._patch = patch

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return self._inner.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self._inner.exec_module(module)
        try:
            self._patch(module)
        except Exception as exc:  # never break the broker because of the hook
            sys.stderr.write(f"perf-hooks: patching {module.__name__} failed: {exc!r}\n")


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, patches: dict[str, Callable[[ModuleType], None]]) -> None:
        self._patches = patches

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        patch = self._patches.get(fullname)
        if patch is None:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = _PatchingLoader(spec.loader, patch)
                return spec
        return None


def _install() -> None:
    patches: dict[str, Callable[[ModuleType], None]] = {}
    if "sql" in TRACE:
        patches["jentic_one.shared.db.session"] = _patch_db_session
        patches["sqlalchemy.dialects.postgresql.asyncpg"] = _patch_ping
    if "stages" in TRACE:
        for mod, quals in _stage_targets().items():
            previous = patches.get(mod)

            def patch(m: ModuleType, q: list[str] = quals, prev: Any = previous) -> None:
                if prev is not None:
                    prev(m)
                _patch_stages(m, q)

            patches[mod] = patch
    if patches:
        sys.meta_path.insert(0, _Finder(patches))
        sys.stderr.write(f"perf-hooks: tracing {sorted(TRACE)} -> {OUT_DIR}\n")


_install()
