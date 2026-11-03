"""Default ``Cache-Control`` for authenticated JSON responses (#1835).

A JSON body returned to a credentialed request is built for that caller. With
no ``Cache-Control`` directive, a shared cache or proxy in front of a
deployment may store it under the URL and serve it to a different caller.
:class:`AuthenticatedResponseCacheMiddleware` closes that gap by marking such
responses ``private, no-store``.

Scope and limits:

* "Authenticated" is decided by the *request*: it carries a credential header
  (:func:`request_carries_credential`). A credentialed request to a public
  route (health, OpenAPI document) is therefore marked too — stricter than
  needed, and harmless.
* Only JSON and ``+json`` bodies are marked. Other control-plane bodies (for
  example the registry's markdown or YAML inspect views) are outside this
  default; a route returning caller-specific non-JSON sets its own directive.
  Broker passthrough responses carry their own ``private, no-store`` (see
  :mod:`jentic_one.broker.core.proxy_headers`).
* Responses under the SPA mount keep the bundle's cache policy
  (:class:`jentic_one.shared.web.static.SPAResponseHeadersMiddleware`), even
  for a bundled ``.json`` file fetched with a bearer token.
* A 500 from :func:`jentic_one.shared.web.errors.unhandled_exception_handler`
  is answered outside the user middleware stack, so that handler applies the
  same directive itself.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jentic_one.shared.web.auth import API_KEY_HEADER
from jentic_one.shared.web.static import is_spa_path

#: Directive stamped on authenticated JSON responses: no shared cache may store
#: the body, and no cache (private ones included) keeps a copy.
PRIVATE_NO_STORE = "private, no-store"

_PRIVATE_NO_STORE_BYTES = PRIVATE_NO_STORE.encode("latin-1")
_CREDENTIAL_HEADERS = frozenset({b"authorization", API_KEY_HEADER.encode("latin-1")})


def request_carries_credential(headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """Whether raw ASGI request ``headers`` present a platform credential header.

    Checks the request headers the platform reads a credential from
    (``Authorization`` and ``x-jentic-api-key``, see
    :func:`jentic_one.shared.web.auth.extract_credential`) for a non-blank
    value, so it runs ahead of routing and costs no identity resolution. Routes
    that take a token in the query string (the OAuth approval and consent
    status polls' ``?st=``, ``/login?ls=``) set their own ``no-store``.
    """
    return any(name.lower() in _CREDENTIAL_HEADERS and value.strip() for name, value in headers)


def _is_json(content_type: bytes) -> bool:
    """Whether a ``Content-Type`` value is JSON or a ``+json`` suffix type."""
    media_type = content_type.split(b";", 1)[0].strip().lower()
    return media_type == b"application/json" or media_type.endswith(b"+json")


class AuthenticatedResponseCacheMiddleware:
    """Stamp ``Cache-Control: private, no-store`` on authenticated JSON responses.

    Applies to every response to a request carrying ``Authorization`` or
    ``x-jentic-api-key`` whose content type is ``application/json`` or a
    ``+json`` type (``application/problem+json`` included), so error bodies are
    covered as well as successes.

    A ``Cache-Control`` already set by a route is never overwritten: the SSE
    stream's ``no-cache`` and the token/flow responses' ``no-store`` are
    deliberate. Non-JSON responses, anonymous requests and every path under
    the SPA mount pass through untouched, so public and SPA caching policy is
    unaffected.

    Implemented as **pure ASGI middleware** for the same reason as
    :class:`jentic_one.shared.web.static.SPAResponseHeadersMiddleware`: it
    rewrites ``http.response.start`` without wrapping the downstream app in a
    cancel scope (#627).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or is_spa_path(scope.get("path", ""))
            or not request_carries_credential(scope.get("headers", []))
        ):
            await self._app(scope, receive, send)
            return
        await self._app(scope, receive, _with_cache_control(send))


def _with_cache_control(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            content_type = b""
            has_cache_control = False
            for name, value in headers:
                lowered = name.lower()
                if lowered == b"content-type":
                    content_type = value
                elif lowered == b"cache-control":
                    has_cache_control = True
            if not has_cache_control and _is_json(content_type):
                headers.append((b"cache-control", _PRIVATE_NO_STORE_BYTES))
                message = {**message, "headers": headers}
        await send(message)

    return wrapped
