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
  ``Max-Age`` equal to the state TTL;
* ``Path`` = the path of the public callback URL (the one the vendor redirects
  the browser to), so a deployment served under a base-URL path still gets it;
* ``Secure`` whenever that callback URL, the deployment's public origin or the
  request itself is https (behind a TLS-terminating proxy the request may be
  plain http while the callback the browser lands on is https). A plain-http
  local install (``http://127.0.0.1``) omits it, since browsers drop a
  ``Secure`` cookie set over http.

Each cookie lives only until its callback or the state TTL. They cannot be
pruned when a flow starts (being scoped to the callback path, the browser never
sends them to the begin routes), so a callback carrying more than
``MAX_BINDING_COOKIES`` of them expires all but its own.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit, urlunsplit

from fastapi import Request, Response

from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import (
    BROWSER_BINDING_COOKIE_PREFIX,
    OAUTH_CALLBACK_PATH,
    BrowserBinding,
    StateError,
    browser_binding_cookie_name,
    browser_binding_matches,
    decode_state,
    new_browser_binding,
)
from jentic_one.control.services.integrations.flow_handlers.auth_code import (
    platform_redirect_uri,
)
from jentic_one.shared.context import Context
from jentic_one.shared.url import is_loopback_host
from jentic_one.shared.web.links import public_base_url

#: Binding cookies a browser may accumulate (abandoned connects) before a
#: callback expires the extras.
MAX_BINDING_COOKIES = 10


def mint_browser_binding(request: Request) -> BrowserBinding:
    """A fresh binding for the calling browser, recording the host it called on."""
    return new_browser_binding(host=request.url.hostname)


def set_browser_binding_cookie(
    response: Response,
    request: Request,
    ctx: Context,
    binding: BrowserBinding,
    *,
    authorize_url: str,
) -> None:
    """Write ``binding`` to the calling browser for the flow ``authorize_url`` starts."""
    callback_url = flow_callback_url(request, ctx, _state_of(ctx, authorize_url))
    response.set_cookie(
        key=binding.cookie_name,
        value=binding.value,
        max_age=ctx.config.credentials.connect.state_ttl_seconds,
        path=_cookie_path(callback_url),
        secure=_secure(request, ctx, callback_url),
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
    """Expire ``state``'s binding cookie on ``response`` (a no-op for an unbound state).

    When the callback came from the browser that started the flow (its
    cookie matched) and that browser also carries ``MAX_BINDING_COOKIES`` or
    more binding cookies of other flows (connects it abandoned), those are
    expired too. A refused callback never prunes.

    Uses the same ``Path`` and ``Secure`` as when it was set (both derive
    from :func:`flow_callback_url` of the same state).
    """
    if state is None or not state.browser_binding_hash:
        return
    callback_url = flow_callback_url(request, ctx, state)
    own = browser_binding_cookie_name(state.browser_binding_hash)
    names = [own]
    # Prune only from the browser that started this flow: a refused callback
    # proves nothing about the cookies it carries, so it touches only its own.
    if browser_binding_matches(state, request.cookies.get(own)):
        others = [
            name
            for name in request.cookies
            if name.startswith(BROWSER_BINDING_COOKIE_PREFIX) and name != own
        ]
        if len(others) >= MAX_BINDING_COOKIES:
            names += others
    for name in names:
        response.delete_cookie(
            key=name,
            path=_cookie_path(callback_url),
            secure=_secure(request, ctx, callback_url),
            httponly=True,
            samesite="lax",
        )


def flow_callback_url(request: Request, ctx: Context, state: ConnectState | None) -> str:
    """The callback URL the vendor sends this flow's browser back to.

    The ``redirect_uri`` signed into the state when the flow carries one (the
    credential connect), else the platform callback a connect-session uses
    (the configured ``direct_oauth2`` ``redirect_uri`` or the public origin),
    else this deployment's public origin plus the callback route.
    """
    if state is not None and state.redirect_uri:
        return state.redirect_uri
    if state is not None and state.session_id:
        return platform_redirect_uri(ctx)
    return f"{public_base_url(ctx.config, request)}{OAUTH_CALLBACK_PATH}"


def _state_of(ctx: Context, authorize_url: str) -> ConnectState | None:
    """The connect state an authorization URL carries (``None`` if it carries none).

    The flows append their signed state last, after any query the configured
    authorization endpoint already had (which may hold its own ``state``), so
    the last ``state`` value is ours.
    """
    raw = parse_qs(urlsplit(authorize_url).query).get("state")
    if not raw:
        return None
    try:
        return decode_state(ctx.config.credentials.connect.state_secret.get_secret_value(), raw[-1])
    except StateError:
        return None


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


def _cookie_path(callback_url: str) -> str:
    return urlsplit(callback_url).path or OAUTH_CALLBACK_PATH


def _secure(request: Request, ctx: Context, callback_url: str) -> bool:
    return "https" in (
        urlsplit(callback_url).scheme,
        urlsplit(public_base_url(ctx.config, request)).scheme,
        request.url.scheme,
    )
