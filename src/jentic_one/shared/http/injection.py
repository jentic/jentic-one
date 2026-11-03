"""Apply injected upstream credentials to an outbound request.

The one implementation the sync broker router and the async job worker share,
so the same call reaches the upstream with the same URL and headers whether it
runs inline or from a queued job.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse, urlunparse

from jentic_one.shared.url import apply_server_variables

if TYPE_CHECKING:
    # Annotation only: ``shared.jobs`` imports this module from its worker, so
    # a runtime import here would be circular.
    from jentic_one.shared.jobs.protocols import InjectedAuth


def apply_injection(
    headers: Mapping[str, str],
    url: str,
    injection: InjectedAuth,
    *,
    inbound_cookie: str | None,
    server_variable_defaults: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Return the outbound ``(url, headers)`` with ``injection`` applied.

    - Server-variable credentials are substituted into the URL template;
      placeholders still left fall back to ``server_variable_defaults``.
    - Query-param credentials are appended to the URL query.
    - ``headers`` (the caller's forwarded or replayed headers) are overlaid by
      the injected headers; an injected name wins in any letter case.
    - The outbound ``Cookie`` is ``inbound_cookie`` with the cookie credentials
      **appended**, so the caller's own cookies always reach the upstream.
      Any ``Cookie`` already in ``headers`` is dropped: ``inbound_cookie`` is
      the single source, so the header is never sent twice.
    """
    if injection.server_variables or server_variable_defaults:
        url = apply_server_variables(url, injection.server_variables, server_variable_defaults)

    if injection.query_params:
        parsed = urlparse(url)
        sep = "&" if parsed.query else ""
        url = urlunparse(
            parsed._replace(query=parsed.query + sep + urlencode(injection.query_params))
        )

    injected = dict(injection.headers)
    cookie = _merge_cookie(inbound_cookie, injection.cookies)
    if cookie:
        injected["Cookie"] = cookie
    overridden = {name.lower() for name in injected} | {"cookie"}
    out = {name: value for name, value in headers.items() if name.lower() not in overridden}
    out.update(injected)
    return url, out


def _merge_cookie(inbound: str | None, cookies: Mapping[str, str]) -> str | None:
    """``inbound`` followed by the ``name=value`` cookie credentials, or ``None``."""
    parts = [inbound.strip()] if inbound and inbound.strip() else []
    parts.extend(f"{name}={value}" for name, value in cookies.items())
    return "; ".join(parts) or None
