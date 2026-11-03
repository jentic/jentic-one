"""Live, ingestable OpenAPI spec describing the real harness operations.

Unlike the curated/poisoned specs in ``specs.py``, this document's ``servers[].url``
points at the harness's own deployed URL (``SMOKE_UPSTREAM_PUBLIC_URL``) and its
``/auth/*`` operations declare ``securitySchemes`` so the broker injects the
matching credential. It is the spec a smoke-test agent ingests to drive real
broker->upstream execution.

The ``servers[].url`` is read **per request** (not at import) so the same image
serves the right address in any environment: ``http://localhost:8084`` for local
standalone runs, ``http://jentic-smoke-upstream:8084`` in the kind cluster.
"""

from __future__ import annotations

import os
from typing import Any, Final
from urllib.parse import urlsplit

from fastapi import APIRouter

router = APIRouter(tags=["live-spec"])

#: Env var that overrides the advertised ``servers[].url`` base.
PUBLIC_URL_ENV: Final = "SMOKE_UPSTREAM_PUBLIC_URL"
#: Default base URL when ``SMOKE_UPSTREAM_PUBLIC_URL`` is unset (local run).
DEFAULT_PUBLIC_URL: Final = "http://localhost:8084"
#: Env var that overrides the advertised ``https://`` base URL of the OAuth2 token stub.
TLS_PUBLIC_URL_ENV: Final = "SMOKE_UPSTREAM_TLS_PUBLIC_URL"
#: Default TLS listen port when deriving the token stub URL from ``_server_url()``.
DEFAULT_TLS_PORT: Final = 8443
#: Default ``tokenUrl`` base when ``SMOKE_UPSTREAM_TLS_PUBLIC_URL`` is unset.
DEFAULT_TLS_PUBLIC_URL: Final = f"https://localhost:{DEFAULT_TLS_PORT}"
#: Path the live spec is served at (the agent ingests this URL).
LIVE_SPEC_PATH: Final = "/specs/live.json"
#: Path of the OAuth2 client-credentials token stub.
OAUTH_TOKEN_PATH: Final = "/oauth/token"

#: The security scheme names declared in the spec (tests assert on these).
SECURITY_SCHEME_NAMES: Final = (
    "bearerAuth",
    "basicAuth",
    "apiKeyAuth",
    "appIdAuth",
    "oauth2Auth",
)

#: A fake token the OAuth stub returns; the harness ``/auth/oauth2`` op only
#: checks for a ``Bearer `` prefix, so the exact value is irrelevant.
OAUTH_STUB_TOKEN: Final = "smoke-oauth-token"  # pragma: allowlist secret


def _server_url() -> str:
    """The advertised upstream base URL, read per request from the environment."""
    return os.environ.get(PUBLIC_URL_ENV, DEFAULT_PUBLIC_URL).rstrip("/")


def _token_server_url() -> str:
    """The advertised HTTPS base URL for the OAuth2 token stub (#1779).

    OAuth2 ``token_url`` endpoints require ``https://``, so the token stub is
    advertised on the harness's TLS listener (``SMOKE_UPSTREAM_TLS_PUBLIC_URL``,
    or ``_server_url()`` rewritten to ``https://<host>:<tls_port>``).
    """
    explicit = os.environ.get(TLS_PUBLIC_URL_ENV, "").strip()
    if explicit:
        return explicit.rstrip("/")
    parsed = urlsplit(_server_url())
    host = parsed.hostname or "localhost"
    tls_port = int(os.environ.get("TLS_PORT", str(DEFAULT_TLS_PORT)))
    return f"https://{host}:{tls_port}"


def _security_schemes() -> dict[str, Any]:
    return {
        "bearerAuth": {"type": "http", "scheme": "bearer"},
        "basicAuth": {"type": "http", "scheme": "basic"},
        "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
        "appIdAuth": {"type": "apiKey", "in": "header", "name": "X-App-Id"},
        "oauth2Auth": {
            "type": "oauth2",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": f"{_token_server_url()}{OAUTH_TOKEN_PATH}",
                    "scopes": {},
                }
            },
        },
    }


def _op(
    operation_id: str,
    summary: str,
    description: str,
    *,
    method: str = "get",
    security: list[dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Build a minimal OpenAPI operation entry keyed by HTTP method.

    Only ``operationId``/``summary``/``description``/``security`` plus a trivial
    ``200`` response are needed: the registry builds its URL index and search
    embeddings from the operation text + path + servers, not the response schema.
    """
    op: dict[str, Any] = {
        "operationId": operation_id,
        "summary": summary,
        "description": description,
        "responses": {"200": {"description": "ok"}},
    }
    if security is not None:
        op["security"] = security
    return {method: op}


def _paths() -> dict[str, Any]:
    return {
        # --- Auth operations: each declares the scheme the broker must inject. ---
        "/auth/bearer": _op(
            "authBearer",
            "Bearer-protected echo",
            "Returns 200 only when a Bearer token is present; exercises broker "
            "bearer-token credential injection.",
            security=[{"bearerAuth": []}],
        ),
        "/auth/basic": _op(
            "authBasic",
            "Basic-auth-protected echo",
            "Returns 200 only when HTTP Basic credentials are present; exercises "
            "broker basic-auth credential injection.",
            security=[{"basicAuth": []}],
        ),
        "/auth/api-key": _op(
            "authApiKey",
            "API-key-protected echo",
            "Returns 200 only when the X-Api-Key header is present; exercises broker "
            "api-key credential injection.",
            security=[{"apiKeyAuth": []}],
        ),
        "/auth/oauth2": _op(
            "authOauth2",
            "OAuth2-protected echo",
            "Returns 200 only when a Bearer token is present; exercises broker "
            "OAuth2 client-credentials token acquisition and injection.",
            security=[{"oauth2Auth": []}],
        ),
        "/auth/complex": _op(
            "authComplex",
            "Multi-scheme protected echo",
            "Returns 200 only when both the X-Api-Key and X-App-Id headers are "
            "present; exercises multi-scheme credential injection.",
            security=[{"apiKeyAuth": [], "appIdAuth": []}],
        ),
        # --- Behaviour / domain operations executed by the smoke tests. ---
        "/behavior/echo": _op(
            "echo",
            "Echo the request",
            "Reflects the method, headers, query params, and body back to the "
            "caller so tests can assert the broker forwarded the request intact.",
            method="post",
        ),
        "/pagination/cursor": _op(
            "paginationCursor",
            "Cursor pagination",
            "Paginates a fixed dataset using an opaque cursor token.",
        ),
        "/pagination/links": _op(
            "paginationLinks",
            "Link-header pagination",
            "Paginates a fixed dataset using RFC 8288 Link response headers.",
        ),
        "/pagination/offset": _op(
            "paginationOffset",
            "Offset pagination",
            "Paginates a fixed dataset using limit and offset query parameters.",
        ),
        "/drift/status-code": _op(
            "driftStatus",
            "Undocumented status code",
            "Returns a status code its spec would not declare (418) to verify the "
            "broker passes undocumented statuses through.",
        ),
        "/drift/content-type": _op(
            "driftContentType",
            "Undocumented content type",
            "Returns plain text where JSON is declared to verify the broker does "
            "not choke on an unexpected content type.",
        ),
        "/drift/schema": _op(
            "driftSchema",
            "Undocumented body shape",
            "Returns a body whose shape contradicts the declared schema.",
        ),
        "/edge/binary": _op(
            "edgeBinary",
            "Binary payload echo",
            "Accepts a raw binary body and returns its sha256 and byte length so "
            "tests can verify the broker forwarded the bytes uncorrupted.",
            method="post",
        ),
        "/edge/chunked": _op(
            "edgeChunked",
            "Chunked streaming response",
            "Streams a 1 MiB chunked response so tests can verify the broker "
            "forwards a large streamed body.",
            method="post",
        ),
        "/edge/empty-204": _op(
            "edgeEmpty",
            "Empty 204 response",
            "Returns 204 with an empty body.",
        ),
        "/edge/html-error": _op(
            "edgeHtmlError",
            "HTML error body",
            "Returns a 502 with a large HTML error body to verify verbatim "
            "passthrough of a non-JSON error payload.",
        ),
        "/parameters/query/pipe": _op(
            "paramPipe",
            "Pipe-delimited query array",
            "Echoes a pipeDelimited query array so tests can verify the broker "
            "forwarded the serialization unchanged.",
        ),
        "/parameters/query/deep-object": _op(
            "paramDeepObject",
            "Deep-object query parameter",
            "Echoes a deepObject query parameter so tests can verify the broker "
            "forwarded the serialization unchanged.",
        ),
        "/limits/query-array": _op(
            "limitsQueryArray",
            "Repeated query parameters",
            "Echoes repeated query parameters so tests can push toward the broker "
            "URL-length ceiling.",
        ),
        "/lifecycle/deprecated-endpoint": _op(
            "lifecycleDeprecated",
            "Deprecated endpoint",
            "Returns Deprecation and Sunset response headers (RFC 8594) to verify "
            "the broker passes upstream lifecycle headers through verbatim.",
        ),
        "/servers/resolution": _op(
            "serversResolution",
            "Server resolution echo",
            "Echoes the host and path the request arrived with so tests can verify "
            "broker server-URL resolution.",
        ),
    }


@router.get(LIVE_SPEC_PATH)
async def live_spec() -> dict[str, Any]:
    """Serve the live, ingestable OpenAPI 3.1 spec for the harness."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Smoke Upstream (live)",
            "version": "1.0.0",
            "description": (
                "Instrumented HTTP mirror used as a real upstream for broker "
                "end-to-end smoke tests. Operations exercise credential injection, "
                "pagination, response drift, transport edge cases, parameter "
                "serialization, and lifecycle headers through the broker proxy."
            ),
            "x-vendor": "smoke-upstream",
        },
        "servers": [{"url": _server_url()}],
        "paths": _paths(),
        "components": {"securitySchemes": _security_schemes()},
    }


@router.post(OAUTH_TOKEN_PATH)
async def oauth_token() -> dict[str, Any]:
    """Stub OAuth2 client-credentials token endpoint.

    Lets the broker's client-credentials refresh succeed; the harness
    ``/auth/oauth2`` endpoint only checks for a ``Bearer `` prefix, so any token
    passes. Not part of the ingested spec's operations.
    """
    return {
        "access_token": OAUTH_STUB_TOKEN,
        "token_type": "Bearer",
        "expires_in": 3600,
    }
