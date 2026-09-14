"""Generic identity-echo protocol.

After a connect flow completes and a token is vaulted, the platform calls the
vendor's identity endpoint with the new token, extracts a `identity_field`
value (dotted JSON path) from the response, and formats it into
`display_template` (Python str.format). The result is stored on the
`connect_sessions.connected_as` column and returned to the caller.

Everything vendor-specific lives on `VendorIdentityProbeConfig` — this module
has no vendor-hardcoded logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from jentic_one.shared.config import VendorIdentityProbeConfig
from jentic_one.shared.url_validation import validate_upstream_url

_logger = structlog.get_logger(__name__)


class IdentityEchoError(Exception):
    """Base class for identity-echo failures."""


class IdentityEchoAuthError(IdentityEchoError):
    """Probe returned 401/403 — token is bad, revoked, or under-scoped."""


class IdentityEchoFormatError(IdentityEchoError):
    """Response body missing the configured field, or template formatting failed."""


@dataclass(slots=True, frozen=True)
class IdentityEchoResult:
    """Structured identity-echo output."""

    raw: Any
    display: str


async def echo_identity(
    *,
    probe: VendorIdentityProbeConfig,
    access_token: str,
    timeout_seconds: float = 15.0,
) -> IdentityEchoResult:
    """Run the identity probe and return the extracted identity."""
    # Defense-in-depth SSRF guard: ``probe.endpoint`` is operator config, but
    # a misconfigured entry could aim this call at a private / metadata target
    # with the freshly minted bearer token — refuse to send it.
    try:
        safe_url = validate_upstream_url(probe.endpoint)
    except ValueError as exc:
        raise IdentityEchoError(f"unsafe upstream URL: {exc}") from exc

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.request(
            probe.method,
            safe_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
    if response.status_code in (401, 403):
        raise IdentityEchoAuthError(
            f"identity probe rejected the token: HTTP {response.status_code}"
        )
    if response.status_code >= 400:
        raise IdentityEchoError(f"identity probe failed: HTTP {response.status_code}")
    try:
        body: Any = response.json()
    except ValueError as exc:
        raise IdentityEchoFormatError("identity probe returned non-JSON body") from exc

    raw = _lookup_dotted(body, probe.identity_field)
    if raw is None:
        raise IdentityEchoFormatError(
            f"identity probe response missing field: {probe.identity_field!r}"
        )
    try:
        display = probe.display_template.format_map({probe.identity_field: raw, "value": raw})
        # Also support the field's leaf name as a top-level format key so
        # templates like "@{login}" work for `identity_field: "login"`.
        display = probe.display_template.format_map(
            {**_flatten_body_for_template(body), "value": raw}
        )
    except (KeyError, IndexError, ValueError) as exc:
        raise IdentityEchoFormatError(
            f"failed to format display_template={probe.display_template!r}"
        ) from exc

    _logger.info("identity_echo.success", display=display)
    return IdentityEchoResult(raw=raw, display=display)


def _lookup_dotted(obj: Any, path: str) -> Any:
    """Resolve a dotted path like "user.name" through nested dicts/lists.

    Returns None on any missing segment; caller must decide whether that's an
    error (all current callers treat it as one).
    """
    current = obj
    for segment in path.split("."):
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _flatten_body_for_template(body: Any) -> dict[str, Any]:
    """Expose the top-level keys of the response body for str.format lookup.

    Deliberately shallow — the display_template is short and vendor-authored
    (config file), so operators can compose "@{login}" or "{name} ({email})"
    against the fields the vendor's own endpoint returns.
    """
    if isinstance(body, dict):
        return {str(k): v for k, v in body.items()}
    return {}
