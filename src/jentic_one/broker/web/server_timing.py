"""Opt-in ``Server-Timing`` response header for the broker (#1717).

Installed only when ``broker.server_timing`` is enabled — with the flag off the
middleware is absent and the stage hooks in the execute path reduce to one
context-variable read each.

Per request the middleware activates a fresh
:class:`~jentic_one.broker.core.server_timing.ServerTimingRecorder`, lets the
execute path record its stages, and appends a ``Server-Timing`` field to the
response start. It emits only for callers that passed the execute route's auth
dependency (the ``auth`` stage is recorded on success only): unauthenticated,
unauthorized, and rate-limited responses carry no timing data, so the header is
not a timing oracle for credential probing.

Because the header rides on the response start, it carries only what is known
before the headers are sent. On the buffered path that is every stage; on the
streaming path the body (and its persistence, which runs after the body) is
still to come, so ``upstream`` there is the wait for the upstream response
headers and ``persist`` is absent.

An upstream ``Server-Timing`` header is passed through verbatim (the passthrough
invariant); the broker's entries are appended as an additional field after it,
so a client reading the combined value sees the upstream's metrics first and the
broker's fixed stage names last.
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jentic_one.broker.core.server_timing import (
    SERVER_TIMING_HEADER,
    ServerTimingRecorder,
    ServerTimingStage,
    activate,
    deactivate,
)

_HEADER_NAME = SERVER_TIMING_HEADER.lower().encode("latin-1")

# Infra routes never run the execute auth dependency; skip the recorder there.
_EXCLUDED_PREFIXES = ("/health", "/ready", "/metrics", "/docs", "/openapi.json")


def _is_excluded(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _EXCLUDED_PREFIXES)


class ServerTimingMiddleware:
    """Pure-ASGI middleware that appends the broker's ``Server-Timing`` field."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_excluded(scope.get("path", "")):
            await self._app(scope, receive, send)
            return

        recorder = ServerTimingRecorder()

        async def send_with_timing(message: Message) -> None:
            if message["type"] == "http.response.start" and recorder.has(ServerTimingStage.AUTH):
                recorder.add(
                    ServerTimingStage.TOTAL,
                    (time.perf_counter() - recorder.started_at) * 1000.0,
                )
                headers = list(message.get("headers", []))
                headers.append((_HEADER_NAME, recorder.header_value().encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        token = activate(recorder)
        try:
            await self._app(scope, receive, send_with_timing)
        finally:
            deactivate(token)


__all__ = ["ServerTimingMiddleware"]
