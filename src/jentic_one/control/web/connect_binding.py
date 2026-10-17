"""Browser-binding cookie for the OAuth connect callback.

The routes that hand a browser an authorization URL (``POST
/credentials/{id}/connect`` and ``POST /connect-sessions/{id}:confirm``) mint a
random per-flow value, set it on the calling browser as a short-lived cookie,
and sign its SHA-256 into the connect state. ``GET /credentials/oauth/callback``
completes the flow only when the request carries the matching cookie, so the
callback must land in the browser that started the flow (for an agent's
session, the approver who confirmed it).

The cookie:

* name ``jentic_connect_<first 16 hex chars of the hash>`` — one per flow, so
  concurrent connects in one browser do not overwrite each other;
* ``HttpOnly``, ``SameSite=Lax`` (sent on the vendor's top-level redirect back),
  ``Path`` scoped to the callback route, ``Max-Age`` equal to the state TTL;
* ``Secure`` whenever the deployment's public origin is https. A plain-http
  origin (local installs on ``http://127.0.0.1``) omits it, since browsers drop
  a ``Secure`` cookie set over http.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from fastapi import Request, Response

from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import (
    OAUTH_CALLBACK_PATH,
    BrowserBinding,
    browser_binding_cookie_name,
    new_browser_binding,
)
from jentic_one.shared.context import Context
from jentic_one.shared.url import is_loopback_host
from jentic_one.shared.web.links import public_base_url


def mint_browser_binding(request: Request) -> BrowserBinding:
    """A fresh binding for the calling browser, recording the host it called on."""
    return new_browser_binding(host=request.url.hostname)


def set_browser_binding_cookie(
    response: Response, request: Request, ctx: Context, binding: BrowserBinding
) -> None:
    """Write ``binding`` to the calling browser."""
    response.set_cookie(
        key=binding.cookie_name,
        value=binding.value,
        max_age=ctx.config.credentials.connect.state_ttl_seconds,
        path=OAUTH_CALLBACK_PATH,
        secure=_secure(request, ctx),
        httponly=True,
        samesite="lax",
    )


def read_browser_binding_cookie(request: Request, state: ConnectState | None) -> str | None:
    """The binding cookie for ``state``'s flow, if the request carries it."""
    if state is None or not state.browser_binding_hash:
        return None
    return request.cookies.get(browser_binding_cookie_name(state.browser_binding_hash))


def clear_browser_binding_cookie(
    response: Response, request: Request, ctx: Context, state: ConnectState | None
) -> None:
    """Expire ``state``'s binding cookie on ``response`` (a no-op for an unbound state)."""
    if state is None or not state.browser_binding_hash:
        return
    response.delete_cookie(
        key=browser_binding_cookie_name(state.browser_binding_hash),
        path=OAUTH_CALLBACK_PATH,
        secure=_secure(request, ctx),
        httponly=True,
        samesite="lax",
    )


def loopback_alias_redirect(request: Request, state: ConnectState | None) -> str | None:
    """The callback URL on the loopback alias that holds the cookie, if that is another host.

    A local deployment answers on both ``localhost`` and ``127.0.0.1``, but the
    session flow's ``redirect_uri`` names one of them while the approver's
    browser may be on the other. Cookies are per host, so the callback is sent
    once to the host the browser started on (same scheme, port, path and
    query). Only between loopback hosts, and only to the host signed into the
    state.
    """
    if state is None or not state.browser_host:
        return None
    current = request.url.hostname or ""
    target = state.browser_host
    if target == current or not (is_loopback_host(current) and is_loopback_host(target)):
        return None
    parts = urlsplit(str(request.url))
    netloc = f"[{target}]" if ":" in target else target
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def _secure(request: Request, ctx: Context) -> bool:
    return urlsplit(public_base_url(ctx.config, request)).scheme == "https"
