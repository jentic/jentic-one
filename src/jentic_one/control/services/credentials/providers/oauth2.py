"""OAuth 2.0 credential-provider base class (RFC 6749 family).

Shared surface for providers whose credentials are OAuth 2.0 tokens: the
type is always ``CredentialType.OAUTH2``, and refresh + code-exchange
both round-trip a token endpoint that returns a JSON token response.

Subclasses live as *siblings* under this base (they are not related to
each other) — one per concrete flow variant:

    * ``DirectOAuth2Provider`` — authorization_code + client_credentials,
      confidential client (client_secret at the token endpoint).
    * ``DeviceAuthorizationConnectProvider`` — RFC 8628 device_code, public
      client (no client_secret).

Managed provider variants (Pipedream) intentionally stay outside this
hierarchy — they don't call an IdP's token endpoint directly, so the
shared ``_post_token`` scaffolding wouldn't apply.
"""

from __future__ import annotations

import json
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import parse_qs

import httpx

from jentic_one.control.services.credentials.providers.base import ProviderError
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectChallenge,
    ConnectRequest,
    ConnectState,
)
from jentic_one.control.services.credentials.schemas.provision import (
    APIReference,
    OAuthTokenView,
    ProvisionResult,
    RefreshResult,
)
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.context import Context
from jentic_one.shared.egress import build_always_pinned_transport
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.redaction import redact_value
from jentic_one.shared.url_validation import validate_upstream_url_async

MAX_OAUTH_EXPIRES_IN_SECONDS: Final[int] = 100 * 365 * 86400
_MAX_OAUTH_ERROR_BODY_CHARS: Final[int] = 8192
_OAUTH_ERROR_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9_.:-]{1,64}$")


class InvalidGrantError(ProviderError):
    """Raised when the IdP rejects a refresh with ``invalid_grant``."""


class TokenExchangeError(ProviderError):
    """Raised when a token-endpoint round-trip fails (non-2xx or non-JSON)."""

    def __init__(self, status: int, body: str = "", *, reason: str = "") -> None:
        self.status = status
        self.body = redact_value(body) if body else ""
        msg = f"Token exchange failed: HTTP {status}"
        if reason:
            msg = f"{msg} ({reason})"
        super().__init__(msg)


def sanitize_oauth_error_slug(raw: object) -> str | None:
    """Return a normalized OAuth error slug when ``raw`` is a safe token, else ``None``."""
    if not isinstance(raw, str):
        return None
    slug = raw.strip().lower()
    if not slug or not _OAUTH_ERROR_SLUG_PATTERN.match(slug):
        return None
    return slug


def extract_oauth_error_code(body: str, content_type: str = "") -> str | None:
    """Extract the structured OAuth ``error`` field from a JSON or form body.

    Inspects only bounded payloads and compares the parsed ``error`` member
    rather than scanning raw body text for substrings.
    """
    if not body or len(body) > _MAX_OAUTH_ERROR_BODY_CHARS:
        return None
    stripped = body.strip()
    if not stripped:
        return None

    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in ("text/html", "application/xhtml+xml") or stripped.startswith("<"):
        return None

    is_json_type = media_type == "application/json" or media_type.endswith("+json")
    is_form_type = media_type == "application/x-www-form-urlencoded"

    if is_json_type or stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return None
        if not isinstance(parsed, dict):
            return None
        return sanitize_oauth_error_slug(parsed.get("error"))

    if is_form_type or (
        not is_json_type and "=" in stripped and "\n" not in stripped and " " not in stripped
    ):
        try:
            params = parse_qs(
                stripped,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=32,
            )
        except ValueError:
            return None
        values = params.get("error")
        if not values or len(values) != 1:
            return None
        return sanitize_oauth_error_slug(values[0])

    return None


def classify_oauth_token_error(
    status_code: int, body: str, content_type: str = ""
) -> ProviderError:
    """Classify a non-200 OAuth token response into terminal vs transient error.

    Rate-limit (429) and server (5xx) responses always take transient precedence
    as :class:`TokenExchangeError`. Client error responses (4xx other than 429)
    raise :class:`InvalidGrantError` only when the structured OAuth ``error``
    field is ``"invalid_grant"``.
    """
    error_code = extract_oauth_error_code(body, content_type)
    if status_code == 429 or status_code >= 500:
        return TokenExchangeError(status_code, error_code or "")
    if 400 <= status_code < 500 and error_code == "invalid_grant":
        return InvalidGrantError("Refresh token has been revoked or expired")
    return TokenExchangeError(status_code, error_code or "")


def parse_oauth_expires_in(raw: object) -> int:
    """Validate and normalize a present OAuth ``expires_in`` value in seconds.

    Callers distinguish an omitted field before invoking this parser. Explicit
    JSON null, boolean, non-numeric, non-positive, non-integral, and unbounded
    values raise :class:`ProviderError`.
    """
    if raw is None:
        raise ProviderError("Invalid expires_in in OAuth token response")
    if isinstance(raw, bool):
        raise ProviderError("Invalid expires_in in OAuth token response")

    seconds: int
    if isinstance(raw, int):
        seconds = raw
    elif isinstance(raw, float):
        if not math.isfinite(raw) or not raw.is_integer():
            raise ProviderError("Invalid expires_in in OAuth token response")
        seconds = int(raw)
    elif isinstance(raw, str):
        stripped = raw.strip()
        if not stripped or len(stripped) > 18:
            raise ProviderError("Invalid expires_in in OAuth token response")
        try:
            seconds = int(stripped, 10)
        except ValueError as exc:
            raise ProviderError("Invalid expires_in in OAuth token response") from exc
    else:
        raise ProviderError("Invalid expires_in in OAuth token response")

    if seconds <= 0 or seconds > MAX_OAUTH_EXPIRES_IN_SECONDS:
        raise ProviderError("Invalid expires_in in OAuth token response")
    return seconds


def true_oauth_expiry(token_data: Mapping[str, Any]) -> datetime | None:
    """Return the access token's UTC expiry from an OAuth token payload."""
    if not isinstance(token_data, Mapping):
        raise ProviderError("Malformed OAuth token response")
    if "expires_in" not in token_data:
        return None
    seconds = parse_oauth_expires_in(token_data.get("expires_in"))
    return datetime.now(UTC) + timedelta(seconds=seconds)


_true_expiry = true_oauth_expiry


def validate_oauth_token_payload(raw_data: object, *, status_code: int = 200) -> dict[str, str]:
    """Validate a 200 OAuth token response object and normalize its fields."""
    if not isinstance(raw_data, dict):
        raise TokenExchangeError(
            status_code, "malformed token response", reason="malformed token response"
        )

    access_token = raw_data.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise TokenExchangeError(status_code, "missing access_token", reason="missing access_token")

    normalized: dict[str, str] = {"access_token": access_token}

    if "expires_in" in raw_data:
        try:
            seconds = parse_oauth_expires_in(raw_data["expires_in"])
        except ProviderError as exc:
            raise TokenExchangeError(
                status_code, "invalid expires_in", reason="invalid expires_in"
            ) from exc
        normalized["expires_in"] = str(seconds)

    for optional_key in ("refresh_token", "scope", "token_type"):
        if optional_key in raw_data and raw_data[optional_key] is not None:
            val = raw_data[optional_key]
            if not isinstance(val, str):
                raise TokenExchangeError(
                    status_code, f"invalid {optional_key}", reason=f"invalid {optional_key}"
                )
            normalized[optional_key] = val

    for key, val in raw_data.items():
        if key not in normalized and key != "expires_in" and isinstance(val, str):
            normalized[key] = val

    return normalized


class OAuth2Provider(ABC):
    """Base for OAuth 2.0 credential providers."""

    name: str

    @property
    def supported_types(self) -> list[CredentialType]:
        return [CredentialType.OAUTH2]

    def supports(self, wire_type: CredentialType) -> bool:
        return wire_type == CredentialType.OAUTH2

    @property
    @abstractmethod
    def managed(self) -> bool: ...

    @property
    def mints_from_account_ref(self) -> bool:
        # The grant lives in ``oauth_tokens``.
        return False

    @abstractmethod
    async def begin_connect(
        self,
        ctx: Context,
        *,
        api: APIReference,
        request: ConnectRequest,
    ) -> ConnectChallenge: ...

    @abstractmethod
    async def complete_connect(
        self,
        ctx: Context,
        *,
        state: ConnectState,
        callback: ConnectCallback,
    ) -> ProvisionResult: ...

    @abstractmethod
    async def refresh(
        self,
        ctx: Context,
        *,
        token: OAuthTokenView,
    ) -> RefreshResult: ...

    async def _post_token(
        self, token_url: str, payload: dict[str, str], *, egress: EgressConfig | None
    ) -> dict[str, str]:
        """POST ``payload`` to ``token_url`` and parse the JSON response.

        ``egress`` is the control-plane egress policy
        (:func:`~jentic_one.shared.config.resolved_control_egress`); both the
        pre-flight URL check and the connect-time DNS pin apply it.

        Maps the two structured failure modes both concrete flows care
        about: an ``invalid_grant`` body (revoked/expired refresh token)
        surfaces as ``InvalidGrantError`` so callers can distinguish it
        from a transient upstream fault; every other non-200 (or a body
        that isn't JSON) becomes ``TokenExchangeError`` carrying the raw
        HTTP status for logging.
        """
        # Defense-in-depth SSRF guard: ``token_url`` comes from the DB
        # (``oauth_client_credentials`` row created at credential-create time,
        # user-supplied). A tampered / misconfigured row must not be able to
        # aim this call at a private / metadata target unless the operator
        # allowlisted it.
        try:
            safe_url = await validate_upstream_url_async(token_url, egress)
        except ValueError as exc:
            raise TokenExchangeError(0, f"unsafe upstream URL: {redact_value(str(exc))}") from exc

        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=build_always_pinned_transport(egress)
            ) as client:
                response = await client.post(
                    safe_url,
                    data=payload,
                    headers={"Accept": "application/json"},
                )
        except ValueError as exc:
            # Raised by the pinning transport: the host re-resolved to a blocked
            # address or did not resolve at all.
            raise TokenExchangeError(0, f"unsafe upstream URL: {redact_value(str(exc))}") from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise TokenExchangeError(0, type(exc).__name__) from None

        if response.status_code != 200:
            raise classify_oauth_token_error(
                response.status_code,
                response.text,
                response.headers.get("content-type", ""),
            )

        try:
            raw_data: object = response.json()
        except ValueError as exc:
            raise TokenExchangeError(response.status_code, "non-JSON token response") from exc
        return validate_oauth_token_payload(raw_data, status_code=response.status_code)
