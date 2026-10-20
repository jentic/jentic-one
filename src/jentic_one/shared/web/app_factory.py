"""Combined app factory that includes per-surface routers in a flat app."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import importlib
import inspect
import re
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from starlette.routing import Route

from jentic_one import __version__
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.catalog import CatalogAutoImportProtocol
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.jobs.catalog_update_scanner import CatalogUpdateScanner
from jentic_one.shared.jobs.connect_poll_scanner import ConnectPollScanner
from jentic_one.shared.jobs.credential_expiry_scanner import CredentialExpiryScanner
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.logging import RequestIDMiddleware
from jentic_one.shared.metrics import make_metrics_asgi_app
from jentic_one.shared.models.events import EventSeverity, EventType, HostOs
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.state.backend import StateBackendCapacityError
from jentic_one.shared.telemetry.client import TelemetryClient
from jentic_one.shared.telemetry.instance_id import resolve_instance_id
from jentic_one.shared.telemetry.loop import TelemetryFlushLoop
from jentic_one.shared.telemetry.sink import TelemetrySink, set_active_sink
from jentic_one.shared.tracing import instrument_inbound_app
from jentic_one.shared.web.agent_discovery import get_agent_discovery_router
from jentic_one.shared.web.auth import API_KEY_HEADER
from jentic_one.shared.web.background_tasks import install_background_tasks
from jentic_one.shared.web.cache_control import AuthenticatedResponseCacheMiddleware
from jentic_one.shared.web.container import AppContainer, RouterSpec
from jentic_one.shared.web.deps import TokenVerifier
from jentic_one.shared.web.errors import (
    apply_route_error_response_hook,
    request_validation_error_handler,
    state_backend_capacity_error_handler,
    unhandled_exception_handler,
)
from jentic_one.shared.web.health import RootHealthResponse
from jentic_one.shared.web.instance_identity import get_instance_router
from jentic_one.shared.web.openapi_meta import (
    fastapi_metadata_kwargs,
    install_openapi_metadata,
)
from jentic_one.shared.web.openapi_responses import COMMON_ERROR_RESPONSES
from jentic_one.shared.web.reference_router import get_reference_router
from jentic_one.shared.web.static import SPA_MOUNT_PATH
from jentic_one.shared.web.system import get_system_router

_logger = structlog.get_logger(__name__)


async def spa_aware_problem_detail_handler(
    request: Request, exc: ProblemDetailException
) -> Response:
    """Problem-details handler that lands anonymous HTML navigations in the SPA.

    A human following a link to a protected URL after their session expired is
    a *browser navigation*: ``GET``, ``Accept: text/html``, and — because the
    web session token lives in the SPA's localStorage, never in a header the
    browser sends on its own — no credential at all. Answering that with raw
    ``application/problem+json`` strands the person on a JSON blob (#813), so
    when an SPA is mounted we redirect into it and let the client-side auth
    state take over (login screen or dashboard). Everything else — API clients
    (JSON ``Accept``), requests that *did* present a credential, non-GETs —
    keeps the RFC 9457 response byte-for-byte, unless the matched route
    reshapes its errors (:func:`apply_route_error_response_hook`).
    """
    if (
        exc.status_code == 401
        and request.method == "GET"
        and getattr(request.app.state, "spa_mounted", False)
        and "text/html" in request.headers.get("accept", "")
        and "authorization" not in request.headers
        and API_KEY_HEADER not in request.headers
    ):
        return RedirectResponse(f"{SPA_MOUNT_PATH}/", status_code=302)
    return apply_route_error_response_hook(
        request, await problem_detail_exception_handler(request, exc)
    )


SURFACE_MODULES = {
    "registry": "jentic_one.registry.web.app",
    "admin": "jentic_one.admin.web.app",
    "control": "jentic_one.control.web.app",
    "broker": "jentic_one.broker.web.app",
    "auth": "jentic_one.auth.web.app",
}

#: Every surface that rides the control-plane (non-broker) app. A split
#: deployment serves these from separate processes, but together they are the
#: one control-plane API the endpoint reference documents.
CONTROL_PLANE_SURFACES: tuple[str, ...] = ("registry", "admin", "control", "auth")

#: Module holding the control-plane identity verifier chain. Imported
#: dynamically (one named seam) so the shared layer carries no static import of
#: a surface — the same posture as the :data:`SURFACE_MODULES` loop.
PLATFORM_VERIFIER_MODULE = "jentic_one.auth.web.verifier"

_db_instrumented = False


def make_platform_token_verifier(ctx: Context) -> TokenVerifier:
    """Build the one identity verifier every control-plane app installs.

    The chain (:data:`PLATFORM_VERIFIER_MODULE`) resolves agent API keys
    (``jak_``; a retired ``sak_`` key is refused), opaque ``at_`` OAuth access
    tokens (resolved against the admin DB's ``access_tokens`` table), and HS256
    web-session JWTs. Combined and standalone factories both call this, so a
    standalone surface accepts exactly the token shapes the combined app does.

    The broker is the one exception: it installs its own data-plane validator
    (JWKS trusted issuers, TTL caches) in ``broker.core.setup``.
    """
    verifier_mod = importlib.import_module(PLATFORM_VERIFIER_MODULE)
    verifier: TokenVerifier = verifier_mod.make_superset_verifier(ctx)
    return verifier


def install_platform_token_verifier(app: FastAPI, ctx: Context, *, replace: bool = True) -> None:
    """Install :func:`make_platform_token_verifier` as ``app.state.verify_token``.

    The only writer of ``app.state.verify_token`` outside the broker.
    ``replace=False`` keeps a verifier already on the app — the composition
    root's safety net for a standalone factory that installed its own (the
    broker's data-plane validator).
    """
    if replace or not hasattr(app.state, "verify_token"):
        app.state.verify_token = make_platform_token_verifier(ctx)


def _require_admin_db(ctx: Context, shape: str) -> None:
    """Fail the app build when a control-plane process cannot verify callers.

    Every control-plane route resolves its caller through the platform
    verifier, which reads API keys, access tokens and permissions from the admin
    DB. Without it ``app.state.verify_token`` would be missing and every
    authenticated request would 500, so refuse to build instead.
    """
    if not ctx.is_db_allowed("admin"):
        raise RuntimeError(
            f"{shape} needs access to the 'admin' database to verify callers "
            "(API keys, access tokens, permissions); add 'admin' to the "
            "context's allowed_dbs"
        )


def mount_metrics_endpoint(app: FastAPI) -> None:
    """Mount the Prometheus ASGI app at "/metrics" when the exporter is enabled.

    Starlette matches routes in registration order, so call this *before*
    including any router with a catch-all path (the broker's
    ``/{upstream_url:path}``) — otherwise the catch-all shadows the scrape
    endpoint.

    The exposition is served at "/metrics/"; the bare "/metrics" path answers a
    307 to it. Starlette's own trailing-slash redirect only fires when *no*
    route matches, and the broker's catch-all matches everything, so the bare
    path gets an explicit redirect route registered alongside the mount. The
    `local-prom-app.yaml` overlay sets `prometheus.io/path` to "/metrics/" with
    the trailing slash — keep them in sync.
    """
    metrics_app = make_metrics_asgi_app()
    if metrics_app is not None:
        app.add_route("/metrics", _redirect_to_metrics_slash, include_in_schema=False)
        app.mount("/metrics", metrics_app)


async def _redirect_to_metrics_slash(request: Request) -> Response:
    """307 the bare "/metrics" path to the "/metrics/" exposition mount."""
    return RedirectResponse(request.url.replace(path="/metrics/"), status_code=307)


_ROOT_CATCH_ALL_RE = re.compile(r"/\{[^{}/:]+:path\}")


def _has_root_catch_all(router: APIRouter) -> bool:
    """Whether ``router`` carries a root-level ``/{name:path}`` route.

    Such a route (the broker's forward proxy) matches every path, so Starlette
    — which matches in registration order — never reaches anything included
    after it.
    """
    return any(
        isinstance(route, Route) and _ROOT_CATCH_ALL_RE.fullmatch(route.path) is not None
        for route in router.routes
    )


def attach_http_observability(app: FastAPI, *, mount_metrics: bool = True) -> None:
    """Wire FastAPI auto-instrumentation and (when enabled) mount /metrics.

    Safe to call once per FastAPI instance. DB instrumentation is *not* done
    here because it must happen after `Context.startup()` has created the
    SQLAlchemy engines — see `instrument_databases`.

    ``mount_metrics=False`` skips the /metrics mount for callers that already
    mounted it ahead of their routers via `mount_metrics_endpoint`.
    """
    instrument_inbound_app(app)

    if mount_metrics:
        mount_metrics_endpoint(app)


def instrument_databases(ctx: Context) -> None:
    """Instrument the SQLAlchemy engines from the live Context.

    Must be called after `ctx.startup()` so engines exist. Idempotent across
    the whole process: the underlying instrumentor is global and only the
    first call wires it up.

    SQLAlchemy is the one DB instrumentor: it traces each statement, with the
    engine's pool context, plus a ``connect`` span per checkout. Transaction
    control (COMMIT/ROLLBACK) and pool pre-ping round trips are not traced —
    SQLAlchemy's tracer hooks only cursor execution, and pre-ping time sits
    inside the ``connect`` span. The asyncpg driver is not instrumented: that
    would add a second span beneath every SQLAlchemy statement span to recover
    those round trips.
    """
    global _db_instrumented
    if _db_instrumented:
        return
    _db_instrumented = True

    # One call for every plane: the instrumentor is a process-wide singleton,
    # so a second ``instrument()`` is ignored and would leave that engine
    # untraced.
    engines = [
        getattr(ctx, f"{name}_db").engine.sync_engine
        for name in ("registry", "admin", "control")
        if ctx.has_db(name)
    ]
    if engines:
        # skip_dep_check keeps DB tracing active when SQLAlchemy 2.1 is installed
        # alongside opentelemetry-instrumentation-sqlalchemy releases whose
        # metadata upper-bounds sqlalchemy at <2.1.0.
        SQLAlchemyInstrumentor().instrument(engines=engines, skip_dep_check=True)


def _start_worker(
    ctx: Context,
    enabled_apps: set[str],
    *,
    upstream_executor: Any | None = None,
    credential_injector: Any | None = None,
    execution_authorizer: Any | None = None,
    actor_status: Any | None = None,
) -> tuple[WorkerLoop, asyncio.Task[None]] | None:
    """Start the background worker if the admin DB is available.

    ``enabled_apps`` is the set of surfaces this process actually serves (from
    ``config.apps``). Handler registration keys off *that*, not merely which DBs
    are reachable: a broker-only process is granted the registry DB so its
    **synchronous** proxy can resolve specs (``SURFACE_DB_DEPS["broker"]``), but
    it is not the registry surface and must not claim ``IMPORT`` jobs — which are
    drawn from the shared jobs table, so a broker worker would otherwise race
    (and win) import jobs against the control plane and run the full ingest
    pipeline (including any registered import-time stages) with the wrong
    service's config.

    ``upstream_executor`` is the broker-side ``UpstreamExecutor`` (the
    ``PipelineExecutor`` over the shared composed runner) and
    ``credential_injector`` is the broker ``CredentialService``; both are built
    by the broker's surface lifespan and stashed on ``app.state`` (so this
    ``shared/`` factory never imports ``broker/``). When the executor is
    provided the execution handler dispatches through the **same** pipeline as
    the sync router (circuit breaker, per-host bulkhead, shared pool, post-
    response enrichment, single ``executions`` persistence) and resolves
    credentials before the call. Without it the execution handler is not
    registered (a surface with no broker has no upstream calls to run).
    ``execution_authorizer`` is the broker's run-time re-authorizer: every
    queued execution is re-checked with the sync route's policy before any
    credential is resolved (the handler refuses a credential injector without
    one). ``actor_status`` is the broker's ``ActorStatusResolver``: the handler
    builds the identity it injects for from a live read of the actor row (and
    likewise refuses an injector without it).

    Returns the ``(worker, task)`` pair so the lifespan can **drain** the worker
    (let the in-flight job finish or be reclaimed) before tearing the shared
    client/runners down — see ``_stop_worker``.
    """
    if not ctx.has_db("admin"):
        return None

    handler_registry = JobHandlerRegistry()

    if "registry" in enabled_apps and ctx.has_db("registry"):
        handler_registry.register(JobKind.IMPORT, ImportHandler(ctx))

    if ctx.has_db("control") and upstream_executor is not None:
        handler_registry.register(
            JobKind.EXECUTION,
            ExecutionHandler(
                executor=upstream_executor,
                upstream_timeout_s=ctx.config.broker.upstream_timeout_s,
                credential_injector=credential_injector,
                execution_authorizer=execution_authorizer,
                actor_status=actor_status,
                egress=ctx.config.broker.egress,
                security_config=ctx.config.security,
                # Same predicate the broker's hold path files under, so a held
                # payload the broker encrypted is one this worker can decrypt.
                encryption=ctx.encryption if ctx.has_encryption_keyset else None,
            ),
        )

    if not handler_registry.kinds:
        return None

    worker = WorkerLoop(
        ctx.admin_db,
        handler_registry,
        worker_config=ctx.config.worker,
        approved_result_retention_seconds=ctx.config.held_executions.result_retention_seconds,
    )
    task = asyncio.create_task(worker.run())
    _logger.info("worker_loop_task_started")
    return worker, task


def _start_expiry_scanner(
    ctx: Context,
    enabled_apps: set[str],
) -> tuple[CredentialExpiryScanner, asyncio.Task[None]] | None:
    """Start the credential-expiry scanner when the control surface runs it.

    The sweep reads OAuth token expiries from the **control** DB and writes
    ``credential.expiring_soon`` / ``credential.expired`` events into the
    **admin** DB, so both must be present. It is a control-plane background job:
    gate it on the ``control`` surface being enabled (``config.apps``), not on
    mere DB reachability — a broker-only process is granted the control DB to
    resolve credentials, but must not run the control plane's expiry sweep.
    """
    if "control" not in enabled_apps:
        return None
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


def _start_catalog_update_scanner(
    ctx: Context,
    enabled_apps: set[str],
) -> tuple[CatalogUpdateScanner, asyncio.Task[None]] | None:
    """Start the Flow-3 catalog update-notify scanner when the registry runs it.

    The sweep reads sweep candidates + check rows from the **registry** DB and emits
    ``catalog.update_available`` events into the **admin** DB, so both must be present.
    Like the import worker it is a registry-surface background job: gate it on the
    ``registry`` surface being enabled (``config.apps``), not on DB reachability, so a
    broker-only process (granted the registry DB for its sync-proxy spec lookups) does
    not run it. A standalone-registry deployment (no admin DB) still gets no scanner —
    there is nowhere to record the notifications.
    """
    if "registry" not in enabled_apps:
        return None
    if not (ctx.has_db("registry") and ctx.has_db("admin")):
        return None
    scanner = CatalogUpdateScanner(ctx)
    task = asyncio.create_task(scanner.run())
    _logger.info("catalog_update_scanner_task_started")
    return scanner, task


async def _stop_catalog_update_scanner(
    handle: tuple[CatalogUpdateScanner, asyncio.Task[None]] | None,
) -> None:
    """Signal and cancel the catalog update-notify scanner (best-effort)."""
    if handle is None:
        return
    scanner, task = handle
    scanner.stop()
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=5.0)


def _start_connect_poll_scanner(
    ctx: Context,
    enabled_apps: set[str],
    *,
    catalog_auto_importer: CatalogAutoImportProtocol | None = None,
) -> tuple[ConnectPollScanner, asyncio.Task[None]] | None:
    """Start the connect-session polling scanner when the control surface runs it.

    Drives the device-flow vendor poll loop server-side so the HTTP
    ``/status`` surface stays read-only. Control-plane background job:
    gate on the ``control`` surface being enabled + control-DB reachability
    (that's where ``connect_sessions`` lives). Broker-only processes with
    control-DB access for credential resolution do not run this scanner.

    ``catalog_auto_importer`` is threaded into the ``ConnectSessionService``
    each tick builds so a scanner-driven device-flow finalise
    (``_finalise_connected`` → ``_maybe_import_catalog``) can enqueue the
    vendor's OpenAPI import. Without this, the request-scoped importer on
    ``app.state`` is invisible to the scanner and every device-flow connect
    silently skips the auto-import.
    """
    if "control" not in enabled_apps:
        return None
    if not ctx.has_db("control"):
        return None
    scanner = ConnectPollScanner(ctx, catalog_auto_importer=catalog_auto_importer)
    task = asyncio.create_task(scanner.run())
    _logger.info("connect_poll_scanner_task_started")
    return scanner, task


async def _stop_connect_poll_scanner(
    handle: tuple[ConnectPollScanner, asyncio.Task[None]] | None,
) -> None:
    """Signal and cancel the connect-poll scanner (best-effort)."""
    if handle is None:
        return
    scanner, task = handle
    scanner.stop()
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=5.0)


async def _stop_worker(handle: tuple[WorkerLoop, asyncio.Task[None]] | None) -> None:
    """Gracefully drain then stop the worker (teardown step 2).

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
        # The OS family rides on every boot event (see HostOs) so the
        # dimension self-heals: a lost POST or a config moved to another
        # machine is corrected on the next startup, matching how comparable
        # products (n8n, GitLab, Grafana) report environment facts. Prefer
        # the install-time value the CLI stamped on the host; in Docker,
        # runtime detection would report the container's Linux.
        await emit_event_best_effort(
            session,
            type=EventType.INSTANCE_BOOTED,
            severity=EventSeverity.INFO,
            summary="Instance booted",
            created_by=None,
            tags={HostOs.resolve(cfg.host_os)},
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


async def _run_shutdown_handlers(app: FastAPI) -> None:
    """Run the handlers registered with ``app.router.add_event_handler("shutdown", …)``.

    FastAPI runs an app's own shutdown handlers only from its default lifespan,
    and every app built here has its own ``lifespan``, so the factory's
    lifespans run them through this helper, for installers (container
    ``extra_installers`` included) that register one to close what they built.
    The process's shared-state backend is not closed here; the lifespan closes
    it last (``ctx.close_state_backend()``). Register on ``app.router`` itself:
    an included router's handlers already run through that router's lifespan,
    which FastAPI merges into the app's, and are copied onto ``app.router``
    too, so they would run twice. Best-effort: a failing handler is logged and
    the rest still run.
    """
    for handler in list(app.router.on_shutdown):
        try:
            result = handler()
            if inspect.isawaitable(result):
                await result
        except Exception:
            _logger.warning(
                "shutdown_handler_failed",
                handler=getattr(handler, "__qualname__", repr(handler)),
                exc_info=True,
            )


def create_surface_app(
    ctx: Context,
    *,
    title: str,
    routers: Sequence[tuple[APIRouter, str, list[str]]],
    enabled_apps: set[str],
    extra_lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
    container: AppContainer | None = None,
    include_instance_router: bool = True,
) -> FastAPI:
    """Create a standalone surface FastAPI app with observability wired.

    Routers are mounted without their prefix (standalone mode serves at root,
    e.g. /health rather than /broker/health — the combined-mode root app
    keeps the prefix so /broker/health, /admin/health, etc. don't collide).

    ``enabled_apps`` is the set of surfaces this process serves — for a
    standalone surface it is that one surface's name. It gates the shared
    background jobs (import worker, catalog + expiry scanners) so they run only
    for the surface that owns them, independent of which DBs happen to be
    reachable (the broker is granted the registry/control DBs for its proxy +
    credential paths, but must not run registry/control background work).

    ``extra_lifespan`` is an optional surface-owned async context manager entered
    after ``ctx.startup()`` and exited before ``ctx.shutdown()`` — the broker
    uses it to open/close its shared outbound ``httpx.AsyncClient``.

    ``container`` is the DI seam: when omitted the default is used and behavior is
    unchanged. A caller passes its own container to inject a ``Broker`` (stashed on
    ``app.state``) and mount extra routers after the surface's own.

    ``include_instance_router`` mounts the public backend-identity endpoint
    (``GET /instance``). It defaults to ``True`` for control-plane surfaces; the
    broker (a data-plane forward proxy whose only public routes are its
    liveness/readiness probes) passes ``False`` so it never advertises a
    control-plane identity surface.
    """
    container = container or AppContainer.default(ctx)
    # The broker verifies callers with its own data-plane validator; every other
    # surface needs the admin DB for the platform verifier, so a misconfigured
    # context fails here rather than 500ing every authenticated request.
    if "broker" not in enabled_apps:
        _require_admin_db(ctx, f"control-plane surface {sorted(enabled_apps)}")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # The process's shared-state backend closes last, after the surface's
        # own extra_lifespan (the broker's runners) has exited.
        try:
            async with _surface_lifespan(app):
                yield
        finally:
            await ctx.close_state_backend()

    @asynccontextmanager
    async def _surface_lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await ctx.startup()
        instrument_databases(ctx)
        telemetry_handle = await _start_telemetry(ctx)
        # Container-injected lifespans enter after the surface's own
        # extra_lifespan (so they may read surface state stashed on app.state)
        # and exit in reverse order before it.
        async with (
            extra_lifespan(app) if extra_lifespan else _null_lifespan(),
            contextlib.AsyncExitStack() as lifespan_stack,
        ):
            for lifespan_factory in container.extra_lifespans:
                await lifespan_stack.enter_async_context(lifespan_factory(app, ctx))
            # Worker starts *inside* the surface lifespan so it can share any
            # surface-owned resource (e.g. the broker's shared upstream
            # executor + credential injector stashed on app.state by
            # extra_lifespan).
            worker_task = _start_worker(
                ctx,
                enabled_apps,
                upstream_executor=getattr(app.state, "broker_upstream_executor", None),
                credential_injector=getattr(app.state, "broker_credential_injector", None),
                execution_authorizer=getattr(app.state, "broker_execution_authorizer", None),
                actor_status=getattr(app.state, "broker_actor_status", None),
            )
            scanner_task = _start_expiry_scanner(ctx, enabled_apps)
            catalog_scanner_task = _start_catalog_update_scanner(ctx, enabled_apps)
            connect_poll_task = _start_connect_poll_scanner(
                ctx,
                enabled_apps,
                catalog_auto_importer=getattr(app.state, "catalog_auto_importer", None),
            )
            try:
                yield
            finally:
                # Drain step 1: signal the admission gate (if any) to
                # report unready + stamp Connection: close, so the LB deregisters
                # this instance *before* we drain in-flight work and tear down the
                # worker (step 2) and — in extra_lifespan's exit — the shared
                # client/runners (step 4). Strict order: unready → worker → client.
                gate = getattr(app.state, "broker_admission_gate", None)
                if gate is not None and hasattr(gate, "start_draining"):
                    gate.start_draining()
                await _stop_connect_poll_scanner(connect_poll_task)
                await _stop_catalog_update_scanner(catalog_scanner_task)
                await _stop_expiry_scanner(scanner_task)
                await _stop_worker(worker_task)
                # Work requests handed off after their response (it may write to
                # the databases) drains before ctx.shutdown() closes them.
                await background_tasks.drain(ctx.config.services.background_task_drain_timeout_s)
                await _stop_telemetry(telemetry_handle)
                await _run_shutdown_handlers(app)
                await ctx.shutdown()

    # ``server.public_base_url`` names the control/auth surfaces' origin; a
    # standalone broker runs on its own, so it keeps the same-origin server.
    public_base_url = "" if "broker" in enabled_apps else ctx.config.server.public_base_url
    meta = fastapi_metadata_kwargs(public_base_url)
    meta["title"] = title
    app = FastAPI(lifespan=lifespan, **meta)
    app.state.ctx = ctx
    background_tasks = install_background_tasks(app)
    if container.broker is not None:
        # Wire the injected broker into BOTH data-plane paths: the sync router
        # reads app.state.broker, while the async worker builds its broker via
        # app.state.broker_factory(runner). Without the factory the async path
        # silently falls back to the default broker.
        app.state.broker = container.broker
        app.state.broker_factory = lambda _runner: container.broker
    if container.unregistered_url_handler is not None:
        # Unregistered-URL hook (sync web edge only) — the router reads it via
        # getattr, so leaving it unset preserves today's 404 exactly. Only the
        # broker catch-all reads it, so on any other surface the handler can
        # never fire — say so instead of failing silent.
        app.state.unregistered_url_handler = container.unregistered_url_handler
        handler_name = (
            f"{type(container.unregistered_url_handler).__module__}."
            f"{type(container.unregistered_url_handler).__qualname__}"
        )
        if "broker" in enabled_apps:
            _logger.info("unregistered_url_handler_installed", handler=handler_name)
        else:
            _logger.warning(
                "unregistered_url_handler_unreachable",
                handler=handler_name,
                reason="only the broker catch-all reads this hook and this app has no broker",
            )
    # Public, schema-hidden agent-discovery documents (the skill set +
    # llms.txt). Mounted on every standalone surface so split deployments
    # (gateway proxying to per-surface backends) serve them too. Registered
    # *before* the surface routers and the container extension seam so neither
    # the broker's /{upstream_url:path} catch-all nor an injected extra router
    # can shadow the discovery paths (the /skills/* routes and llms.txt).
    app.include_router(get_agent_discovery_router())
    # Prometheus scrape endpoint (when enabled), mounted ahead of the surface
    # routers for the same reason: the broker catch-all must not shadow it.
    mount_metrics_endpoint(app)
    # A surface router carrying a root /{name:path} catch-all (the broker's
    # forward proxy) is held back and included last — after the container
    # extension seam — so it shadows neither an injected extra router nor an
    # installer-added route. Every other surface router still precedes the
    # extensions, so an extension never shadows a built-in route.
    catch_all_routers: list[tuple[APIRouter, Sequence[str]]] = []
    for router, _prefix, tags in routers:
        if _has_root_catch_all(router):
            catch_all_routers.append((router, tags))
            continue
        app.include_router(router, tags=list(tags), responses=COMMON_ERROR_RESPONSES)
    # Public, schema-visible backend-identity endpoint so a client (MCP server,
    # CLI, agent) can tell which backend it is bound to — cloud vs. local. Off
    # for the broker data plane (its only public routes are its probes).
    if include_instance_router:
        # No COMMON_ERROR_RESPONSES: a parameterless public GET can't produce
        # 400/422 (same posture as /health).
        app.include_router(get_instance_router())
        # Running/latest version so the signed-in SPA can show the current
        # version and an update banner. Authenticated (any valid session; not
        # published unauthenticated). The latest release is resolved server-side
        # from GitHub (cached, best-effort), degrading to latest=null on failure.
        app.include_router(get_system_router())
    # The platform identity verifier — the same chain create_combined_app
    # installs, so a standalone surface accepts every token shape the combined
    # app does (notably opaque ``at_`` OAuth access tokens). Installed before the
    # container extensions, and no surface hook writes it afterwards, so an
    # injected installer can replace it. The broker installs its own data-plane
    # validator (broker.core.setup).
    if "broker" not in enabled_apps:
        install_platform_token_verifier(app, ctx)
    _include_extra_routers(app, container.extra_routers)
    for installer in container.extra_installers:
        installer(app, ctx)
    for catch_all_router, catch_all_tags in catch_all_routers:
        app.include_router(
            catch_all_router, tags=list(catch_all_tags), responses=COMMON_ERROR_RESPONSES
        )
    # Authenticated JSON is caller-specific: keep it out of shared caches (#1835).
    app.add_middleware(AuthenticatedResponseCacheMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(ProblemDetailException, spa_aware_problem_detail_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StateBackendCapacityError, state_backend_capacity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    attach_http_observability(app, mount_metrics=False)
    install_openapi_metadata(app)
    return app


async def _root_health() -> RootHealthResponse:
    """Liveness probe for the combined control-plane app.

    Unauthenticated and dependency-free so orchestrators and load balancers
    have a stable target. Returns ``{"status": "ok"}`` when the process is up.
    """
    return RootHealthResponse(status="ok", version=__version__)


def _include_control_plane_routes(
    app: FastAPI,
    apps: Sequence[str],
    *,
    ctx: Context,
    extra_routers: Sequence[RouterSpec] = (),
    prebuilt_routers: Sequence[APIRouter] = (),
) -> None:
    """Mount the combined control-plane route table on ``app``.

    Shared by :func:`create_combined_app` and
    :func:`build_control_plane_reference_app` so the live endpoint reference a
    standalone surface serves documents exactly the routes a combined app
    mounts. Routes only — exception handlers, verifiers and the extension
    routers themselves are the caller's. ``ctx`` is the serving process's
    context, for route factories that read deployment config.
    ``extra_routers`` are the container routers the caller mounts after this
    table; a partial app's endpoint reference documents them too.

    The reference app re-runs this helper lazily, on the first unauthenticated
    request for the reference, with every control-plane surface as ``apps``. So
    every route factory called here must be config-only and side-effect-free. A
    router whose factory resolves state at build (runs contributors, logs, may
    raise) is built once by the serving app and passed in ``prebuilt_routers``:
    it is mounted after the instance router, and the reference app reuses the
    same router objects instead of calling the factory again.
    """
    app.get(
        "/health",
        operation_id="getHealth",
        summary="Health",
        tags=["System"],
        # Keep the wire body exactly ``{"status", "version"}`` — the
        # lockout-only ``reason`` member is never emitted as null.
        response_model_exclude_none=True,
    )(_root_health)

    # Public, schema-hidden agent-discovery documents: the skill set
    # (GET /skills/{name}.md, the GET /SKILL.md alias, GET /skills/index.json)
    # and llms.txt (GET /llms.txt, GET /.well-known/llms.txt) — see #651 / #809.
    # Registered before the surfaces so no surface route can shadow them (same
    # order as create_surface_app).
    app.include_router(get_agent_discovery_router())

    for surface in apps:
        mod = importlib.import_module(SURFACE_MODULES[surface])
        for router, prefix, tags in mod.get_routers():
            app.include_router(router, prefix=prefix, tags=tags, responses=COMMON_ERROR_RESPONSES)

    # Public, schema-hidden endpoint reference (the CLI + docs SPA read this
    # instead of parsing the OpenAPI document). A full control-plane app
    # introspects itself, so every route it mounts (extensions included) is
    # listed. A process serving only part of the control plane (a split
    # deploy's admin+auth pod) documents the whole control plane plus its own
    # extension routers instead of just its own routes.
    if set(CONTROL_PLANE_SURFACES) <= set(apps):
        app.include_router(get_reference_router())
    else:
        app.include_router(
            get_control_plane_reference_router(
                ctx, extra_routers=extra_routers, prebuilt_routers=prebuilt_routers
            )
        )

    # Public backend-identity endpoint so a client (MCP server, CLI, agent) can
    # tell which backend it is bound to — cloud vs. a local self-hosted install.
    # No COMMON_ERROR_RESPONSES: a parameterless public GET can't produce
    # 400/422 (same posture as /health).
    app.include_router(get_instance_router())

    for router in prebuilt_routers:
        app.include_router(router, responses=COMMON_ERROR_RESPONSES)

    # Running/latest version so the signed-in SPA can show the current version
    # and an update banner. Authenticated (any valid session). The latest release
    # is resolved server-side from GitHub (cached, best-effort), degrading to
    # latest=null on failure.
    app.include_router(get_system_router())


def _include_extra_routers(app: FastAPI, extra_routers: Sequence[RouterSpec]) -> None:
    """Mount container-injected routers (append-only; never shadow a built-in)."""
    for router, prefix, tags in extra_routers:
        app.include_router(router, prefix=prefix, tags=list(tags), responses=COMMON_ERROR_RESPONSES)


def build_control_plane_reference_app(
    ctx: Context,
    *,
    extra_routers: Sequence[RouterSpec] = (),
    prebuilt_routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """A route-only app spanning every control-plane surface, for introspection.

    Never served: it has no lifespan, verifier or middleware, and never touches
    a database. A partial control-plane process's ``GET
    /reference/endpoints.json`` introspects it instead of the serving app,
    because a split deployment presents the whole control-plane API (behind one
    gateway) even though each process mounts only its own surface's routes.
    ``extra_routers`` (the serving process's container routers) are mounted
    after the built-in table, in the same order the combined app uses.
    ``prebuilt_routers`` are the serving app's already-built router objects
    (see :func:`_include_control_plane_routes`); they are reused, never rebuilt.
    """
    app = FastAPI(**fastapi_metadata_kwargs(ctx.config.server.public_base_url))
    _include_control_plane_routes(
        app, CONTROL_PLANE_SURFACES, ctx=ctx, prebuilt_routers=prebuilt_routers
    )
    _include_extra_routers(app, extra_routers)
    install_openapi_metadata(app)
    return app


def get_control_plane_reference_router(
    ctx: Context,
    *,
    extra_routers: Sequence[RouterSpec] = (),
    prebuilt_routers: Sequence[APIRouter] = (),
) -> APIRouter:
    """``GET /reference/endpoints.json`` for a partial control-plane process.

    Documents every control-plane surface (:data:`CONTROL_PLANE_SURFACES`) —
    the same payload the combined app and the committed
    ``docs/reference/endpoints.json`` carry — plus the process's own
    ``extra_routers``, not just the serving process's surface routes.
    ``prebuilt_routers`` are the serving app's already-built router objects; the
    cold reference build reuses them, so it never re-runs their factories.
    """
    return get_reference_router(
        source_app=functools.partial(
            build_control_plane_reference_app,
            ctx,
            extra_routers=tuple(extra_routers),
            prebuilt_routers=tuple(prebuilt_routers),
        )
    )


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
    _require_admin_db(ctx, f"combined control-plane app {apps}")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        try:
            async with _combined_lifespan(app):
                yield
        finally:
            # The process's shared-state backend closes once, last.
            await ctx.close_state_backend()

    @asynccontextmanager
    async def _combined_lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await ctx.startup()
        instrument_databases(ctx)
        telemetry_handle = await _start_telemetry(ctx)
        # Container-injected lifespans (default empty) — entered after
        # ctx.startup(), exited in reverse order before ctx.shutdown().
        async with contextlib.AsyncExitStack() as lifespan_stack:
            for lifespan_factory in container.extra_lifespans:
                await lifespan_stack.enter_async_context(lifespan_factory(app, ctx))
            worker_task = _start_worker(ctx, set(apps))
            scanner_task = _start_expiry_scanner(ctx, set(apps))
            catalog_scanner_task = _start_catalog_update_scanner(ctx, set(apps))
            connect_poll_task = _start_connect_poll_scanner(
                ctx,
                set(apps),
                catalog_auto_importer=getattr(app.state, "catalog_auto_importer", None),
            )
            try:
                yield
            finally:
                await _stop_connect_poll_scanner(connect_poll_task)
                await _stop_catalog_update_scanner(catalog_scanner_task)
                await _stop_expiry_scanner(scanner_task)
                await _stop_worker(worker_task)
                await background_tasks.drain(ctx.config.services.background_task_drain_timeout_s)
                await _stop_telemetry(telemetry_handle)
                await _run_shutdown_handlers(app)
                await ctx.shutdown()

    root = FastAPI(lifespan=lifespan, **fastapi_metadata_kwargs(ctx.config.server.public_base_url))
    root.state.ctx = ctx
    background_tasks = install_background_tasks(root)
    # Injected Broker (None by default → broker surface builds its default
    # per request). Wire BOTH data-plane paths: the sync router reads
    # root.state.broker; the async worker builds its broker via
    # root.state.broker_factory(runner). Without the factory the async path
    # silently falls back to the default broker.
    if container.broker is not None:
        root.state.broker = container.broker
        root.state.broker_factory = lambda _runner: container.broker
    if container.unregistered_url_handler is not None:
        # Unregistered-URL hook (sync web edge only) — read via getattr in the
        # broker router, so leaving it unset preserves today's 404 exactly. The
        # broker never rides the combined app (``__main__`` guards it), so a
        # handler set here can never fire — warn instead of failing silent.
        root.state.unregistered_url_handler = container.unregistered_url_handler
        _logger.warning(
            "unregistered_url_handler_unreachable",
            handler=f"{type(container.unregistered_url_handler).__module__}."
            f"{type(container.unregistered_url_handler).__qualname__}",
            reason="only the broker catch-all reads this hook and the combined app has no broker",
        )
    root.add_exception_handler(ProblemDetailException, spa_aware_problem_detail_handler)  # type: ignore[arg-type]
    root.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
    root.add_exception_handler(StateBackendCapacityError, state_backend_capacity_error_handler)  # type: ignore[arg-type]
    root.add_exception_handler(Exception, unhandled_exception_handler)

    # The combined control-plane route table — the same assembly the
    # standalone endpoint reference introspects (see
    # build_control_plane_reference_app), so the two cannot drift.
    _include_control_plane_routes(root, apps, ctx=ctx, extra_routers=container.extra_routers)

    for surface in apps:
        mod = importlib.import_module(SURFACE_MODULES[surface])
        if hasattr(mod, "get_exception_handlers"):
            for exc_class, handler in mod.get_exception_handlers():
                root.add_exception_handler(exc_class, handler)
        if hasattr(mod, "install_on_app"):
            mod.install_on_app(root, ctx)

    # Deterministic identity verifier: the platform chain (API keys + opaque
    # ``at_`` + HS256) is installed after every surface ran ``install_on_app``
    # (no surface hook writes it), so the active verifier never depends on
    # surface ordering or on whether the auth surface rides this process.
    # Standalone surfaces install the same chain in create_surface_app.
    install_platform_token_verifier(root, ctx)

    # Extension point: injected routers/installers mount after all built-in
    # surfaces (append-only; never shadows a built-in route). No-op by default.
    _include_extra_routers(root, container.extra_routers)
    for installer in container.extra_installers:
        installer(root, ctx)

    # Authenticated JSON is caller-specific: keep it out of shared caches (#1835).
    root.add_middleware(AuthenticatedResponseCacheMiddleware)
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
