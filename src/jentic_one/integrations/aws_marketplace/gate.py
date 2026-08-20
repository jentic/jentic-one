"""The entitlement gate: lockout state + the enforcing ASGI middleware.

When the checker's effective verdict is NOT_ENTITLED the middleware returns
503 (RFC 9457 problem details) for every request except the probe surface:

- ``/health``, ``/{surface}/health``, ``/ready`` keep answering **200** so
  orchestrators don't flap the pod (the *process* is healthy; the *license*
  is not) — but with ``{"status": "not_entitled", "reason": …}`` so the state
  is discoverable exactly where an operator looks first.
- ``/instance`` passes through untouched (backend identity stays readable).

Pure ASGI (not ``BaseHTTPMiddleware``), matching ``RequestIDMiddleware`` —
same #627 rationale: no anyio task wrapper around the downstream app.
Recovery needs no restart: the refresher flips :class:`EntitlementGate` back
and traffic resumes on the next request.
"""

from __future__ import annotations

import json
import re
from typing import cast

from jentic.problem_details import ServiceUnavailable
from starlette.types import ASGIApp, Receive, Scope, Send

# Liveness/readiness probes: the root and per-surface health routes plus the
# broker's saturation-aware readiness probe. Single path segment before
# /health only — matches how combined mode prefixes surface health routers.
_PROBE_PATH = re.compile(r"^/(?:[^/]+/)?health$|^/ready$")
_IDENTITY_PATH = "/instance"

_PROBLEM_TYPE = "https://jentic.com/problems/not-entitled"


class EntitlementGate:
    """Holds the lockout state; flipped by the entitlement refresher task."""

    def __init__(self) -> None:
        self._locked_out = False
        self._reason = ""

    @property
    def locked_out(self) -> bool:
        return self._locked_out

    @property
    def reason(self) -> str:
        return self._reason

    def lock(self, reason: str) -> None:
        self._locked_out = True
        self._reason = reason

    def unlock(self) -> None:
        self._locked_out = False
        self._reason = ""


class EntitlementMiddleware:
    """Short-circuit requests with 503 problem details while locked out."""

    def __init__(self, app: ASGIApp, *, gate: EntitlementGate) -> None:
        self._app = app
        self._gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._gate.locked_out:
            await self._app(scope, receive, send)
            return

        path = scope["path"]
        if path == _IDENTITY_PATH:
            await self._app(scope, receive, send)
            return
        if _PROBE_PATH.match(path):
            # Probes stay green (the pod is healthy, the license isn't); the
            # body is where the operator learns why everything else is 503.
            await _send_json(
                send,
                status=200,
                media_type="application/json",
                body={"status": "not_entitled", "reason": self._gate.reason},
            )
            return

        problem = ServiceUnavailable(
            detail=self._gate.reason or "AWS Marketplace entitlement check failed",
            type=_PROBLEM_TYPE,
            title="Not entitled",
        )
        # ``ProblemDetailException.detail`` is annotated ``str`` (its
        # ``HTTPException`` heritage) but carries the RFC 9457 members dict.
        body = cast("dict[str, object]", problem.detail)
        await _send_json(
            send,
            status=problem.status_code,
            media_type="application/problem+json",
            body=body,
        )


async def _send_json(send: Send, *, status: int, media_type: str, body: dict[str, object]) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", media_type.encode()),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
