"""Bounded HTTP client for control-plane calls to IdP and vendor endpoints.

The token, device-authorization, identity-probe and Pipedream endpoints are
operator- or user-supplied, so every exchange with them runs under one total
wall-clock deadline (connect through the last body byte) and a hard cap on the
decoded body. A per-operation httpx timeout alone is not enough: a server that
sends one byte per interval never trips it. Responses are streamed through
:func:`~jentic_one.shared.bounded_read.read_capped_body`, which also bounds the
inflated size of a compressed body, and the request asks for an uncompressed
body.

All failures surface as :class:`IdpTransportError` with a closed ``kind`` and a
message that never carries a response payload.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx

from jentic_one.shared.bounded_read import (
    ResponseTooLargeError,
    UndecodableBodyError,
    decode_body,
    read_capped_body,
)
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.egress import build_always_pinned_transport
from jentic_one.shared.redaction import redact_value
from jentic_one.shared.url_validation import validate_upstream_url_async

IDP_DEADLINE_SECONDS: Final = 20.0
"""Total wall clock for one exchange, from connect to the last body byte."""

IDP_MAX_RESPONSE_BYTES: Final = 256 * 1024
"""Cap on the decoded body of a token / device / identity / Pipedream response."""

_PER_OP_TIMEOUT_SECONDS: Final = 15.0

IdpErrorKind = Literal["unsafe_url", "timeout", "too_large", "undecodable", "transport"]


class IdpTransportError(Exception):
    """An IdP exchange failed before a complete, bounded response was read.

    ``kind`` is a closed taxonomy. The message is the kind, plus a redacted
    detail only for ``unsafe_url``; it never carries a response body.
    """

    def __init__(self, kind: IdpErrorKind, detail: str | None = None) -> None:
        self.kind: IdpErrorKind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


@dataclass(frozen=True, slots=True)
class IdpResponse:
    """A fully read, size-bounded IdP response."""

    status_code: int
    headers: httpx.Headers
    body: bytes
    _encoding: str | None = None

    def text(self) -> str:
        """The body decoded with the response's charset (UTF-8 fallback), lossily."""
        return self.body.decode(self._encoding or "utf-8", errors="replace")

    def json(self) -> Any:
        """The body parsed as JSON; raises ``ValueError`` like ``httpx.Response.json``."""
        return json.loads(self.body)


async def idp_request(
    method: str,
    url: str,
    *,
    egress: EgressConfig | None,
    data: Mapping[str, str] | None = None,
    json_body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    deadline_s: float = IDP_DEADLINE_SECONDS,
    max_bytes: int = IDP_MAX_RESPONSE_BYTES,
) -> IdpResponse:
    """Send one request to an IdP endpoint under a total deadline and body cap.

    The URL is checked against ``egress`` and the connection is DNS-pinned to
    the validated address; redirects are not followed.
    """
    request_headers = dict(headers or {})
    request_headers["Accept-Encoding"] = "identity"
    try:
        async with asyncio.timeout(deadline_s):
            try:
                safe_url = await validate_upstream_url_async(url, egress)
            except ValueError as exc:
                raise IdpTransportError("unsafe_url", _redacted(exc)) from None
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(min(deadline_s, _PER_OP_TIMEOUT_SECONDS)),
                follow_redirects=False,
                transport=build_always_pinned_transport(egress),
            ) as client:
                request = client.build_request(
                    method,
                    safe_url,
                    data=dict(data) if data is not None else None,
                    json=dict(json_body) if json_body is not None else None,
                    headers=request_headers,
                )
                response = await client.send(request, stream=True)
                try:
                    body = await read_capped_body(response, max_bytes)
                    return IdpResponse(
                        status_code=response.status_code,
                        headers=response.headers,
                        body=body,
                        _encoding=response.encoding,
                    )
                finally:
                    await response.aclose()
    except TimeoutError:
        raise IdpTransportError("timeout") from None
    except ResponseTooLargeError:
        raise IdpTransportError("too_large") from None
    except UndecodableBodyError:
        raise IdpTransportError("undecodable") from None
    except IdpTransportError:
        raise
    except ValueError as exc:
        # The pinning transport raises ValueError when the host re-resolves to
        # a blocked address or does not resolve at all.
        raise IdpTransportError("unsafe_url", _redacted(exc)) from None
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise IdpTransportError("transport", type(exc).__name__) from None


def _redacted(exc: BaseException) -> str:
    return str(redact_value(str(exc)))


__all__ = [
    "IDP_DEADLINE_SECONDS",
    "IDP_MAX_RESPONSE_BYTES",
    "IdpResponse",
    "IdpTransportError",
    "decode_body",
    "idp_request",
]
