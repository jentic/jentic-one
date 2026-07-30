"""Shared URL utilities for server-variable substitution."""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Bind/loopback hosts that all denote "this host" for origin-comparison
# purposes. Binding ``0.0.0.0`` (all interfaces) is routinely reached over
# ``127.0.0.1``/``localhost``, so a configured public URL on any of these must
# not be flagged as a mismatch against a ``0.0.0.0`` bind.
_EQUIVALENT_HOSTS = frozenset({"0.0.0.0", "127.0.0.1", "localhost", "::1"})

# A ``{name}`` OpenAPI server-variable placeholder. Names follow the OpenAPI
# variable-name grammar (letters, digits, underscores, hyphens, dots) so an
# empty ``{}`` or a stray brace in a query value is not mistaken for one.
_SERVER_VAR_PLACEHOLDER = re.compile(r"\{[A-Za-z0-9_.-]+\}")


def apply_server_variables(url: str, variables: dict[str, str]) -> str:
    """Substitute OpenAPI server-variable values into a URL template.

    Each ``{name}`` placeholder in the URL is replaced with the URL-encoded
    value from *variables*. Unmatched placeholders (e.g. path parameters
    resolved elsewhere) are left intact.
    """
    result = url
    for name, value in variables.items():
        placeholder = "{" + name + "}"
        result = result.replace(placeholder, quote(value, safe=""))
    return result


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
