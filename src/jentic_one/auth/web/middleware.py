"""Middleware that rewrites form-encoded OAuth requests to JSON.

RFC 6749 mandates application/x-www-form-urlencoded for token endpoints.
FastAPI's Pydantic body parsing only accepts JSON. This middleware transparently
converts form-encoded bodies on OAuth paths to JSON so the endpoints can remain
simple typed Pydantic models (preserving OpenAPI schema generation).
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_OAUTH_PATHS = frozenset({"/oauth/token", "/oauth/revoke", "/oauth/introspect"})

_FORM_CONTENT_TYPE = b"application/x-www-form-urlencoded"
_JSON_CONTENT_TYPE = b"application/json"


class FormToJsonMiddleware:
    """ASGI middleware that converts form-encoded bodies to JSON on OAuth paths."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in _OAUTH_PATHS:
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_type = headers.get(b"content-type", b"")
        mime = content_type.split(b";")[0].strip().lower()

        if mime != _FORM_CONTENT_TYPE:
            await self._app(scope, receive, send)
            return

        body_parts: list[bytes] = []
        while True:
            message: Message = await receive()
            body_parts.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        raw_body = b"".join(body_parts)
        parsed = parse_qs(raw_body.decode(), keep_blank_values=True)
        data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
        json_body = json.dumps(data).encode()

        new_headers = [
            (k, _JSON_CONTENT_TYPE if k == b"content-type" else v)
            for k, v in scope.get("headers", [])
        ]
        content_length_present = any(k == b"content-length" for k, _ in new_headers)
        if content_length_present:
            new_headers = [
                (k, str(len(json_body)).encode() if k == b"content-length" else v)
                for k, v in new_headers
            ]

        scope["headers"] = new_headers

        body_sent = False

        async def receive_json() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": json_body, "more_body": False}
            return {"type": "http.disconnect"}

        await self._app(scope, receive_json, send)
