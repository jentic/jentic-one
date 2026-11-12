"""Apply injected upstream credentials to an outbound request.

The one implementation the sync broker router and the async job worker share,
so the same call reaches the upstream with the same URL and headers whether it
runs inline or from a queued job.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, unquote_plus, urlencode, urlparse, urlunparse

from jentic_one.shared.url import apply_server_variables

if TYPE_CHECKING:
    # Annotation only: ``shared.jobs`` imports this module from its worker, so
    # a runtime import here would be circular.
    from jentic_one.shared.jobs.protocols import InjectedAuth

_MAX_TRACKED_COOKIES = 4096
_current_upstream_url: ContextVar[str | None] = ContextVar(
    "broker_current_upstream_url", default=None
)


@dataclass(frozen=True, slots=True)
class _UpstreamCookieScope:
    """Origin, path, and security scope for an upstream-issued ``Set-Cookie``."""

    host_port: tuple[str, int]
    path: str
    secure: bool


_UPSTREAM_COOKIE_SCOPES: OrderedDict[tuple[str, str], list[_UpstreamCookieScope]] = OrderedDict()


def set_current_upstream_url(url: str | None) -> None:
    """Record the active upstream target URL for the current execution task."""
    _current_upstream_url.set(url)


def get_current_upstream_url() -> str | None:
    """Return the active upstream target URL for the current execution task, if any."""
    return _current_upstream_url.get()


def clear_upstream_cookie_scopes() -> None:
    """Clear tracked upstream ``Set-Cookie`` origin scopes."""
    _UPSTREAM_COOKIE_SCOPES.clear()


def _parse_origin_and_path(url: str) -> tuple[str, tuple[str, int], str] | None:
    """Return ``(scheme, (hostname, port), path)`` for *url*, or ``None`` if invalid."""
    if not url or not url.strip():
        return None
    candidate = url.strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    host = parsed.hostname.lower().rstrip(".")
    path = parsed.path or "/"
    return parsed.scheme, (host, port), path


def _cookie_path_matches(request_path: str, cookie_path: str) -> bool:
    """RFC 6265 §5.1.4 path-match predicate."""
    req = request_path or "/"
    cpath = cookie_path or "/"
    if req == cpath:
        return True
    if req.startswith(cpath):
        if cpath.endswith("/"):
            return True
        if len(req) > len(cpath) and req[len(cpath)] == "/":
            return True
    return False


def record_upstream_set_cookie(upstream_url: str, set_cookie_header: str) -> None:
    """Record the upstream origin and path scope for a ``Set-Cookie`` field value.

    Prevents a shared-origin cookie jar from replaying an upstream session cookie
    set by one API to an unrelated upstream API behind the same broker origin.
    """
    parsed_target = _parse_origin_and_path(upstream_url)
    if parsed_target is None:
        return
    _, host_port, request_path = parsed_target

    segments = [seg.strip() for seg in set_cookie_header.split(";") if seg.strip()]
    if not segments or "=" not in segments[0]:
        return
    raw_name, _, raw_val = segments[0].partition("=")
    cookie_name = raw_name.strip()
    cookie_value = raw_val.strip()
    if not cookie_name:
        return

    # RFC 6265 §5.1.4 default-path: omit the final request-path segment.
    cookie_path = request_path.rsplit("/", 1)[0] or "/"
    secure = False
    for attr in segments[1:]:
        attr_name, has_eq, attr_val = attr.partition("=")
        attr_key = attr_name.strip().lower()
        if attr_key == "path" and has_eq:
            val = attr_val.strip()
            if val.startswith("/"):
                cookie_path = val
        elif attr_key == "secure" and not has_eq:
            secure = True

    key = (cookie_name, cookie_value)
    scope = _UpstreamCookieScope(host_port=host_port, path=cookie_path, secure=secure)
    existing = _UPSTREAM_COOKIE_SCOPES.get(key)
    if existing is not None:
        _UPSTREAM_COOKIE_SCOPES.move_to_end(key)
        # Replace any prior scope for the same host_port so updated attributes win.
        filtered = [s for s in existing if s.host_port != host_port]
        filtered.append(scope)
        _UPSTREAM_COOKIE_SCOPES[key] = filtered
        return

    if len(_UPSTREAM_COOKIE_SCOPES) >= _MAX_TRACKED_COOKIES:
        _UPSTREAM_COOKIE_SCOPES.popitem(last=False)
    _UPSTREAM_COOKIE_SCOPES[key] = [scope]


def is_cookie_allowed_for_upstream(cookie_name: str, cookie_value: str, target_url: str) -> bool:
    """Whether ``(cookie_name, cookie_value)`` may be forwarded to ``target_url``."""
    scopes = _UPSTREAM_COOKIE_SCOPES.get((cookie_name, cookie_value))
    if scopes is None:
        # An unknown pair may be a foreign session after a restart or eviction;
        # there is no origin evidence that makes it safe to forward.
        return False
    parsed_target = _parse_origin_and_path(target_url)
    if parsed_target is None:
        return False
    scheme, host_port, req_path = parsed_target
    return any(
        scope.host_port == host_port
        and (not scope.secure or scheme == "https")
        and _cookie_path_matches(req_path, scope.path)
        for scope in scopes
    )


def filter_inbound_cookie(
    inbound_cookie: str | None,
    target_url: str,
    *,
    excluded_names: AbstractSet[str] = frozenset(),
) -> str | None:
    """Filter caller cookie pairs for ``target_url``, dropping colliding or foreign cookies."""
    if not inbound_cookie or not inbound_cookie.strip():
        return None
    kept: list[str] = []
    for raw_pair in inbound_cookie.split(";"):
        pair = raw_pair.strip()
        if not pair:
            continue
        if "=" in pair:
            raw_name, _, raw_val = pair.partition("=")
            name = raw_name.strip()
            val = raw_val.strip()
        else:
            name = pair
            val = ""
        if (
            name in excluded_names
            or unquote(name) in excluded_names
            or unquote_plus(name) in excluded_names
        ):
            continue
        if target_url and not is_cookie_allowed_for_upstream(name, val, target_url):
            continue
        kept.append(pair)
    return "; ".join(kept) or None


def _strip_colliding_query_params(raw_query: str, injected_params: Mapping[str, str]) -> str:
    """Remove caller query parameters that collide with injected parameter names, then append."""
    kept_segments: list[str] = []
    if raw_query:
        for segment in raw_query.split("&"):
            if not segment:
                continue
            raw_key = segment.partition("=")[0]
            if (
                raw_key in injected_params
                or unquote(raw_key) in injected_params
                or unquote_plus(raw_key) in injected_params
            ):
                continue
            kept_segments.append(segment)
    encoded_injected = urlencode(injected_params)
    if encoded_injected:
        kept_segments.append(encoded_injected)
    return "&".join(kept_segments)


def _connection_nominated_from_mapping(headers: Mapping[str, str]) -> frozenset[str]:
    """Extract lower-cased ``Connection``-nominated field names from ``headers``."""
    nominated: set[str] = set(getattr(headers, "connection_nominated", ()))
    for key, val in headers.items():
        if key.lower() == "connection":
            for token in val.split(","):
                cleaned = token.strip().lower()
                if cleaned:
                    nominated.add(cleaned)
    return frozenset(nominated)


def apply_injection(
    headers: Mapping[str, str],
    url: str,
    injection: InjectedAuth,
    *,
    inbound_cookie: str | None = None,
    server_variable_defaults: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Return the outbound ``(url, headers)`` with ``injection`` applied.

    - Server-variable credentials are substituted into the URL template;
      placeholders still left fall back to ``server_variable_defaults``.
    - Query-param credentials remove any colliding caller query parameter names
      and append the injected parameters to the URL query.
    - ``headers`` (the caller's forwarded or replayed headers) have any
      ``Connection``-nominated hop-by-hop fields removed and are overlaid by
      the injected headers; an injected name wins in any letter case.
    - The outbound ``Cookie`` strips any caller cookie colliding with an
      injected cookie name or scoped to a different upstream origin, then
      appends the cookie credentials. Any ``Cookie`` already in ``headers`` is
      consolidated so the header is never sent twice.
    """
    if injection.server_variables or server_variable_defaults:
        url = apply_server_variables(url, injection.server_variables, server_variable_defaults)

    set_current_upstream_url(url)

    if injection.query_params:
        parsed = urlparse(url)
        url = urlunparse(
            parsed._replace(
                query=_strip_colliding_query_params(parsed.query, injection.query_params)
            )
        )

    nominated = _connection_nominated_from_mapping(headers)
    if "cookie" in nominated:
        effective_inbound: str | None = None
    elif inbound_cookie is not None:
        effective_inbound = inbound_cookie
    else:
        cookie_vals = [v for k, v in headers.items() if k.lower() == "cookie"]
        effective_inbound = "; ".join(cookie_vals) or None

    injected = dict(injection.headers)
    cookie = _merge_cookie(effective_inbound, injection.cookies, target_url=url)
    if cookie:
        injected["Cookie"] = cookie
    overridden = {name.lower() for name in injected} | {"cookie", "connection"} | nominated
    out = {name: value for name, value in headers.items() if name.lower() not in overridden}
    out.update(injected)
    return url, out


def _merge_cookie(
    inbound: str | None, cookies: Mapping[str, str], *, target_url: str = ""
) -> str | None:
    """Filtered ``inbound`` followed by the ``name=value`` cookie credentials, or ``None``."""
    filtered_inbound = filter_inbound_cookie(
        inbound,
        target_url,
        excluded_names=set(cookies.keys()),
    )
    parts = [filtered_inbound] if filtered_inbound else []
    parts.extend(f"{name}={value}" for name, value in cookies.items())
    return "; ".join(parts) or None
