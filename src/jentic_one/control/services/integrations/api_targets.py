"""Scheme selection, host pinning and the review digest for API-target connect sessions.

Pure helpers over the registry's :class:`ApiSecurityView`: the spec — never
the agent or the approver — decides which credential a session collects and
where it may be injected.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from jentic_one.control.services.integrations.errors import (
    AuthTypeNotDeclaredError,
    AuthTypeRequiredError,
    NoDeclaredSchemeError,
    ReservedAuthFieldError,
    UnpinnedServerHostError,
)
from jentic_one.shared.catalog import ApiSecurityView, DeclaredSecurityScheme

#: Declared-scheme kinds a connect session can serve.
SCHEME_API_KEY = "api_key"
SCHEME_BEARER = "bearer"
SCHEME_BASIC = "basic"
SCHEME_OAUTH2 = "oauth2"

_API_KEY_LOCATIONS = frozenset({"header", "query", "cookie"})

# Headers the platform sets or that steer the request itself; an API key may
# not be injected into them. ``Authorization`` is reserved for API keys only —
# bearer and basic schemes own it. Covers the hop-by-hop headers (RFC 9110
# §7.6.1), the forwarding headers the broker strips as spoofable, and the
# trace-context headers the broker writes on every outbound call, so a declared
# key can never collide with, or be dropped as, one of those.
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "cookie",
        "authorization",
        "proxy-authorization",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "forwarded",
        "x-real-ip",
        "via",
        "traceparent",
        "tracestate",
        "baggage",
    }
)
_RESERVED_HEADER_PREFIXES = ("x-forwarded-", "jentic-", "x-jentic-")


@dataclass(frozen=True, slots=True)
class SelectedScheme:
    """The declared scheme a session collects a credential for."""

    name: str
    kind: str
    location: str | None
    field_name: str | None
    oauth_scopes: tuple[str, ...] = ()
    authorization_url: str | None = None
    token_url: str | None = None


def scheme_kind(scheme: DeclaredSecurityScheme) -> str | None:
    """The connect-session kind of a declared scheme, or ``None`` when unsupported.

    OpenID Connect, mutual TLS, digest and other HTTP schemes are not served.
    """
    if scheme.type == "apiKey":
        if scheme.location in _API_KEY_LOCATIONS and scheme.field_name:
            return SCHEME_API_KEY
        return None
    if scheme.type == "http":
        if scheme.http_scheme == "bearer":
            return SCHEME_BEARER
        if scheme.http_scheme == "basic":
            return SCHEME_BASIC
        return None
    if scheme.type == "oauth2":
        return SCHEME_OAUTH2
    return None


def _selected(scheme: DeclaredSecurityScheme, kind: str) -> SelectedScheme:
    if kind == SCHEME_API_KEY:
        location, field_name = scheme.location, scheme.field_name
    elif kind in (SCHEME_BEARER, SCHEME_BASIC):
        location, field_name = "header", "Authorization"
    else:
        location, field_name = None, None
    return SelectedScheme(
        name=scheme.name,
        kind=kind,
        location=location,
        field_name=field_name,
        oauth_scopes=scheme.oauth_scopes,
        authorization_url=scheme.authorization_url,
        token_url=scheme.token_url,
    )


def is_reserved_header(field_name: str) -> bool:
    lowered = field_name.strip().lower()
    return lowered in _RESERVED_HEADERS or lowered.startswith(_RESERVED_HEADER_PREFIXES)


def select_scheme(view: ApiSecurityView, auth_type: str | None) -> SelectedScheme:
    """Pick the declared scheme a session collects.

    ``auth_type`` (optional, the agent's proposal) matches a declared scheme by
    name first, then by kind (``api_key`` / ``bearer`` / ``basic`` /
    ``oauth2``). Without it, a single usable scheme is used; several OAuth
    schemes count as one (the first by name); any other mix needs
    ``auth_type``. An API-key scheme that targets a reserved header is refused.
    """
    usable = [(s, kind) for s in view.schemes if (kind := scheme_kind(s)) is not None]
    if not usable:
        raise NoDeclaredSchemeError(view.vendor, view.name, view.version)
    options = sorted(s.name for s, _ in usable)

    if auth_type is not None:
        matches = [(s, k) for s, k in usable if s.name == auth_type] or [
            (s, k) for s, k in usable if k == auth_type
        ]
        if not matches:
            raise AuthTypeNotDeclaredError(auth_type, options)
        if len(matches) > 1 and {k for _, k in matches} != {SCHEME_OAUTH2}:
            raise AuthTypeRequiredError(sorted(s.name for s, _ in matches))
    elif len(usable) == 1 or {k for _, k in usable} == {SCHEME_OAUTH2}:
        matches = usable
    else:
        raise AuthTypeRequiredError(options)

    scheme, kind = sorted(matches, key=lambda m: m[0].name)[0]
    selected = _selected(scheme, kind)
    if (
        selected.kind == SCHEME_API_KEY
        and selected.location == "header"
        and selected.field_name is not None
        and is_reserved_header(selected.field_name)
    ):
        raise ReservedAuthFieldError(selected.field_name)
    return selected


def require_pinnable_hosts(view: ApiSecurityView) -> None:
    """Refuse an API whose server host comes from a variable with no ``enum``."""
    if view.unpinned_host_variables:
        raise UnpinnedServerHostError(list(view.unpinned_host_variables))


def scheme_still_declared(
    view: ApiSecurityView, *, kind: str | None, location: str | None, field_name: str | None
) -> bool:
    """Whether the live revision still declares a scheme matching the snapshot."""
    for scheme in view.schemes:
        declared = scheme_kind(scheme)
        if declared is None or declared != kind:
            continue
        selected = _selected(scheme, declared)
        if selected.location == location and selected.field_name == field_name:
            return True
    return False


def oauth_scopes_of(view: ApiSecurityView) -> list[str]:
    """Every OAuth scope any declared OAuth scheme lists, in first-seen order."""
    scopes: list[str] = []
    for scheme in view.schemes:
        if scheme_kind(scheme) != SCHEME_OAUTH2:
            continue
        for name in scheme.oauth_scopes:
            if name not in scopes:
                scopes.append(name)
    return scopes


def oauth_endpoints_of(view: ApiSecurityView) -> tuple[str | None, str | None]:
    """The first declared authorization-code flow's ``(authorization_url, token_url)``."""
    for scheme in sorted(view.schemes, key=lambda s: s.name):
        if scheme_kind(scheme) == SCHEME_OAUTH2 and scheme.authorization_url:
            return scheme.authorization_url, scheme.token_url
    return None, None


def review_digest(payload: Mapping[str, object]) -> str:
    """Stable SHA-256 over the review fields an approver decides on."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def missing_scopes(requested: Iterable[str], granted: Iterable[str]) -> list[str]:
    """Requested scopes the grant does not include, sorted."""
    have = set(granted)
    return sorted({s for s in requested if s not in have})
