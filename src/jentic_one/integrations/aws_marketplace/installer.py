"""Wiring for the entitlement gate: the installer + the lifespan.

Two seams, matching the ``AppContainer`` contract:

- :func:`install_entitlement_gate` (an ``Installer``) runs synchronously at
  app **build** time: mounts the middleware and stashes the gate on
  ``app.state``.
- :func:`entitlement_lifespan` (a ``LifespanFactory``) runs the async parts:
  the blocking startup check (a NOT_ENTITLED deployment is locked from the
  first request) and the periodic refresher that flips both lockout **and
  recovery** without a restart.

Both are activated by ``AppContainer.default`` only when
``entitlement.enabled`` — every other deployment never touches this module.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from jentic_one.integrations.aws_marketplace.checker import EntitlementChecker
from jentic_one.integrations.aws_marketplace.client import LicenseVerdict, build_license_client
from jentic_one.integrations.aws_marketplace.gate import EntitlementGate, EntitlementMiddleware
from jentic_one.shared.context import Context

_log = structlog.get_logger(__name__)

_LOCKOUT_REASON = (
    "AWS Marketplace subscription is not active for this deployment; "
    "renew the subscription to restore service"
)


def install_entitlement_gate(app: FastAPI, ctx: Context) -> None:
    """Mount the gate middleware (build-time seam; state starts unlocked)."""
    gate = EntitlementGate()
    app.state.entitlement_gate = gate
    app.add_middleware(EntitlementMiddleware, gate=gate)


@asynccontextmanager
async def entitlement_lifespan(app: FastAPI, ctx: Context) -> AsyncIterator[None]:
    """Startup check + periodic re-check driving the gate.

    Tests may pre-set ``app.state.entitlement_checker`` (e.g. with a stubbed
    license client) before startup; otherwise the checker and its HTTP client
    are built here from config and torn down on exit.
    """
    gate: EntitlementGate = app.state.entitlement_gate
    owned_http: httpx.AsyncClient | None = None
    checker: EntitlementChecker | None = getattr(app.state, "entitlement_checker", None)
    if checker is None:
        owned_http = httpx.AsyncClient()
        checker = EntitlementChecker(
            ctx.config, client=build_license_client(ctx.config.entitlement, owned_http)
        )
        app.state.entitlement_checker = checker

    _apply(gate, await checker.current_verdict())
    refresh_task = asyncio.create_task(
        _refresh_loop(checker, gate, ctx.config.entitlement.refresh_interval_seconds),
        name="entitlement-refresh",
    )
    try:
        yield
    finally:
        refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task
        if owned_http is not None:
            await owned_http.aclose()


def _apply(gate: EntitlementGate, verdict: LicenseVerdict) -> None:
    if verdict is LicenseVerdict.ENTITLED:
        if gate.locked_out:
            _log.info("entitlement.gate_unlocked")
        gate.unlock()
    else:
        if not gate.locked_out:
            _log.warning("entitlement.gate_locked", verdict=verdict.value)
        gate.lock(_LOCKOUT_REASON)


async def _refresh_loop(
    checker: EntitlementChecker, gate: EntitlementGate, interval_seconds: int
) -> None:
    """Re-check on the refresh cadence; flips lockout and recovery alike.

    The checker never raises (AWS failures degrade to grace/UNKNOWN inside
    it), so this loop only ends by cancellation at shutdown.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        _apply(gate, await checker.current_verdict())
