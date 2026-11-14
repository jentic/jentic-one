"""Pure helpers for reconstructing the upstream URL and filtering proxied headers.

Both are pure functions (no FastAPI/httpx/DB) so they unit-test in isolation —
domain layer per the 00-overview layering table.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import unquote, unquote_plus

from jentic_one.broker.core.headers import (
    BROKER_CONSUMED_HEADERS,
    BROKER_OWNED_RESPONSE_HEADER_PREFIXES,
    BROKER_OWNED_RESPONSE_HEADERS,
    HOP_BY_HOP_HEADERS,
    SPOOFABLE_HEADERS,
    URL_BEARING_RESPONSE_HEADERS,
    connection_nominated_headers,
    extract_header_items,
    scrub_sensitive_header_items,
)
from jentic_one.shared.http.injection import (
    ReflectionGuard,
    filter_inbound_cookie,
    get_current_reflection_guard,
    get_current_upstream_url,
    record_upstream_set_cookie,
    set_current_upstream_url,
)

# Restore the scheme's doubled slash that Starlette collapses (``https:/x`` →
# ``https://x``) and normalise RFC 3986 §3.1 scheme names to lower-case while
# leaving host, path, and query bytes untouched (#1897).
_COLLAPSED_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):/(?!/)")
_EXPLICIT_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")

# Response framing header dropped on passthrough: the ASGI server recomputes
# ``content-length`` from the actual body bytes (or uses chunked transfer on
# streaming responses). ``content-encoding`` is preserved because the HTTP
# runner reads raw, undecoded bytes via ``aiter_raw``.
_STRIPPED_RESPONSE_BODY_HEADERS: frozenset[str] = frozenset({"content-length"})

# Inbound framing that describes the *caller's* body, not the outbound one. The
# broker drops the body on body-less methods (GET, HEAD, OPTIONS) and httpx
# frames whatever body it does send, so a forwarded ``content-length`` can only
# be stale: the HTTP client rejects a declared length that the body does not
# match, failing the request before it reaches the upstream.
_INBOUND_FRAMING_HEADERS: frozenset[str] = frozenset({"content-length"})

#: ``Cache-Control`` on every passthrough response — the same directive the
#: control plane stamps on authenticated JSON
#: (:data:`jentic_one.shared.web.cache_control.PRIVATE_NO_STORE`).
PASSTHROUGH_CACHE_CONTROL = "private, no-store"

# Request headers an async (``Prefer: respond-async``) or held execution keeps
# for its worker run. The job payload sits in the admin DB (a held one for its
# whole approval window), so this is an explicit
# allow-list of end-to-end semantic headers that describe the body, range/conditional
# semantics, content negotiation, and the API version a vendor requires — never
# the caller's credentials, cookies, hop-by-hop or broker headers. The run
# re-injects upstream credentials itself; the caller's ``Cookie`` rides
# separately, encrypted (``INBOUND_COOKIE_PAYLOAD_KEY`` in ``shared/jobs/hold.py``).
REPLAY_HEADERS: frozenset[str] = frozenset(
    {
        "accept",
        "accept-charset",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "content-disposition",
        "content-encoding",
        "content-language",
        "content-location",
        "content-range",
        "content-type",
        "expect",
        "if-match",
        "if-modified-since",
        "if-none-match",
        "if-range",
        "if-unmodified-since",
        "max-forwards",
        "range",
        "user-agent",
        # Vendor API version and semantic headers required on calls.
        "anthropic-beta",
        "anthropic-version",
        "api-version",
        "intercom-version",
        "notion-version",
        "openai-beta",
        "openai-organization",
        "openai-project",
        "stripe-account",
        "stripe-context",
        "stripe-version",
        "x-api-version",
        "x-github-api-version",
    }
)


class ResponseHeaderMap(dict[str, str]):
    """Dictionary view of HTTP headers that also retains raw ``(name, value)`` pairs."""

    __slots__ = ("raw_headers",)

    def __init__(
        self,
        mapping: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        *,
        raw_headers: Iterable[tuple[str, str]] | None = None,
    ) -> None:
        if isinstance(mapping, Mapping):
            super().__init__(mapping)
            if raw_headers is not None:
                self.raw_headers: tuple[tuple[str, str], ...] = tuple(
                    (str(k), str(v)) for k, v in raw_headers
                )
            elif isinstance(mapping, ResponseHeaderMap):
                self.raw_headers = mapping.raw_headers
            else:
                self.raw_headers = tuple(extract_header_items(mapping))
        else:
            items = tuple((str(k), str(v)) for k, v in mapping)
            combined: dict[str, str] = {}
            for k, v in items:
                if k in combined:
                    combined[k] = f"{combined[k]}, {v}"
                else:
                    combined[k] = v
            super().__init__(combined)
            self.raw_headers = (
                tuple((str(k), str(v)) for k, v in raw_headers)
                if raw_headers is not None
                else items
            )

    def multi_items(self) -> list[tuple[str, str]]:
        """Return all ``(name, value)`` pairs in order, preserving duplicates."""
        return list(self.raw_headers)

    def getlist(self, name: str) -> list[str]:
        """Return all values for ``name`` (case-insensitive) in order."""
        target = name.lower()
        return [v for k, v in self.raw_headers if k.lower() == target]

    get_list = getlist


class ForwardedHeaderMap(dict[str, str]):
    """Forwarded request headers carrying ``Connection``-nominated field names."""

    __slots__ = ("connection_nominated",)

    def __init__(
        self,
        mapping: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        *,
        connection_nominated: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(mapping)
        self.connection_nominated = connection_nominated


def reconstruct_upstream_url(scope: Mapping[str, Any]) -> str:
    """Rebuild the upstream URL from the raw ASGI scope, byte-exact.

    Reconstructs from ``scope["raw_path"]`` / ``scope["query_string"]`` — **never**
    the decoded ``{upstream_url:path}`` param — so percent-encoding survives
    (``%2F`` stays ``%2F``). Forwarding the decoded param would mutilate a path
    segment that legitimately contains an encoded slash (a single segment
    ``jentic%2Fcore`` would split into two), causing upstream 404s or a
    path-traversal primitive.
    """
    raw_path_bytes = scope.get("raw_path") or scope.get("path", "").encode("latin-1")
    raw_path = raw_path_bytes.decode("latin-1")
    query_string = scope.get("query_string", b"").decode("latin-1")

    raw_path = raw_path.lstrip("/")
    url = _COLLAPSED_SCHEME_RE.sub(lambda m: f"{m.group(1).lower()}://", raw_path, count=1)
    scheme_match = _EXPLICIT_SCHEME_RE.match(url)
    if scheme_match is not None:
        url = f"{scheme_match.group(1).lower()}://{url[scheme_match.end() :]}"
    else:
        url = f"https://{url}"
    if query_string:
        url = f"{url}?{query_string}"
    set_current_upstream_url(url)
    return url


def forward_headers(
    inbound: Mapping[str, str],
    injected: Mapping[str, str],
    *,
    upstream_url: str | None = None,
) -> dict[str, str]:
    """Filter inbound request headers for forwarding, then apply injected auth.

    Strips fixed hop-by-hop (incl. ``Host``), ``Connection``-nominated
    hop-by-hop, broker-consumed, and spoofable forwarding/topology headers,
    plus the inbound ``Content-Length``: httpx computes the outbound length
    from the body actually sent. Injected auth headers win on conflict in any
    letter case.
    """
    nominated = connection_nominated_headers(inbound)
    injected_lower = {k.lower() for k in injected}
    out: dict[str, str] = {}
    for key, value in inbound.items():
        lowered = key.lower()
        if (
            lowered in HOP_BY_HOP_HEADERS
            or lowered in nominated
            or lowered in BROKER_CONSUMED_HEADERS
            or lowered in SPOOFABLE_HEADERS
            or lowered in _INBOUND_FRAMING_HEADERS
            or lowered in injected_lower
        ):
            continue
        if lowered == "cookie" and upstream_url:
            filtered_cookie = filter_inbound_cookie(value, upstream_url)
            if not filtered_cookie:
                continue
            value = filtered_cookie
        out[key] = value
    out.update(injected)
    return ForwardedHeaderMap(out, connection_nominated=nominated)


# API version request headers beyond the named ones above. Many vendors refuse
# a call without one, and the version chooses the body and response schema, so
# a queued or held run must send it as the caller did. A header replays when its name
# is one or more hyphen-separated alphanumeric labels ending in ``-version``
# (every ``<vendor>-version``); a bare ``version`` and anything in the
# ``jentic-``/``x-jentic-`` namespace do not. :func:`is_replay_header` excludes
# the hop-by-hop, broker-consumed and spoofable sets regardless.
_REPLAY_VERSION_HEADER_RE = re.compile(r"^(?:[a-z0-9]+-)+version$")
_JENTIC_HEADER_PREFIXES: tuple[str, ...] = ("jentic-", "x-jentic-")


def is_replay_header(name: str) -> bool:
    """Whether a queued or held execution keeps the inbound header ``name``.

    True for :data:`REPLAY_HEADERS` and API version headers (see
    ``_REPLAY_VERSION_HEADER_RE``); never for hop-by-hop, broker-consumed,
    spoofable, framing, or ``Jentic-*`` headers.
    """
    key = name.lower()
    if (
        key in HOP_BY_HOP_HEADERS
        or key in BROKER_CONSUMED_HEADERS
        or key in SPOOFABLE_HEADERS
        or key in _INBOUND_FRAMING_HEADERS
        or key.startswith(_JENTIC_HEADER_PREFIXES)
    ):
        return False
    return key in REPLAY_HEADERS or _REPLAY_VERSION_HEADER_RE.fullmatch(key) is not None


def replay_headers(inbound: Mapping[str, str]) -> dict[str, str]:
    """The inbound headers :func:`is_replay_header` keeps, keyed lower-case.

    Excludes any header nominated in ``Connection`` so connection-scoped fields
    never persist into a queued or held job payload.
    """
    nominated = connection_nominated_headers(inbound)
    return {
        key.lower(): value
        for key, value in inbound.items()
        if key.lower() not in nominated and is_replay_header(key)
    }


extract_replay_headers = replay_headers


_LINK_TARGET_RE = re.compile(r"<([^<>]*)>")
_REFRESH_URL_RE = re.compile(r"(\burl\s*=\s*)(.*)$", re.IGNORECASE)
_PERCENT_ESCAPE_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def _strip_query_credentials(target: str, query_param_names: frozenset[str]) -> str:
    """Remove injected query fields while retaining the target's other components."""
    before_fragment, fragment_marker, fragment = target.partition("#")
    path, query_marker, query = before_fragment.partition("?")
    if not query_marker:
        return target
    kept: list[str] = []
    for segment in query.split("&"):
        raw_name = segment.partition("=")[0]
        if (
            raw_name in query_param_names
            or unquote(raw_name) in query_param_names
            or unquote_plus(raw_name) in query_param_names
        ):
            continue
        kept.append(segment)
    cleaned = path
    if kept:
        cleaned = f"{cleaned}?{'&'.join(kept)}"
    if fragment_marker:
        cleaned = f"{cleaned}#{fragment}"
    return cleaned


def _replace_reflected_secret(value: str, secret: str) -> str:
    """Replace a credential, ignoring hex case only inside percent-escape octets."""
    pattern: list[str] = []
    offset = 0
    for escape in _PERCENT_ESCAPE_RE.finditer(secret):
        pattern.append(re.escape(secret[offset : escape.start()]))
        octet = escape.group(1)
        pattern.append(
            "%"
            + "".join(
                f"[{char.lower()}{char.upper()}]" if char in "abcdefABCDEF" else char
                for char in octet
            )
        )
        offset = escape.end()
    pattern.append(re.escape(secret[offset:]))
    return re.sub("".join(pattern), "[REDACTED]", value)


def _strip_refresh_query_credentials(value: str, query_param_names: frozenset[str]) -> str:
    """Remove injected query fields while preserving Refresh URL delimiters."""

    def replace(match: re.Match[str]) -> str:
        target = match.group(2)
        if target.startswith(('"', "'")):
            delimiter = target[0]
            closing = target.find(delimiter, 1)
            if closing != -1:
                return (
                    match.group(1)
                    + delimiter
                    + _strip_query_credentials(target[1:closing], query_param_names)
                    + target[closing:]
                )
        return match.group(1) + _strip_query_credentials(target, query_param_names)

    return _REFRESH_URL_RE.sub(replace, value, count=1)


def _scrub_url_header(name: str, value: str, query_param_names: frozenset[str]) -> str:
    """Remove injected query fields from one URL-bearing response header."""
    if name == "link":
        return _LINK_TARGET_RE.sub(
            lambda match: f"<{_strip_query_credentials(match.group(1), query_param_names)}>",
            value,
        )
    if name == "refresh":
        return _strip_refresh_query_credentials(value, query_param_names)
    return _strip_query_credentials(value, query_param_names)


def scrub_reflected_secrets(
    items: Iterable[tuple[str, str]], guard: ReflectionGuard | None
) -> list[tuple[str, str]]:
    """Remove broker metadata, injected query fields, and reflected credential values."""
    query_param_names = guard.query_param_names if guard is not None else frozenset()
    secrets = guard.secrets if guard is not None else ()
    scrubbed: list[tuple[str, str]] = []
    for key, value in items:
        lowered = key.lower()
        if lowered in BROKER_OWNED_RESPONSE_HEADERS or lowered.startswith(
            BROKER_OWNED_RESPONSE_HEADER_PREFIXES
        ):
            continue
        if lowered in URL_BEARING_RESPONSE_HEADERS and query_param_names:
            value = _scrub_url_header(lowered, value, query_param_names)
        for secret in secrets:
            value = _replace_reflected_secret(value, secret)
        scrubbed.append((key, value))
    return scrubbed


def passthrough_response_header_items(
    upstream: Mapping[str, str] | Iterable[tuple[str, str]],
    *,
    upstream_url: str | None = None,
) -> list[tuple[str, str]]:
    """Filter upstream response header ``(name, value)`` pairs for passthrough.

    Preserves repeated fields (such as multiple ``Set-Cookie`` headers) and
    ``Content-Encoding`` (matching the raw bytes read via ``aiter_raw``),
    strips fixed and ``Connection``-nominated hop-by-hop headers plus
    ``Content-Length``, and replaces ``Cache-Control`` with
    :data:`PASSTHROUGH_CACHE_CONTROL`.
    """
    items = extract_header_items(upstream)
    nominated = connection_nominated_headers(upstream)
    effective_url = upstream_url or get_current_upstream_url()
    kept: list[tuple[str, str]] = []
    for key, value in items:
        lowered = key.lower()
        if (
            lowered in HOP_BY_HOP_HEADERS
            or lowered in nominated
            or lowered in _STRIPPED_RESPONSE_BODY_HEADERS
            or lowered == "cache-control"
        ):
            continue
        if lowered == "set-cookie" and effective_url:
            record_upstream_set_cookie(effective_url, value)
        kept.append((key, value))
    kept = scrub_reflected_secrets(kept, get_current_reflection_guard())
    kept.append(("cache-control", PASSTHROUGH_CACHE_CONTROL))
    return kept


def passthrough_streaming_header_items(
    upstream: Mapping[str, str] | Iterable[tuple[str, str]],
    *,
    upstream_url: str | None = None,
) -> list[tuple[str, str]]:
    """Filter upstream streaming response header ``(name, value)`` pairs for passthrough."""
    return passthrough_response_header_items(upstream, upstream_url=upstream_url)


filter_response_header_items = passthrough_response_header_items


def passthrough_response_headers(
    upstream: Mapping[str, str] | Iterable[tuple[str, str]],
    *,
    upstream_url: str | None = None,
) -> ResponseHeaderMap:
    """Filter upstream *response* headers for passthrough.

    Strips fixed and ``Connection``-nominated hop-by-hop headers plus
    ``content-length`` (recomputed by the ASGI server from the actual body).
    ``content-encoding`` is preserved because ``HttpRunner`` streams raw,
    still-compressed bytes via ``aiter_raw``. ``Cache-Control`` is always
    ``private, no-store``. Returns a :class:`ResponseHeaderMap` (a ``dict``
    subclass) that also retains ``raw_headers`` for repeated fields.
    """
    items = passthrough_response_header_items(upstream, upstream_url=upstream_url)
    return ResponseHeaderMap(items, raw_headers=items)


def passthrough_streaming_headers(
    upstream: Mapping[str, str] | Iterable[tuple[str, str]],
    *,
    upstream_url: str | None = None,
) -> ResponseHeaderMap:
    """Filter upstream response headers for the **raw streaming** passthrough."""
    items = passthrough_streaming_header_items(upstream, upstream_url=upstream_url)
    return ResponseHeaderMap(items, raw_headers=items)


filter_response_headers = passthrough_response_headers


__all__ = [
    "PASSTHROUGH_CACHE_CONTROL",
    "REPLAY_HEADERS",
    "ForwardedHeaderMap",
    "ResponseHeaderMap",
    "extract_replay_headers",
    "filter_response_header_items",
    "filter_response_headers",
    "forward_headers",
    "is_replay_header",
    "passthrough_response_header_items",
    "passthrough_response_headers",
    "passthrough_streaming_header_items",
    "passthrough_streaming_headers",
    "reconstruct_upstream_url",
    "replay_headers",
    "scrub_reflected_secrets",
    "scrub_sensitive_header_items",
]
