"""Combined app factory that includes per-surface routers in a flat app."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import opentelemetry.instrumentation.fastapi as otel_fastapi
import structlog
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from jentic_one import __version__
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.jobs.credential_expiry_scanner import CredentialExpiryScanner
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.logging import RequestIDMiddleware
from jentic_one.shared.metrics import make_metrics_asgi_app
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.telemetry.client import TelemetryClient
from jentic_one.shared.telemetry.instance_id import resolve_instance_id
from jentic_one.shared.telemetry.loop import TelemetryFlushLoop
from jentic_one.shared.telemetry.sink import TelemetrySink, set_active_sink
from jentic_one.shared.tracing import instrument_inbound_app
from jentic_one.shared.web.container import AppContainer
from jentic_one.shared.web.openapi_meta import (
    fastapi_metadata_kwargs,
    install_openapi_metadata,
)
from jentic_one.shared.web.openapi_responses import COMMON_ERROR_RESPONSES
from jentic_one.shared.web.reference_router import get_reference_router

_logger = structlog.get_logger(__name__)

SURFACE_MODULES = {
    "registry": "jentic_one.registry.web.app",
    "admin": "jentic_one.admin.web.app",
    "control": "jentic_one.control.web.app",
    "broker": "jentic_one.broker.web.app",
    "auth": "jentic_one.auth.web.app",
}

_db_instrumented = False
_otel_route_guard_installed = False


def _install_otel_route_detail_guard() -> None:
    """Stop OTel FastAPI instrumentation 500ing on partial route matches.

    ``opentelemetry.instrumentation.fastapi._get_route_details`` walks
    ``app.routes`` and reads ``route.path``. FastAPI now wraps ``include_router``
    results in an opaque ``_IncludedRouter`` that has no ``path`` (the same quirk
    handled in ``shared/web/static.py``). Upstream guards the ``Match.FULL``
    branch with ``try/except AttributeError`` but not the ``Match.PARTIAL`` one,
    so any request that path-matches an included router without matching a method
    — a CORS ``OPTIONS`` preflight, a ``405`` — raises ``AttributeError`` and the
    span-name extraction turns it into a ``500`` (verified on
    ``opentelemetry-instrumentation-fastapi==0.63b1``). We wrap the function to
    fall back to the request path. Idempotent; the global guard is process-wide.
    """
    global _otel_route_guard_installed
    if _otel_route_guard_installed:
        return

    original: Any = otel_fastapi._get_route_details

    def _safe_get_route_details(scope: dict[str, Any]) -> Any:
        try:
            return original(scope)
        except AttributeError:
            return scope.get("path")

    otel_fastapi._get_route_details = _safe_get_route_details
    _otel_route_guard_installed = True


def attach_http_observability(app: FastAPI) -> None:
    """Wire FastAPI auto-instrumentation and (when enabled) mount /metrics.

    Safe to call once per FastAPI instance. DB instrumentation is *not* done
    here because it must happen after `Context.startup()` has created the
    SQLAlchemy engines — see `instrument_databases`.

    The Prometheus ASGI app is mounted at "/metrics", which causes Starlette
    to redirect the bare "/metrics" path to "/metrics/" with a 307. The
    `local-prom-app.yaml` overlay therefore sets `prometheus.io/path` to
    "/metrics/" with the trailing slash — keep them in sync.
    """
    _install_otel_route_detail_guard()
    instrument_inbound_app(app)

    metrics_app = make_metrics_asgi_app()
    if metrics_app is not None:
        app.mount("/metrics", metrics_app)


def instrument_databases(ctx: Context) -> None:
    """Instrument SQLAlchemy + asyncpg using engines from the live Context.

    Must be called after `ctx.startup()` so engines exist. Idempotent across
    the whole process: the underlying instrumentors are global and only the
    first call wires them up.
    """
    global _db_instrumented
    if _db_instrumented:
        return
    _db_instrumented = True

    AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]

    sqlalchemy_instrumentor = SQLAlchemyInstrumentor()
    for name in ("registry", "admin", "control"):
        if not ctx.has_db(name):
            continue
        db = getattr(ctx, f"{name}_db")
        sqlalchemy_instrumentor.instrument(engine=db.engine.sync_engine)


def _start_worker(
    ctx: Context,
    *,
    upstream_executor: Any | None = None,
    credential_injector: Any | None = None,
) -> tuple[WorkerLoop, asyncio.Task[None]] | None:
    """Start the background worker if the admin DB is available.

    ``upstream_executor`` is the broker-side ``UpstreamExecutor`` (the
    ``PipelineExecutor`` over the shared composed runner, §11 RN-0.3) and
    ``credential_injector`` is the broker ``CredentialService``; both are built
    by the broker's surface lifespan and stashed on ``app.state`` (so this
    ``shared/`` factory never imports ``broker/``). When the executor is
    provided the execution handler dispatches through the **same** pipeline as
    the sync router (circuit breaker, per-host bulkhead, shared pool, post-
    response enrichment, single ``executions`` persistence) and resolves
    credentials before the call. Without it the execution handler is not
    registered (a surface with no broker has no upstream calls to run).

    Returns the ``(worker, task)`` pair so the lifespan can **drain** the worker
    (let the in-flight job finish or be reclaimed) before tearing the shared
    client/runners down — see ``_stop_worker`` (§09 E4.3).
    """
    if not ctx.has_db("admin"):
        return None

    handler_registry = JobHandlerRegistry()

    if ctx.has_db("registry"):
        handler_registry.register(JobKind.IMPORT, ImportHandler(ctx))

    if ctx.has_db("control") and upstream_executor is not None:
        handler_registry.register(
            JobKind.EXECUTION,
            ExecutionHandler(
                executor=upstream_executor,
                upstream_timeout_s=ctx.config.broker.upstream_timeout_s,
                credential_injector=credential_injector,
                egress=ctx.config.broker.egress,
                security_config=ctx.config.security,
            ),
        )

    if not handler_registry.kinds:
        return None

    worker = WorkerLoop(ctx.admin_db, handler_registry, worker_config=ctx.config.worker)
    task = asyncio.create_task(worker.run())
    _logger.info("worker_loop_task_started")
    return worker, task


def _start_expiry_scanner(
    ctx: Context,
) -> tuple[CredentialExpiryScanner, asyncio.Task[None]] | None:
    """Start the credential-expiry scanner when both control + admin DBs exist.

    The sweep reads OAuth token expiries from the **control** DB and writes
    ``credential.expiring_soon`` / ``credential.expired`` events into the
    **admin** DB, so both must be present. Without either DB there is nothing to
    scan (or nowhere to record events), so the scanner is not started.
    """
    if not (ctx.has_db("control") and ctx.has_db("admin")):
        return None
    scanner = CredentialExpiryScanner(
        ctx.control_db,
        ctx.admin_db,
        security_config=ctx.config.security,
    )
    task = asyncio.create_task(scanner.run())
    _logger.info("credential_expiry_scanner_task_started")
    return scanner, task


async def _stop_expiry_scanner(
    handle: tuple[CredentialExpiryScanner, asyncio.Task[None]] | None,
) -> None:
    """Signal and cancel the credential-expiry scanner (best-effort)."""
    if handle is None:
        return
    scanner, task = handle
    scanner.stop()
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=5.0)


async def _stop_worker(handle: tuple[WorkerLoop, asyncio.Task[None]] | None) -> None:
    """Gracefully drain then stop the worker (§09 E4.3 teardown step 2).

    Drains first (so the in-flight job finishes or is safely reclaimable) **before**
    the surface lifespan closes the shared ``httpx`` client/runners — otherwise a
    mid-flight job would crash with ``PoolClosed``. A job that doesn't finish within
    the drain budget is left ``RUNNING`` and reclaimed via its visibility timeout on
    restart, so no work is dropped.
    """
    if handle is None:
        return
    worker, task = handle
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await worker.drain()
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=5.0)


async def _start_telemetry(
    ctx: Context,
) -> tuple[TelemetryFlushLoop, asyncio.Task[None], TelemetryClient] | None:
    """Resolve the instance id, wire the sink + flush loop, emit lifecycle events.

    No-op unless ``telemetry.enabled`` (the consent gate) **and** the admin DB is
    available (the resolve + lifecycle events need it). Emits ``instance_initialized``
    exactly once (on the process that wins the insert) and ``instance_booted`` on
    every startup — both through ``emit_event`` so they also forward to telemetry.
    Best-effort: a telemetry wiring failure must never crash startup.
    """
    cfg = ctx.config.telemetry
    if not cfg.enabled or not ctx.has_db("admin"):
        return None
    try:
        instance_id, created = await resolve_instance_id(ctx.admin_db, cfg)
    except Exception as exc:
        _logger.warning("telemetry_instance_id_resolve_failed", error=str(exc))
        return None

    sink = TelemetrySink(enabled=True, queue_max=cfg.queue_max)
    ctx.instance_id = instance_id
    ctx.telemetry = sink
    set_active_sink(sink)

    client = TelemetryClient(
        endpoint=cfg.endpoint,
        instance_id=instance_id,
        version=__version__,
        request_timeout_s=cfg.request_timeout_s,
    )
    loop = TelemetryFlushLoop(
        sink,
        client,
        flush_interval_s=cfg.flush_interval_s,
        max_batch=cfg.max_batch,
    )
    task = asyncio.create_task(loop.run())
    _logger.info("telemetry_flush_loop_task_started")

    async with ctx.admin_db.transaction() as session:
        if created:
            await emit_event_best_effort(
                session,
                type=EventType.INSTANCE_INITIALIZED,
                severity=EventSeverity.INFO,
                summary="Instance initialized",
                created_by=None,
            )
        await emit_event_best_effort(
            session,
            type=EventType.INSTANCE_BOOTED,
            severity=EventSeverity.INFO,
            summary="Instance booted",
            created_by=None,
        )

    return loop, task, client


async def _stop_telemetry(
    handle: tuple[TelemetryFlushLoop, asyncio.Task[None], TelemetryClient] | None,
) -> None:
    """Drain + cancel the telemetry flush loop (best-effort), then close the client."""
    set_active_sink(None)
    if handle is None:
        return
    loop, task, client = handle
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await loop.drain()
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=5.0)
    with contextlib.suppress(Exception):
        await client.aclose()


@asynccontextmanager
async def _null_lifespan() -> AsyncGenerator[None]:
    """No-op surface lifespan used when a surface provides no extra_lifespan."""
    yield


def create_surface_app(
    ctx: Context,
    *,
    title: str,
    routers: Sequence[tuple[APIRouter, str, list[str]]],
    extra_lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
    container: AppContainer | None = None,
) -> FastAPI:
    """Create a standalone surface FastAPI app with observability wired.

    Routers are mounted without their prefix (standalone mode serves at root,
    e.g. /health rather than /broker/health — the combined-mode root app
    keeps the prefix so /broker/health, /admin/health, etc. don't collide).

    ``extra_lifespan`` is an optional surface-owned async context manager entered
    after ``ctx.startup()`` and exited before ``ctx.shutdown()`` — the broker
    uses it to open/close its shared outbound ``httpx.AsyncClient`` (§04).

    ``container`` is the DI seam: when omitted the default is used and behavior is
    unchanged. A caller passes its own container to inject a ``Broker`` (stashed on
    ``app.state``) and mount extra routers after the surface's own.
    """
    container = container or AppContainer.default(ctx)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await ctx.startup()
        instrument_databases(ctx)
        telemetry_handle = await _start_telemetry(ctx)
        async with extra_lifespan(app) if extra_lifespan else _null_lifespan():
            # Worker starts *inside* the surface lifespan so it can share any
            # surface-owned resource (e.g. the broker's shared upstream
            # executor + credential injector stashed on app.state by
            # extra_lifespan) — §04 / §11 RN-0.3.
            worker_task = _start_worker(
                ctx,
                upstream_executor=getattr(app.state, "broker_upstream_executor", None),
                credential_injector=getattr(app.state, "broker_credential_injector", None),
            )
            scanner_task = _start_expiry_scanner(ctx)
            try:
                yield
            finally:
                # §09 E4.3 drain step 1: signal the admission gate (if any) to
                # report unready + stamp Connection: close, so the LB deregisters
                # this instance *before* we drain in-flight work and tear down the
                # worker (step 2) and — in extra_lifespan's exit — the shared
                # client/runners (step 4). Strict order: unready → worker → client.
                gate = getattr(app.state, "broker_admission_gate", None)
                if gate is not None and hasattr(gate, "start_draining"):
                    gate.start_draining()
                await _stop_expiry_scanner(scanner_task)
                await _stop_worker(worker_task)
                await _stop_telemetry(telemetry_handle)
                await ctx.shutdown()

    meta = fastapi_metadata_kwargs()
    meta["title"] = title
    app = FastAPI(lifespan=lifespan, **meta)
    app.state.ctx = ctx
    if container.broker is not None:
        # Wire the injected broker into BOTH data-plane paths: the sync router
        # reads app.state.broker, while the async worker builds its broker via
        # app.state.broker_factory(runner). Without the factory the async path
        # silently falls back to the default broker.
        app.state.broker = container.broker
        app.state.broker_factory = lambda _runner: container.broker
    for router, _prefix, tags in routers:
        app.include_router(router, tags=list(tags), responses=COMMON_ERROR_RESPONSES)
    for extra_router, extra_prefix, extra_tags in container.extra_routers:
        app.include_router(
            extra_router,
            prefix=extra_prefix,
            tags=list(extra_tags),
            responses=COMMON_ERROR_RESPONSES,
        )
    for installer in container.extra_installers:
        installer(app, ctx)
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    attach_http_observability(app)
    install_openapi_metadata(app)
    return app


def create_combined_app(
    ctx: Context,
    apps: list[str],
    *,
    container: AppContainer | None = None,
) -> FastAPI:
    """Build a root FastAPI that includes routers from all enabled surfaces.

    ``container`` is the DI seam: when omitted, the default is used and behavior is
    unchanged. A caller passes its own container to inject a ``Broker`` and mount
    extra routers/installers after all built-in surfaces.
    """
    container = container or AppContainer.default(ctx)
    structlog.get_logger(__name__).info("creating combined app", apps=apps)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await ctx.startup()
        instrument_databases(ctx)
        telemetry_handle = await _start_telemetry(ctx)
        worker_task = _start_worker(ctx)
        scanner_task = _start_expiry_scanner(ctx)
        try:
            yield
        finally:
            await _stop_expiry_scanner(scanner_task)
            await _stop_worker(worker_task)
            await _stop_telemetry(telemetry_handle)
            await ctx.shutdown()

    root = FastAPI(lifespan=lifespan, **fastapi_metadata_kwargs())
    root.state.ctx = ctx
    # Injected Broker (None by default → broker surface builds its default
    # per request). Wire BOTH data-plane paths: the sync router reads
    # root.state.broker; the async worker builds its broker via
    # root.state.broker_factory(runner). Without the factory the async path
    # silently falls back to the default broker.
    if container.broker is not None:
        root.state.broker = container.broker
        root.state.broker_factory = lambda _runner: container.broker
    root.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]

    @root.get(
        "/health",
        operation_id="getHealth",
        summary="Health",
        tags=["System"],
    )
    async def health() -> JSONResponse:
        """Liveness probe for the combined control-plane app.

        Unauthenticated and dependency-free so orchestrators and load balancers
        have a stable target. Returns ``{"status": "ok"}`` when the process is up.
        """
        return JSONResponse({"status": "ok", "version": __version__})

    for surface in apps:
        module_path = SURFACE_MODULES[surface]
        mod = importlib.import_module(module_path)
        for router, prefix, tags in mod.get_routers():
            root.include_router(router, prefix=prefix, tags=tags, responses=COMMON_ERROR_RESPONSES)
        if hasattr(mod, "get_exception_handlers"):
            for exc_class, handler in mod.get_exception_handlers():
                root.add_exception_handler(exc_class, handler)
        if hasattr(mod, "install_on_app"):
            mod.install_on_app(root, ctx)

    # Public, schema-hidden endpoint reference (the CLI + docs SPA read this
    # instead of parsing the OpenAPI document). Registered after all surfaces so
    # the reference it builds covers every included route.
    root.include_router(get_reference_router())

    # Extension point: injected routers/installers mount after all built-in
    # surfaces (append-only; never shadows a built-in route). No-op by default.
    for router, prefix, tags in container.extra_routers:
        root.include_router(
            router, prefix=prefix, tags=list(tags), responses=COMMON_ERROR_RESPONSES
        )
    for installer in container.extra_installers:
        installer(root, ctx)

    root.add_middleware(RequestIDMiddleware)
    attach_http_observability(root)
    install_openapi_metadata(root)

    # Serve the SPA once when the admin surface is active. ``app.frontend()``
    # registers low-priority routes, so they are checked only after every API
    # route and never shadow one. No-op when no UI bundle is packaged.
    if "admin" in apps:
        admin_mod = importlib.import_module(SURFACE_MODULES["admin"])
        admin_mod.mount_ui(root)

    return root


def reset_db_instrumentation() -> None:
    """Reset the DB-instrumentation guard. For testing only."""
    global _db_instrumented
    _db_instrumented = False
