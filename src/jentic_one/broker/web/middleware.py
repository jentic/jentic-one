"""Broker admission-control middleware.

Pure-ASGI middleware (**not** ``BaseHTTPMiddleware``): Starlette's
``BaseHTTPMiddleware`` wraps the request in a way that is known to break
downstream ``request.stream()`` / ``request.body()`` consumption — and the
body cap reads ``request.stream()`` in the handler. A plain ASGI middleware
avoids that interaction (and is where the rate limiter will key off the
validated token).

The cap is **per-instance** — it governs this event loop and is never
coordinated across instances (that is rate limiting). Acquire is
**non-blocking**: past ``max_in_flight`` the request is shed immediately with a
``503`` + ``Retry-After`` rather than queued (the whole point is to fail fast).
``/health`` and ``/metrics`` are excluded so infra routes answer even while
shedding.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jentic_one.broker.core.exceptions import ErrorOrigin
from jentic_one.broker.core.headers import JenticHeader

_PROBLEM_JSON = b"application/problem+json"

# Infra routes must answer even when the broker is shedding load.
_EXCLUDED_PREFIXES = ("/health", "/ready", "/metrics")


class _AdmissionGate:
    """Non-blocking per-instance in-flight counter (the admission logic, no HTTP).

    A plain object the middleware calls — it never raises an HTTP/web error
    (that is the edge's concern). ``in_flight`` is tracked for the readiness
    probe and the saturation metric.
    """

    def __init__(self, *, max_in_flight: int) -> None:
        self._max = max_in_flight
        self.in_flight = 0
        # Flipped on SIGTERM drain: readiness reports unready so the
        # LB deregisters this instance, and the middleware stamps Connection:
        # close on responses so keep-alive clients re-resolve to a healthy pod.
        self.draining = False

    def try_acquire(self) -> bool:
        """Reserve a slot without blocking; ``False`` when at capacity."""
        if self.in_flight >= self._max:
            return False
        self.in_flight += 1
        return True

    def release(self) -> None:
        self.in_flight -= 1

    def start_draining(self) -> None:
        """Mark the instance as draining (unready + Connection: close)."""
        self.draining = True

    @property
    def saturation(self) -> float:
        """In-flight as a fraction of capacity (``0.0`` to ``1.0+``) for readiness."""
        if self._max <= 0:
            return 1.0
        return self.in_flight / self._max


class AdmissionControlMiddleware:
    """Sheds ``503`` + ``Retry-After`` past the per-instance in-flight cap."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_in_flight: int,
        retry_after_s: int,
        gate: _AdmissionGate | None = None,
    ) -> None:
        self._app = app
        # The gate may be supplied by the app factory so the readiness probe
        # can observe ``in_flight`` on the very same counter.
        self._gate = gate if gate is not None else _AdmissionGate(max_in_flight=max_in_flight)
        self._retry_after = str(retry_after_s)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_excluded(scope.get("path", "")):
            await self._app(scope, receive, send)
            return

        if not self._gate.try_acquire():
            await self._send_503(send)
            return

        # During drain, stamp ``Connection: close`` so a keep-alive LB/client
        # tears the connection down after this in-flight request and re-resolves
        # to a healthy pod for the next one (readiness alone doesn't close an
        # already-open keep-alive connection).
        out_send = _drain_close_send(send) if self._gate.draining else send
        try:
            await self._app(scope, receive, out_send)
        finally:
            self._gate.release()

    async def _send_503(self, send: Send) -> None:
        body = json.dumps(
            {
                "type": "server_at_capacity",
                "title": "Server at capacity; retry shortly.",
                "status": 503,
                "error_origin": ErrorOrigin.BROKER.value,
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", _PROBLEM_JSON),
                    (b"retry-after", self._retry_after.encode()),
                    (JenticHeader.ERROR_ORIGIN.value.encode(), ErrorOrigin.BROKER.value.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _is_excluded(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _EXCLUDED_PREFIXES)


def _drain_close_send(send: Send) -> Send:
    """Wrap ``send`` to add ``Connection: close`` to the response start (drain)."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            if not any(name.lower() == b"connection" for name, _ in headers):
                headers.append((b"connection", b"close"))
                message = {**message, "headers": headers}
        await send(message)

    return wrapped


__all__ = ["AdmissionControlMiddleware", "_AdmissionGate"]
