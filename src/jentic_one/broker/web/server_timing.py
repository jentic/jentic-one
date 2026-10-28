"""Opt-in ``Server-Timing`` response header for the broker (#1717).

Installed only when ``broker.server_timing`` is enabled — with the flag off the
middleware is absent and the stage hooks in the execute path reduce to one
context-variable read each.

Per request the middleware activates a fresh
:class:`~jentic_one.broker.core.server_timing.ServerTimingRecorder`, lets the
execute path record its stages, and (when ``emit_header`` is set) appends a
``Server-Timing`` field to the response start. ``emit_header=False`` keeps the
recorder for in-process consumers without exposing anything to the caller.

What reaches the caller is gated twice:

- Only callers that passed the execute route's auth dependency get a field
  (``jentic-auth`` is recorded on success only): unauthenticated,
  unauthorized, and rate-limited responses carry no timing data, and a
  load-shed ``503`` is answered by admission control outside this middleware.
- A broker-generated error (status ``>= 400`` with no ``jentic-upstream``
  stage, e.g. ``404 operation_not_found``, a rule-deny ``403``, a ``424``)
  carries stage *names* only, no durations. Those responses are where an
  authenticated caller could probe server-side branches (unknown pin versus
  another caller's draft, cache hit versus miss) and a server-measured duration
  strips the network noise that otherwise hides the difference. Once the
  upstream was called — success, an upstream error status, or a broker
  transport error such as ``504`` — the durations are emitted in full.

Because the header rides on the response start, it carries only what is known
before the headers are sent. On the buffered path that is every stage; on the
streaming path the body (and its persistence, which runs after the body) is
still to come, so ``jentic-upstream`` there is the wait for the upstream
response headers and ``jentic-persist`` is absent.

An upstream ``Server-Timing`` header is passed through (the passthrough
invariant) and the broker's entries are appended as an additional field after
it. Every broker name carries the reserved ``jentic-`` prefix, so the broker's
entries stay distinguishable from the upstream's ``auth``/``total``/``db``
metrics even if an intermediary folds or reorders fields. To keep that prefix
trustworthy, an upstream entry claiming it is dropped from the upstream field
while the broker emits its own; every other upstream entry is forwarded
byte-for-byte, and a field with no reserved entry is left untouched.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jentic_one.broker.core.server_timing import (
    SERVER_TIMING_HEADER,
    SERVER_TIMING_NAMESPACE,
    ServerTimingRecorder,
    ServerTimingStage,
    activate,
    deactivate,
)

_HEADER_NAME = SERVER_TIMING_HEADER.lower().encode("latin-1")
_NAMESPACE = SERVER_TIMING_NAMESPACE.encode("latin-1")


def _split_entries(value: bytes) -> list[bytes]:
    """Split a ``Server-Timing`` field on commas outside quoted strings."""
    entries: list[bytes] = []
    start = 0
    in_quotes = False
    escaped = False
    for i, byte in enumerate(value):
        if escaped:
            escaped = False
        elif in_quotes and byte == 0x5C:  # backslash escapes the next byte
            escaped = True
        elif byte == 0x22:  # double quote
            in_quotes = not in_quotes
        elif byte == 0x2C and not in_quotes:  # comma
            entries.append(value[start:i])
            start = i + 1
    entries.append(value[start:])
    return entries


def _drop_reserved_entries(value: bytes) -> bytes | None:
    """``value`` without entries in the broker's namespace (``None`` if none remain).

    Returns ``value`` itself when it holds no reserved entry, so a field the
    broker does not need to touch is forwarded byte-for-byte.
    """
    entries = _split_entries(value)
    kept = [
        entry
        for entry in entries
        if not entry.split(b";", 1)[0].strip().lower().startswith(_NAMESPACE)
    ]
    if len(kept) == len(entries):
        return value
    joined = b",".join(kept).strip()
    return joined or None


def _without_reserved_upstream_entries(
    headers: list[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    out: list[tuple[bytes, bytes]] = []
    for name, value in headers:
        if name.lower() == _HEADER_NAME:
            filtered = _drop_reserved_entries(value)
            if filtered is None:
                continue
            value = filtered
        out.append((name, value))
    return out


class ServerTimingMiddleware:
    """Pure-ASGI middleware owning the per-request stage recorder."""

    def __init__(self, app: ASGIApp, *, emit_header: bool = True) -> None:
        self._app = app
        self._emit_header = emit_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        recorder = ServerTimingRecorder()
        out_send = self._timed_send(recorder, send) if self._emit_header else send

        token = activate(recorder)
        try:
            await self._app(scope, receive, out_send)
        finally:
            deactivate(token)

    @staticmethod
    def _timed_send(recorder: ServerTimingRecorder, send: Send) -> Send:
        async def send_with_timing(message: Message) -> None:
            if message["type"] == "http.response.start" and recorder.has(ServerTimingStage.AUTH):
                recorder.add(ServerTimingStage.TOTAL, recorder.elapsed_ms())
                broker_error = int(message.get("status", 200)) >= 400 and not recorder.has(
                    ServerTimingStage.UPSTREAM
                )
                value = recorder.header_value(with_durations=not broker_error)
                headers = _without_reserved_upstream_entries(list(message.get("headers", [])))
                headers.append((_HEADER_NAME, value.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        return send_with_timing


__all__ = ["ServerTimingMiddleware"]
