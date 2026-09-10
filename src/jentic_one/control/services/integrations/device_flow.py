"""RFC 8628 OAuth 2.0 Device Authorization Grant implementation.

Implements the two upstream calls the platform makes on the agent's behalf:

- :func:`begin_device_flow` — POST to the vendor's device authorization
  endpoint to request a `device_code` + `user_code` pair.
- :func:`poll_device_flow` — POST to the vendor's token endpoint with the
  `device_code`; maps the RFC 8628 error codes into a small enum so
  the caller (ConnectSessionService) can decide what to do next.

Device flow is a public-client flow — no client secret is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
import structlog

_logger = structlog.get_logger(__name__)


class DeviceFlowError(Exception):
    """Base class for device-flow upstream errors."""


class DeviceFlowUpstreamError(DeviceFlowError):
    """Vendor returned a non-2xx status we didn't expect."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"vendor returned HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


@dataclass(slots=True, frozen=True)
class BeginResult:
    """RFC 8628 device authorization response."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


PollStatus = Literal["pending", "slow_down", "denied", "expired", "success"]


@dataclass(slots=True, frozen=True)
class PollResult:
    """One vendor poll attempt, mapped into a status enum.

    - `pending`  = authorization_pending (RFC 8628 §3.5)
    - `slow_down` = slow_down (caller must widen interval)
    - `denied`   = access_denied (user rejected at vendor)
    - `expired`  = expired_token (device_code TTL exceeded)
    - `success`  = access_token issued
    """

    status: PollStatus
    access_token: str | None = None
    refresh_token: str | None = None
    scope: str | None = None
    expires_in: int | None = None
    token_type: str | None = None


async def begin_device_flow(
    *,
    authorization_endpoint: str,
    client_id: str,
    scopes: list[str],
    timeout_seconds: float = 15.0,
) -> BeginResult:
    """Start a device flow session with the vendor.

    RFC 8628 §3.1: POST { client_id, scope } → 200 with device_code, etc.
    """
    payload = {"client_id": client_id}
    if scopes:
        payload["scope"] = " ".join(scopes)

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            authorization_endpoint,
            data=payload,
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        raise DeviceFlowUpstreamError(response.status_code, response.text)
    try:
        data: dict[str, str | int] = response.json()
    except ValueError as exc:
        raise DeviceFlowUpstreamError(response.status_code, response.text) from exc

    try:
        device_code = str(data["device_code"])
        user_code = str(data["user_code"])
        verification_uri = str(data["verification_uri"])
        expires_in = int(data.get("expires_in", 900))
        interval = int(data.get("interval", 5))
    except (KeyError, ValueError) as exc:
        raise DeviceFlowUpstreamError(
            response.status_code, f"malformed response: {data!r}"
        ) from exc

    verification_uri_complete = (
        str(data["verification_uri_complete"]) if "verification_uri_complete" in data else None
    )
    return BeginResult(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_in=expires_in,
        interval=interval,
    )


async def poll_device_flow(
    *,
    token_endpoint: str,
    client_id: str,
    device_code: str,
    timeout_seconds: float = 15.0,
) -> PollResult:
    """Poll the vendor's token endpoint once for the device flow result.

    RFC 8628 §3.4 / §3.5: POST { grant_type, device_code, client_id }.
    - 200 { access_token, ... }  → success
    - 400 { error: authorization_pending } → pending
    - 400 { error: slow_down }             → slow_down
    - 400 { error: access_denied }         → denied
    - 400 { error: expired_token }         → expired

    Any other non-2xx maps to `DeviceFlowUpstreamError` so the session can
    fail loudly (misconfigured client_id, revoked app, etc.).
    """
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": client_id,
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            token_endpoint,
            data=payload,
            headers={"Accept": "application/json"},
        )
    try:
        data = response.json() if response.text else {}
    except ValueError as exc:
        raise DeviceFlowUpstreamError(response.status_code, response.text) from exc

    if response.status_code == 200 and "access_token" in data:
        return PollResult(
            status="success",
            access_token=str(data["access_token"]),
            refresh_token=(str(data["refresh_token"]) if "refresh_token" in data else None),
            scope=str(data["scope"]) if "scope" in data else None,
            expires_in=int(data["expires_in"]) if "expires_in" in data else None,
            token_type=str(data["token_type"]) if "token_type" in data else None,
        )

    error = str(data.get("error", "")).lower() if isinstance(data, dict) else ""
    if error == "authorization_pending":
        return PollResult(status="pending")
    if error == "slow_down":
        return PollResult(status="slow_down")
    if error == "access_denied":
        return PollResult(status="denied")
    if error == "expired_token":
        return PollResult(status="expired")

    _logger.warning(
        "device_flow.unexpected_poll_response",
        status=response.status_code,
        body_snippet=response.text[:200],
    )
    raise DeviceFlowUpstreamError(response.status_code, response.text)
