"""Apply injected upstream credentials to an outbound request.

The one implementation the sync broker router and the async job worker share,
so the same call reaches the upstream with the same URL and headers whether it
runs inline or from a queued job.
"""

from __future__ import annotations

import ipaddress
import re
from collections import OrderedDict
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from urllib.parse import quote, quote_plus, unquote, unquote_plus, urlencode, urlparse, urlunparse

from jentic_one.shared.url import apply_server_variables

if TYPE_CHECKING:
    # Annotation only: ``shared.jobs`` imports this module from its worker, so
    # a runtime import here would be circular.
    from jentic_one.shared.jobs.protocols import InjectedAuth

_MAX_ACTOR_PARTITIONS = 256
_MAX_COOKIES_PER_PARTITION = 256
_MAX_COOKIE_LIFETIME_S = 100 * 365 * 24 * 60 * 60
_MIN_REFLECTION_SECRET_LEN = 8
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

_current_upstream_url: ContextVar[str | None] = ContextVar(
    "broker_current_upstream_url", default=None
)
_current_cookie_scope: ContextVar[str] = ContextVar("broker_current_cookie_scope", default="")


@dataclass(frozen=True, slots=True)
class ReflectionGuard:
    """Credential material used to sanitize upstream response headers."""

    query_param_names: frozenset[str]
    secrets: tuple[str, ...]
    credential_cookies: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        """Describe the guard without exposing credential values."""
        cookie_names = tuple(name for name, _ in self.credential_cookies)
        return (
            f"ReflectionGuard(query_param_names={self.query_param_names!r}, "
            f"secret_count={len(self.secrets)}, credential_cookie_names={cookie_names!r})"
        )


_current_reflection_guard: ContextVar[ReflectionGuard | None] = ContextVar(
    "broker_current_reflection_guard", default=None
)


@dataclass(frozen=True, slots=True)
class _UpstreamCookieScope:
    """Origin/domain, path, security, and expiry scope for an upstream ``Set-Cookie``."""

    host_port: tuple[str, int]
    domain: str | None
    path: str
    secure: bool
    expires_at: float | None = None


_CookiePartition = OrderedDict[tuple[str, str], list[_UpstreamCookieScope]]
_UPSTREAM_COOKIE_SCOPES: OrderedDict[str, _CookiePartition] = OrderedDict()


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


def format_cookie_scope_key(
    actor_id: str | None = None,
    credential_id: str | None = None,
) -> str:
    """Build a partition key isolating upstream cookies by actor and credential."""
    actor = (actor_id or "").strip()
    cred = (credential_id or "").strip()
    if actor and cred:
        return f"{actor}:{cred}"
    return actor or cred


def set_current_cookie_scope(scope_key: str | None) -> Token[str]:
    """Record the active cookie scope partition key for the current execution task."""
    return _current_cookie_scope.set((scope_key or "").strip())


def reset_current_cookie_scope(token: Token[str]) -> None:
    """Restore the prior cookie scope partition key."""
    _current_cookie_scope.reset(token)


def get_current_cookie_scope() -> str:
    """Return the active cookie scope partition key for the current execution task."""
    return _current_cookie_scope.get()


def _resolve_scope_key(
    *,
    actor_id: str | None = None,
    credential_id: str | None = None,
    scope_key: str | None = None,
) -> str:
    """Resolve the effective cookie partition key from explicit args or context."""
    if scope_key is not None:
        return scope_key.strip()
    if actor_id is not None or credential_id is not None:
        return format_cookie_scope_key(actor_id, credential_id)
    return get_current_cookie_scope()


def set_current_upstream_url(url: str | None) -> None:
    """Record the active upstream target URL for the current execution task."""
    _current_upstream_url.set(url)


def get_current_upstream_url() -> str | None:
    """Return the active upstream target URL for the current execution task, if any."""
    return _current_upstream_url.get()


def set_current_reflection_guard(guard: ReflectionGuard | None) -> None:
    """Record response-reflection metadata for the current execution task."""
    _current_reflection_guard.set(guard)


def get_current_reflection_guard() -> ReflectionGuard | None:
    """Return response-reflection metadata for the current execution task, if any."""
    return _current_reflection_guard.get()


def clear_upstream_cookie_scopes() -> None:
    """Clear tracked upstream ``Set-Cookie`` origin scopes across all partitions."""
    _UPSTREAM_COOKIE_SCOPES.clear()
    _current_cookie_scope.set("")


def _is_ip_address(host: str) -> bool:
    """Return ``True`` when ``host`` parses as an IPv4 or IPv6 literal."""
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _normalize_cookie_domain(raw_domain: str) -> str | None:
    """Normalize an RFC 6265 ``Domain`` attribute value, rejecting single-label or IP values."""
    candidate = raw_domain.strip().lower()
    if candidate.startswith("."):
        candidate = candidate[1:]
    if not candidate or "." not in candidate or _is_ip_address(candidate):
        return None
    labels = candidate.split(".")
    if any(
        not (1 <= len(label) <= 63) or _DOMAIN_LABEL_RE.fullmatch(label) is None for label in labels
    ):
        return None
    return candidate


def _host_domain_matches(host: str, domain: str) -> bool:
    """RFC 6265 §5.1.3 domain-match predicate on non-IP multi-label hostnames."""
    if _is_ip_address(host) or _is_ip_address(domain) or "." not in domain:
        return False
    return host == domain or host.endswith(f".{domain}")


def _parse_max_age(raw_value: str) -> int | None:
    """Parse an RFC 6265 §5.2.2 ``Max-Age`` attribute value."""
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    digits = cleaned[1:] if cleaned[0] == "-" else cleaned
    if not digits or not digits.isascii() or not digits.isdigit():
        return None
    return int(cleaned)


def _parse_expires(raw_value: str) -> datetime | None:
    """Parse an HTTP-date ``Expires`` attribute value into a UTC ``datetime``."""
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    try:
        parsed = parsedate_to_datetime(cleaned)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_scope_expired(scope: _UpstreamCookieScope, now_ts: float) -> bool:
    """Return ``True`` when ``scope`` has reached or passed its expiry timestamp."""
    return scope.expires_at is not None and scope.expires_at <= now_ts


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


def _scope_matches_target(
    scope: _UpstreamCookieScope,
    scheme: str,
    host_port: tuple[str, int],
    req_path: str,
) -> bool:
    """Return ``True`` when ``scope`` permits forwarding to ``(scheme, host_port, req_path)``."""
    if scope.secure and scheme != "https":
        return False
    if not _cookie_path_matches(req_path, scope.path):
        return False
    if scope.domain is not None:
        return _host_domain_matches(host_port[0], scope.domain)
    return scope.host_port == host_port


def _scope_matches_eviction(
    scope: _UpstreamCookieScope,
    *,
    host_port: tuple[str, int],
    cookie_domain: str | None,
    explicit_path: str | None,
) -> bool:
    """Return ``True`` when an expired/deletion ``Set-Cookie`` evicts ``scope``."""
    if explicit_path is not None and not (
        scope.path == explicit_path or _cookie_path_matches(scope.path, explicit_path)
    ):
        return False
    if cookie_domain is not None:
        if scope.domain is not None:
            return _host_domain_matches(scope.domain, cookie_domain) or _host_domain_matches(
                cookie_domain, scope.domain
            )
        return _host_domain_matches(scope.host_port[0], cookie_domain)
    if scope.domain is not None:
        return _host_domain_matches(host_port[0], scope.domain)
    return scope.host_port == host_port


def _evict_matching_cookie_scopes(
    partition: _CookiePartition,
    *,
    cookie_name: str,
    host_port: tuple[str, int],
    cookie_domain: str | None,
    explicit_path: str | None,
    now_ts: float,
) -> None:
    """Remove matching scopes for ``cookie_name`` without promoting surviving entries to MRU."""
    for key in list(partition.keys()):
        tracked_name, _ = key
        if tracked_name != cookie_name:
            continue
        kept = [
            s
            for s in partition[key]
            if not _is_scope_expired(s, now_ts)
            and not _scope_matches_eviction(
                s,
                host_port=host_port,
                cookie_domain=cookie_domain,
                explicit_path=explicit_path,
            )
        ]
        if kept:
            partition[key] = kept
        else:
            del partition[key]


def _prune_expired_in_partition(partition: _CookiePartition, now_ts: float) -> None:
    """Drop expired cookie scopes in ``partition`` before capacity eviction."""
    for key in list(partition.keys()):
        live = [s for s in partition[key] if not _is_scope_expired(s, now_ts)]
        if live:
            partition[key] = live
        else:
            del partition[key]


def _get_or_create_partition(partition_key: str, now_ts: float) -> _CookiePartition:
    """Return ``partition_key``'s bounded LRU map, evicting the oldest partition past capacity."""
    existing = _UPSTREAM_COOKIE_SCOPES.get(partition_key)
    if existing is not None:
        _UPSTREAM_COOKIE_SCOPES.move_to_end(partition_key)
        return existing

    if len(_UPSTREAM_COOKIE_SCOPES) >= _MAX_ACTOR_PARTITIONS:
        for pkey in list(_UPSTREAM_COOKIE_SCOPES.keys()):
            part = _UPSTREAM_COOKIE_SCOPES[pkey]
            _prune_expired_in_partition(part, now_ts)
            if not part:
                del _UPSTREAM_COOKIE_SCOPES[pkey]
    while len(_UPSTREAM_COOKIE_SCOPES) >= _MAX_ACTOR_PARTITIONS:
        _UPSTREAM_COOKIE_SCOPES.popitem(last=False)

    created: _CookiePartition = OrderedDict()
    _UPSTREAM_COOKIE_SCOPES[partition_key] = created
    return created


def record_upstream_set_cookie(
    upstream_url: str,
    set_cookie_header: str,
    *,
    actor_id: str | None = None,
    credential_id: str | None = None,
    scope_key: str | None = None,
) -> None:
    """Record or evict the upstream origin/domain and path scope for a ``Set-Cookie`` value.

    Prevents a shared-origin cookie jar from replaying an upstream session cookie
    set by one API or actor to an unrelated upstream API or actor behind the same
    broker origin.
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

    explicit_path: str | None = None
    raw_domain_attr: str | None = None
    secure = False
    max_age_val: int | None = None
    expires_dt: datetime | None = None

    for attr in segments[1:]:
        attr_name, has_eq, attr_val = attr.partition("=")
        attr_key = attr_name.strip().lower()
        if attr_key == "path" and has_eq:
            val = attr_val.strip()
            if val.startswith("/"):
                explicit_path = val
        elif attr_key == "domain" and has_eq:
            dom_val = attr_val.strip()
            if dom_val:
                raw_domain_attr = dom_val
        elif attr_key == "secure":
            secure = True
        elif attr_key == "max-age" and has_eq:
            parsed_max_age = _parse_max_age(attr_val)
            if parsed_max_age is not None:
                max_age_val = parsed_max_age
        elif attr_key == "expires" and has_eq:
            parsed_expires = _parse_expires(attr_val)
            if parsed_expires is not None:
                expires_dt = parsed_expires

    cookie_domain: str | None = None
    if raw_domain_attr is not None:
        cookie_domain = _normalize_cookie_domain(raw_domain_attr)
        if cookie_domain is None or not _host_domain_matches(host_port[0], cookie_domain):
            return

    now = _utc_now()
    now_ts = now.timestamp()
    is_expired = False
    expires_at: float | None = None
    if max_age_val is not None:
        if max_age_val <= 0:
            is_expired = True
        else:
            expires_at = now_ts + min(max_age_val, _MAX_COOKIE_LIFETIME_S)
    elif expires_dt is not None:
        if expires_dt <= now:
            is_expired = True
        else:
            expires_at = min(expires_dt.timestamp(), now_ts + _MAX_COOKIE_LIFETIME_S)

    partition_key = _resolve_scope_key(
        actor_id=actor_id, credential_id=credential_id, scope_key=scope_key
    )
    if is_expired:
        partition = _UPSTREAM_COOKIE_SCOPES.get(partition_key)
        if partition is None:
            return
        _evict_matching_cookie_scopes(
            partition,
            cookie_name=cookie_name,
            host_port=host_port,
            cookie_domain=cookie_domain,
            explicit_path=explicit_path,
            now_ts=now_ts,
        )
        if not partition:
            del _UPSTREAM_COOKIE_SCOPES[partition_key]
        return

    # RFC 6265 §5.1.4 default-path: omit the final request-path segment.
    cookie_path = (
        explicit_path if explicit_path is not None else (request_path.rsplit("/", 1)[0] or "/")
    )
    scope = _UpstreamCookieScope(
        host_port=host_port,
        domain=cookie_domain,
        path=cookie_path,
        secure=secure,
        expires_at=expires_at,
    )
    partition = _get_or_create_partition(partition_key, now_ts)
    key = (cookie_name, cookie_value)
    existing = partition.get(key)
    if existing is not None:
        partition.move_to_end(key)
        # Replace any prior scope for the same domain or host_port so updated attributes win.
        filtered = [
            s
            for s in existing
            if not _is_scope_expired(s, now_ts)
            and not (
                s.domain == cookie_domain
                if cookie_domain is not None
                else (s.domain is None and s.host_port == host_port)
            )
        ]
        filtered.append(scope)
        partition[key] = filtered
        return

    if len(partition) >= _MAX_COOKIES_PER_PARTITION:
        _prune_expired_in_partition(partition, now_ts)
    while len(partition) >= _MAX_COOKIES_PER_PARTITION:
        partition.popitem(last=False)
    partition[key] = [scope]


def _is_tracked_in_other_partition(
    key: tuple[str, str],
    current_partition_key: str,
    now_ts: float,
) -> bool:
    """Return ``True`` when ``key`` has a live scope in another actor/credential partition."""
    for pkey, other_partition in _UPSTREAM_COOKIE_SCOPES.items():
        if pkey == current_partition_key:
            continue
        other_scopes = other_partition.get(key)
        if other_scopes and any(not _is_scope_expired(s, now_ts) for s in other_scopes):
            return True
    return False


def is_cookie_allowed_for_upstream(
    cookie_name: str,
    cookie_value: str,
    target_url: str,
    *,
    allow_untracked: bool = False,
    actor_id: str | None = None,
    credential_id: str | None = None,
    scope_key: str | None = None,
) -> bool:
    """Whether ``(cookie_name, cookie_value)`` may be forwarded to ``target_url``."""
    partition_key = _resolve_scope_key(
        actor_id=actor_id, credential_id=credential_id, scope_key=scope_key
    )
    partition = _UPSTREAM_COOKIE_SCOPES.get(partition_key)
    key = (cookie_name, cookie_value)
    scopes = partition.get(key) if partition is not None else None
    now_ts = _utc_now().timestamp()
    if scopes is None:
        if not allow_untracked:
            return False
        if not _is_tracked_in_other_partition(key, partition_key, now_ts):
            # An unknown pair on the live edge may be a foreign session after a
            # restart or eviction; only cookies already filtered at enqueue/hold for
            # this target URL may proceed in a separate worker process.
            return True
        guard = get_current_reflection_guard()
        return bool(
            guard is not None
            and key in guard.credential_cookies
            and get_current_upstream_url() == target_url
        )
    live_scopes = [s for s in scopes if not _is_scope_expired(s, now_ts)]
    if len(live_scopes) != len(scopes) and partition is not None:
        if live_scopes:
            partition[key] = live_scopes
        else:
            del partition[key]
            if not partition:
                del _UPSTREAM_COOKIE_SCOPES[partition_key]

    parsed_target = _parse_origin_and_path(target_url)
    if parsed_target is None:
        return False
    scheme, host_port, req_path = parsed_target
    if any(_scope_matches_target(scope, scheme, host_port, req_path) for scope in live_scopes):
        if partition is not None and key in partition:
            _UPSTREAM_COOKIE_SCOPES.move_to_end(partition_key)
            partition.move_to_end(key)
        return True
    guard = get_current_reflection_guard()
    return bool(
        allow_untracked
        and guard is not None
        and (cookie_name, cookie_value) in guard.credential_cookies
        and get_current_upstream_url() == target_url
    )


def filter_inbound_cookie(
    inbound_cookie: str | None,
    target_url: str,
    *,
    excluded_names: AbstractSet[str] = frozenset(),
    allow_untracked: bool = False,
    actor_id: str | None = None,
    credential_id: str | None = None,
    scope_key: str | None = None,
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
        if target_url and not is_cookie_allowed_for_upstream(
            name,
            val,
            target_url,
            allow_untracked=allow_untracked,
            actor_id=actor_id,
            credential_id=credential_id,
            scope_key=scope_key,
        ):
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


def _reflection_secrets(injection: InjectedAuth) -> tuple[str, ...]:
    """Return raw and URL-encoded credential values suitable for header redaction."""
    sources = [*injection.query_params.values(), *injection.cookies.values()]
    for value in injection.headers.values():
        sources.append(value)
        for scheme in ("Bearer ", "Basic ", "SSWS ", "Token "):
            if value.startswith(scheme):
                sources.append(value[len(scheme) :])
                break
    variants = {
        variant
        for value in sources
        if len(value) >= _MIN_REFLECTION_SECRET_LEN
        for variant in (value, quote(value, safe=""), quote_plus(value))
    }
    return tuple(sorted(variants, key=lambda value: (-len(value), value)))


def apply_injection(
    headers: Mapping[str, str],
    url: str,
    injection: InjectedAuth,
    *,
    inbound_cookie: str | None = None,
    inbound_cookie_scoped: bool = False,
    server_variable_defaults: Mapping[str, str] | None = None,
    actor_id: str | None = None,
    credential_id: str | None = None,
    scope_key: str | None = None,
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
      injected cookie name or scoped to a different upstream origin or actor,
      then appends the cookie credentials. Any ``Cookie`` already in ``headers``
      is consolidated so the header is never sent twice.
    """
    set_current_reflection_guard(
        ReflectionGuard(
            query_param_names=frozenset(injection.query_params),
            secrets=_reflection_secrets(injection),
            credential_cookies=tuple(injection.cookies.items()),
        )
    )
    if injection.server_variables or server_variable_defaults:
        url = apply_server_variables(url, injection.server_variables, server_variable_defaults)

    if injection.query_params:
        parsed = urlparse(url)
        url = urlunparse(
            parsed._replace(
                query=_strip_colliding_query_params(parsed.query, injection.query_params)
            )
        )

    set_current_upstream_url(url)
    nominated = _connection_nominated_from_mapping(headers)
    if "cookie" in nominated:
        effective_inbound: str | None = None
    elif inbound_cookie is not None:
        effective_inbound = inbound_cookie
    else:
        cookie_vals = [v for k, v in headers.items() if k.lower() == "cookie"]
        effective_inbound = "; ".join(cookie_vals) or None

    injected = dict(injection.headers)
    cookie = _merge_cookie(
        effective_inbound,
        injection.cookies,
        target_url=url,
        allow_untracked=inbound_cookie_scoped and inbound_cookie is not None,
        actor_id=actor_id,
        credential_id=credential_id,
        scope_key=scope_key,
    )
    if cookie:
        injected["Cookie"] = cookie
    overridden = {name.lower() for name in injected} | {"cookie", "connection"} | nominated
    out = {name: value for name, value in headers.items() if name.lower() not in overridden}
    out.update(injected)
    return url, out


def _merge_cookie(
    inbound: str | None,
    cookies: Mapping[str, str],
    *,
    target_url: str = "",
    allow_untracked: bool = False,
    actor_id: str | None = None,
    credential_id: str | None = None,
    scope_key: str | None = None,
) -> str | None:
    """Filtered ``inbound`` followed by the ``name=value`` cookie credentials, or ``None``."""
    filtered_inbound = filter_inbound_cookie(
        inbound,
        target_url,
        excluded_names=set(cookies.keys()),
        allow_untracked=allow_untracked,
        actor_id=actor_id,
        credential_id=credential_id,
        scope_key=scope_key,
    )
    parts = [filtered_inbound] if filtered_inbound else []
    parts.extend(f"{name}={value}" for name, value in cookies.items())
    return "; ".join(parts) or None
