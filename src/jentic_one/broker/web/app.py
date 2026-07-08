"""Broker application factory — standalone (separate-service) only.

The broker runs as its own service; it is never bundled into the combined app
(``__main__`` guards against it). Infra routes (``/health``, ``/docs``,
``/openapi.json``, ``/metrics``) are registered **before** the catch-all so the
catch-all never shadows them.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from opentelemetry.metrics import CallbackOptions, Observation

from jentic_one.broker.adapters.http_client import HttpClientProvider
from jentic_one.broker.adapters.runners.circuit import CircuitBreakerRunner
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.execution.executor import PipelineExecutor
from jentic_one.broker.services.execution.pipeline import build_runner
from jentic_one.broker.services.idempotency import SharedStateIdempotencyStore
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.middleware import AdmissionControlMiddleware, _AdmissionGate
from jentic_one.broker.web.readiness import make_readiness_router
from jentic_one.broker.web.routers import execute
from jentic_one.shared.context import Context
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.resilience import CircuitBreaker, RateLimiter
from jentic_one.shared.state import build_state_backend
from jentic_one.shared.tracing import instrument_outbound_client
from jentic_one.shared.web.app_factory import create_surface_app
from jentic_one.shared.web.health import make_health_router


def _routers(*, readiness_saturation_threshold: float) -> list[tuple[APIRouter, str, list[str]]]:
    # Order matters: the health router (and the /metrics mount + /docs added by
    # create_surface_app) must be registered before the catch-all so a request to
    # /health is never swallowed by /{upstream_url:path}.
    return [
        (make_health_router("broker"), "", ["broker"]),
        (
            make_readiness_router(saturation_threshold=readiness_saturation_threshold),
            "",
            ["broker"],
        ),
        (execute.router, "", ["broker"]),
    ]


def create_app(ctx: Context) -> FastAPI:
    """Create the broker FastAPI application for standalone deployment."""
    resilience = ctx.config.broker.resilience
    upstream_cfg = resilience.upstream
    meter = get_meter("broker")

    @asynccontextmanager
    async def broker_lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # The single shared bounded outbound client lives for the process; the
        # runner (with its per-host bulkhead) wraps it and is the one both the
        # sync handler (via get_http_runner) and worker share (§04).
        provider = HttpClientProvider(upstream_cfg, ctx.config.broker.egress)
        app.state.broker_http_client = provider.client

        # Distributed tracing continues *into* the upstream: instrument the shared
        # client so every outbound request carries W3C traceparent/tracestate
        # (with span redaction owned by the facade). Per-client, on the live pool.
        instrument_outbound_client(provider.client)

        # In-flight gauge (§05 R5.3) reads the shared admission gate every scrape.
        def _observe_in_flight(_options: CallbackOptions) -> list[Observation]:
            gate = getattr(app.state, "broker_admission_gate", None)
            return [Observation(gate.in_flight if gate is not None else 0)]

        meter.create_observable_gauge(
            "broker.admission.in_flight",
            callbacks=[_observe_in_flight],
            description="Requests currently in flight through the admission gate.",
        )

        # One shared-state backend per process powers both the rate limiter and
        # the circuit breaker (memory default; Redis ⇒ cluster-wide, §06).
        state = build_state_backend(resilience.backend)
        app.state.broker_state_backend = state

        rl = resilience.rate_limit
        app.state.broker_rate_limiter = (
            RateLimiter(state, default_rpm=rl.default_rpm, burst=rl.burst) if rl.enabled else None
        )

        # Idempotency-Key replay store over the same shared backend (§07). When
        # disabled the provider yields None and the handler skips claim/replay.
        idem = ctx.config.broker.idempotency
        app.state.broker_idempotency_store = (
            SharedStateIdempotencyStore(
                state,
                pending_ttl_s=idem.pending_ttl_s,
                done_ttl_s=idem.ttl_s,
                max_response_bytes=idem.max_response_bytes,
            )
            if idem.enabled
            else None
        )

        http_runner = HttpRunner(
            provider.client,
            max_per_host=upstream_cfg.max_per_host,
            max_response_bytes=upstream_cfg.max_response_bytes,
        )
        # Capability profile of the underlying transport — the breaker/deadline
        # wrappers don't declare capabilities, so capture it from the HTTP runner
        # itself and hand it to build_runner for capability-gating (§11 RN-0.3).
        runner_caps = http_runner.capabilities()
        base_runner: HttpRunner | CircuitBreakerRunner = http_runner
        cb = resilience.circuit_breaker
        if cb.enabled:
            breaker = CircuitBreaker(
                state,
                failure_ratio=cb.failure_ratio,
                min_calls=cb.min_calls,
                window_s=cb.window_s,
                cooldown_s=cb.cooldown_s,
            )
            base_runner = CircuitBreakerRunner(
                base_runner, breaker, enforcement_mode=cb.enforcement_mode
            )
        # §11 — wrap the (optionally breaker-decorated) transport with the
        # always-on execution envelope (overall deadline, outermost) plus the
        # capability-gated layers (retry today) the transport supports. Stored as
        # the single composed runner both callers use.
        composed_runner = build_runner(
            base_runner,
            deadline_s=resilience.request_deadline_s,
            retry=resilience.retry,
            caps=runner_caps,
        )

        # §11 RN-0.3 — the RunnerRegistry is the scheme→runner selection seam and
        # owns runner lifecycle. Today only the HTTP runner is registered (for
        # http/https, required: the broker has no job without it); non-HTTP
        # runners register here as they land. Both the sync handler (via
        # get_upstream_runner) and the async worker (via PipelineExecutor) select
        # through this registry so neither hard-codes "the HTTP runner".
        registry = RunnerRegistry()
        registry.register(["http", "https"], composed_runner, required=True)
        await registry.startup()
        app.state.broker_runner_registry = registry

        # §11 RN-0.3 — "one pipeline, two callers". The async worker (started by
        # create_surface_app's lifespan *after* this block) dispatches through
        # the SAME composed runner as the sync router via this executor, and
        # resolves credentials with the same CredentialService. Both are stashed
        # on app.state so the shared/ worker factory wires them without importing
        # broker/ (the arch boundary). Built here so they wrap the live runner.
        #
        # Seam symmetry ("one pipeline, two callers"): if a caller set a
        # `broker_factory` on app.state (the same factory the sync router honors
        # in web/routers/execute.py), the worker's executor uses it too — so an
        # injected Broker reaches BOTH the sync and async paths, not just sync.
        # Unset by default → PipelineExecutor falls back to `default_broker`.
        injected_broker_factory = getattr(app.state, "broker_factory", None)
        app.state.broker_upstream_executor = (
            PipelineExecutor(registry, broker_factory=injected_broker_factory)
            if injected_broker_factory is not None
            else PipelineExecutor(registry)
        )
        app.state.broker_credential_injector = CredentialService(ctx)
        try:
            yield
        finally:
            # §09 E4.3 drain step 4 — close the shared client/runners and state
            # backend LAST, after the surface lifespan has already flipped the
            # gate unready (step 1) and drained the worker (step 2), so no
            # in-flight job hits a closed pool (PoolClosed). The registry drains
            # every runner; the provider closes the shared HTTP pool the HTTP
            # runner borrows (its lifecycle is still provider-owned today).
            await registry.aclose()
            await provider.aclose()
            await state.aclose()

    app = create_surface_app(
        ctx,
        title="jentic-one-broker",
        routers=_routers(readiness_saturation_threshold=resilience.readiness_saturation_threshold),
        extra_lifespan=broker_lifespan,
    )
    # One admission gate shared by the shedding middleware and the readiness
    # probe (§05 R5.2), so both observe the same in-flight counter.
    gate = _AdmissionGate(max_in_flight=resilience.max_in_flight)
    app.state.broker_admission_gate = gate
    app.add_middleware(
        AdmissionControlMiddleware,
        max_in_flight=resilience.max_in_flight,
        retry_after_s=resilience.shed_retry_after_s,
        gate=gate,
    )
    install_broker_auth(app, ctx)
    install_broker_error_handlers(app)
    return app
