"""Shared URL utilities: server-variable substitution and public-origin helpers."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from urllib.parse import quote, urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Bind/loopback hosts that all denote "this host" for origin-comparison
# purposes. Binding ``0.0.0.0`` (all interfaces) is routinely reached over
# ``127.0.0.1``/``localhost``, so a configured public URL on any of these must
# not be flagged as a mismatch against a ``0.0.0.0`` bind.
_EQUIVALENT_HOSTS = frozenset({"0.0.0.0", "127.0.0.1", "localhost", "::1"})

# The grammar of a ``{name}`` OpenAPI server-variable placeholder, in its
# literal or percent-encoded (``%7Bname%7D``) form — many HTTP clients encode
# the braces on their own, so both spellings name the same variable and every
# reader of a placeholder here is built from these fragments. Names follow the
# OpenAPI variable-name grammar (letters, digits, underscores, hyphens, dots)
# so an empty ``{}`` or a stray brace in a query value is not mistaken for one.
_SV_OPEN = r"(?:\{|%7[Bb])"
_SV_CLOSE = r"(?:\}|%7[Dd])"
_SV_NAME = r"[A-Za-z0-9_.-]+"

_SERVER_VAR_PLACEHOLDER = re.compile(_SV_OPEN + _SV_NAME + _SV_CLOSE)
_NAMED_SERVER_VAR_PLACEHOLDER = re.compile(_SV_OPEN + "(" + _SV_NAME + ")" + _SV_CLOSE)


def _server_var_placeholder(name: str) -> re.Pattern[str]:
    """Match ``{name}`` in its literal or percent-encoded (``%7Bname%7D``) form."""
    return re.compile(_SV_OPEN + re.escape(name) + _SV_CLOSE)


def decode_server_variable_placeholders(host: str) -> str:
    """Rewrite a percent-encoded server-variable placeholder in *host* to literal braces.

    Only the braces around an OpenAPI variable name are decoded, never any
    other escape. The URL index stores a templated host with literal braces, so
    a caller that encoded them has to be matched against that one spelling.

    A general unescape would be unsafe here: it also folds escapes that *can*
    spell a real DNS label (``%41pi.example.com`` → ``api.example.com``), which
    would route to an operation and decrypt and inject its credential while the
    broker still forwards the raw host — one that then fails to resolve. Braces
    cannot appear in a real label, so decoding only those keeps routing and
    forwarding in agreement: :func:`apply_server_variables` substitutes either
    spelling, and a placeholder nothing substitutes leaves the host
    unresolvable whichever way it was spelled.
    """
    return _NAMED_SERVER_VAR_PLACEHOLDER.sub(r"{\1}", host)


def apply_server_variables(
    url: str,
    variables: Mapping[str, str] | None,
    defaults: Mapping[str, str] | None = None,
) -> str:
    """Substitute OpenAPI server-variable values into a URL template.

    Each ``{name}`` placeholder (literal or percent-encoded) is replaced with
    the URL-encoded value from *variables*; placeholders still left afterwards
    fall back to the declared *defaults*. Only placeholders are rewritten — a
    concrete URL is never changed — and unknown placeholders (e.g. path
    parameters resolved elsewhere) are left intact.
    """
    result = url
    for source in (variables or {}, defaults or {}):
        for name, value in source.items():
            encoded = quote(value, safe="")
            # ``quote(safe="")`` percent-encodes ``\``, so the value is a safe
            # literal ``re.sub`` replacement (no backreference escapes).
            result = _server_var_placeholder(name).sub(encoded, result)
    return result


def server_variables_compatible(
    scope: Mapping[str, str] | None,
    resolved: Mapping[str, str] | None,
    *,
    unresolved: bool = False,
) -> bool:
    """Whether a credential's ``server_variables`` agree with a request URL's values.

    *scope* is the credential's stored ``server_variables``; *resolved* the
    concrete variable values of the request URL. Each scoped variable the URL
    resolves must carry exactly the same value (server variables may sit in a
    case-sensitive path; values resolved from the host arrive lowercased, as
    the request host is normalised). A variable the URL leaves as a
    placeholder, or does not declare, imposes no constraint; an unscoped
    credential matches. *unresolved* means the URL's values could not be
    determined: a scoped credential then never matches (fail closed).
    """
    if not scope:
        return True
    if unresolved:
        return False
    if not resolved:
        return True
    for name, value in scope.items():
        actual = resolved.get(name)
        if actual is not None and actual != value:
            return False
    return True


def has_host_server_variable(url: str) -> bool:
    """Whether *url*'s scheme/host carries an unsubstituted ``{name}`` template.

    A region-split API (e.g. ``https://{region}.posthog.com``) reaches the broker
    with the placeholder still in the **host** because the caller derives the URL
    from the spec's templated server. Only the scheme + netloc are inspected so an
    ordinary path parameter (``/users/{id}``) or a ``{...}`` inside the query never
    triggers a false positive — those are not server variables.
    """
    parts = urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc}" if parts.netloc else url.split("/", 1)[0]
    return bool(_SERVER_VAR_PLACEHOLDER.search(host))


def normalize_base_url(raw: str) -> str:
    """Normalize a configured public base URL.

    Strips a trailing ``/``, requires an ``http``/``https`` scheme and a host,
    rejects embedded userinfo (``user:pass@host`` must never end up in a
    published link), rejects a query string or fragment (a base URL is an origin
    + optional path, not a full request URL — a stray ``?x=1`` would corrupt
    every derived link), and rejects an out-of-range/non-numeric port. A path
    suffix is allowed — a deployment may be mounted under a gateway prefix.
    Raises ``ValueError`` on invalid input so pydantic surfaces it with the
    offending field path (this is where an operator wants to hear about a typo,
    not as a runtime traceback at startup).
    """
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"base URL must be http(s): {raw!r}")
    if not parts.hostname:
        raise ValueError(f"base URL must include a host: {raw!r}")
    if parts.username or parts.password:
        raise ValueError("base URL must not embed userinfo (user:pass@host)")
    if parts.query or parts.fragment:
        raise ValueError(f"base URL must not contain a query or fragment: {raw!r}")
    # ``urlsplit`` defers port parsing to attribute access, which raises for an
    # out-of-range or non-numeric port (``http://h:99999`` / ``http://h:8a``).
    # Touch it here so a bad port fails at config load, not at startup.
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError(f"base URL has an invalid port: {raw!r}") from exc
    return raw.rstrip("/")


def validate_redirect_uri(raw: str) -> str:
    """Validate a configured OAuth ``redirect_uri`` and return it **unchanged**.

    Unlike ``normalize_base_url`` this is a full callback URL that the IdP
    matches byte-for-byte against its registration, so nothing is rewritten: a
    trailing ``/`` is kept, and a query string is allowed (RFC 6749 §3.1.2).
    Requires an ``http``/``https`` scheme and a host, and rejects userinfo, a
    fragment (forbidden by RFC 6749 §3.1.2) and an invalid port.
    """
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"redirect_uri must be http(s): {raw!r}")
    if not parts.hostname:
        raise ValueError(f"redirect_uri must include a host: {raw!r}")
    if parts.username or parts.password:
        raise ValueError("redirect_uri must not embed userinfo (user:pass@host)")
    if parts.fragment:
        raise ValueError(f"redirect_uri must not contain a fragment: {raw!r}")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError(f"redirect_uri has an invalid port: {raw!r}") from exc
    return raw


def _origin(url: str) -> tuple[str, str, int] | None:
    """Return ``(scheme, host, port)`` for *url*, or ``None`` if unparseable.

    Never raises — a non-numeric/out-of-range port (which ``urlsplit(...).port``
    would raise ``ValueError`` on) maps to ``None`` so callers treat it as a
    non-comparable origin rather than crashing.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return None
    try:
        explicit_port = parts.port
    except ValueError:
        return None
    port = explicit_port or _DEFAULT_PORTS.get(parts.scheme, 0)
    return parts.scheme, parts.hostname, port


def origins_equivalent(a: str, b: str) -> bool:
    """Whether two URLs share an origin, folding loopback/bind-host aliases.

    Scheme + host + port must match, except that every host in
    ``_EQUIVALENT_HOSTS`` (``0.0.0.0``/``127.0.0.1``/``localhost``/``::1``) is
    treated as equivalent, and default ports (``80``/``443``) fold into their
    scheme. Unparseable input never compares equal.
    """
    oa, ob = _origin(a), _origin(b)
    if oa is None or ob is None:
        return False
    scheme_a, host_a, port_a = oa
    scheme_b, host_b, port_b = ob
    if scheme_a != scheme_b or port_a != port_b:
        return False
    if host_a == host_b:
        return True
    return host_a in _EQUIVALENT_HOSTS and host_b in _EQUIVALENT_HOSTS


def is_loopback_host(host: str) -> bool:
    """Whether *host* (a bind host or URL hostname) names this machine only.

    ``localhost`` and any ``127.0.0.0/8`` / ``::1`` literal. The all-interfaces
    binds (``0.0.0.0`` / ``::``) are deliberately **not** loopback.
    """
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    """Whether *url*'s hostname is loopback (see ``is_loopback_host``)."""
    hostname = urlsplit(url).hostname
    return bool(hostname) and is_loopback_host(hostname or "")
