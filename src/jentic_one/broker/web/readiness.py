"""Broker readiness probe — saturation-aware.

Liveness (``/health``, shared router) stays green as long as the process is up;
**readiness** (``/ready``, here) flips to ``503`` once sustained in-flight load
nears the admission cap, so the load balancer sheds *this* instance before it
starts shedding requests itself — without the pod being killed. The probe reads
the very same ``_AdmissionGate`` the admission middleware counts on (shared via
``app.state``), so there is one source of truth for in-flight.

``/ready`` is admission-excluded (see ``middleware._EXCLUDED_PREFIXES``) so it
answers even while the broker is shedding real traffic.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from jentic_one.broker.web.middleware import _AdmissionGate
from jentic_one.shared.metrics import get_meter

# Default fraction of the admission cap at/above which the probe reports
# unready. Below 1.0 so the LB drains this instance *before* it hits the hard
# shed wall. Overridable per-app via ``BrokerResilienceConfig`` (operators tune
# it when the default headroom doesn't fit their traffic shape).
_DEFAULT_READY_SATURATION_THRESHOLD = 0.9

_meter = get_meter("broker")
_not_ready_total = _meter.create_counter(
    "broker.readiness.not_ready_total",
    description="Readiness probes that reported unready due to saturation.",
)


def make_readiness_router(
    *, saturation_threshold: float = _DEFAULT_READY_SATURATION_THRESHOLD
) -> APIRouter:
    """Build the broker ``/ready`` router (separate from shared ``/health``).

    ``saturation_threshold`` is the in-flight fraction of the admission cap at or
    above which ``/ready`` reports unready (default ``0.9``; see
    ``BrokerResilienceConfig.readiness_saturation_threshold``).
    """
    router = APIRouter()

    @router.get(
        "/ready",
        operation_id="brokerReady",
        summary="Broker readiness (saturation-aware)",
        tags=["System"],
    )
    async def ready(request: Request, response: Response) -> dict[str, object]:
        """Report ``503`` under sustained saturation; ``200`` otherwise.

        Unauthenticated, like liveness. The gate is absent only before the
        lifespan wires it (or in minimal test apps) — treat that as ready.
        """
        gate: _AdmissionGate | None = getattr(request.app.state, "broker_admission_gate", None)
        if gate is not None and gate.draining:
            _not_ready_total.add(1)
            response.status_code = 503
            return {"status": "unready", "reason": "draining"}
        saturation = gate.saturation if gate is not None else 0.0
        if saturation >= saturation_threshold:
            _not_ready_total.add(1)
            response.status_code = 503
            return {"status": "unready", "reason": "saturated", "saturation": round(saturation, 3)}
        return {"status": "ready", "saturation": round(saturation, 3)}

    return router


__all__ = ["make_readiness_router"]
