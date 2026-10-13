"""Canonical example values for OpenAPI path, query and header parameters.

Routers attach these through FastAPI's ``openapi_examples=`` keyword, which
renders a Parameter-level ``examples`` map (``{name: {value: ...}}``) — the
form API linters and doc renderers read. (``examples=[...]`` would land inside
the parameter's ``schema`` instead, which parameter-example lint rules ignore.)

Identifier values follow the real wire formats so a reader can tell one entity
from another at a glance: KSUID-style ``<prefix>_<8-hex-seconds><16-hex>`` ids
(see ``shared/db/ids.py``), UUID revision ids, and the base64 ``{t, id}``
pagination cursor (see ``shared/pagination.py``). The values are illustrative
only — none of them resolve against a real deployment.

``tests/arch/test_openapi_metadata.py`` asserts every parameter in the
control-plane spec carries an example and that each example validates against
the parameter's schema.
"""

from __future__ import annotations

from fastapi.openapi.models import Example


def example(value: object, summary: str | None = None) -> dict[str, Example]:
    """Build a single-entry ``openapi_examples`` map for a parameter."""
    entry: Example = {"value": value}
    if summary is not None:
        entry["summary"] = summary
    return {"example": entry}


# --- Entity identifiers (KSUID-style unless noted) ---------------------------

AGENT_ID = "agnt_6968b3984d1c238dbe7e2e29"
USER_ID = "usr_6968b3987ee4c3ec0b4ee3b9"
CREDENTIAL_ID = "cred_6968b39855d91a3561684b32"
RULE_SET_ID = "prs_6968b3987c22b63e87e79f05"
OVERLAY_ID = "ovr_6968b398ae1c9a6e2fe6520a"
CONNECT_SESSION_ID = "cs_6968b3983b8b91c75627bee5"
OAUTH_APP_REGISTRATION_ID = "oar_6968b39821e5f89a6975f501"
OAUTH_CLIENT_RECORD_ID = "oac_6968b39840a935d9f7e2714b"
OAUTH_GRANT_ID = "ocg_6968b3985e21c5fdf643025d"
EXECUTION_ID = "exec_6968b3982706c619fe73f0cf"
EVENT_ID = "evt_6968b398b04f002b6dbff4d1"
AUDIT_ID = "aud_6968b3984275d71e90f7f09f"
JOB_ID = "job_6968b3985e8c9902207afaeb"
NOTE_ID = "note_6968b398edb465624291e405"
TOOLKIT_ID = "tk_6968b3981ec22d56dbbc91e8"
REVISION_ID = "3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c"  # registry revisions are UUIDs
# Public OAuth ``client_id`` (``oc_`` + 32 url-safe chars), distinct from the
# ``oac_`` record id used in admin paths.
OAUTH_PUBLIC_CLIENT_ID = "oc_Xk3vQ9mT2pLw8RzN5bYc1HdF7gJs4AeU"  # pragma: allowlist secret
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"  # W3C trace-context trace id

# --- Registry API identity ---------------------------------------------------

API_VENDOR = "stripe.com"
API_NAME = "stripe"
API_VERSION = "2024-04-10"
API_REF = f"{API_VENDOR}:{API_NAME}:{API_VERSION}"  # ``vendor[:name[:version]]`` filter
API_VENDOR_NAME = f"{API_VENDOR}/{API_NAME}"  # ``vendor/name`` monitoring filter
CATALOG_API_ID = "stripe.com"  # catalog manifest identity: ``domain[/sub-api]``
OPERATION_ID = "listCharges"
OPERATION_METHOD_URL = "GET https://api.stripe.com/v1/charges"
VENDOR_KEY = "github"  # vendor-registry key (connect flow / auth capabilities)
PROVIDER_NAME = "pipedream"  # credential provider config name

# --- Pagination, time windows, conditional requests --------------------------

# ``encode_cursor(2026-01-15T09:30:00Z, REVISION_ID)`` — opaque to clients.
CURSOR = "eyJ0IjogIjIwMjYtMDEtMTVUMDk6MzA6MDArMDA6MDAiLCAiaWQiOiAiM2YyYjhjMWUtN2Q0YS00ZTliLWE2YzUtMWQyZTNmNGE1YjZjIn0="  # noqa: E501  # pragma: allowlist secret
DATETIME_FROM = "2026-01-01T00:00:00Z"
DATETIME_TO = "2026-01-31T23:59:59Z"
EPOCH_SINCE = 1767225600  # 2026-01-01T00:00:00Z, Unix seconds
EPOCH_UNTIL = 1769903999  # 2026-01-31T23:59:59Z, Unix seconds
NOTE_ETAG = '"3"'  # notes ETag: quoted revision counter
GOVERNED_HOSTS_ETAG = (
    '"9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"'  # pragma: allowlist secret
)

# --- OAuth / OIDC (RFC 6749, RFC 7636, OIDC Core) ----------------------------

REDIRECT_URI = "https://client.example.com/oauth/callback"
OAUTH_STATE = "af0ifjsldkj"
OIDC_NONCE = "n-0S6_WzA2Mj"
# RFC 7636 Appendix B worked example.
PKCE_CODE_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # pragma: allowlist secret
AUTHORIZATION_CODE = "SplxlOBeZQQYbYS6WxSbIA"  # RFC 6749 §4.1.2 example  # pragma: allowlist secret
# Signed flow blobs: ``<urlsafe-b64 JSON>.<hex HMAC>``. Truncated, illustrative.
SIGNED_FLOW_STATE = (
    "eyJfcHVycG9zZSI6ICJleGFtcGxlIn0=.5d41402abc4b2a76b9719d911017c592"  # pragma: allowlist secret
)
# Opaque ``secrets.token_urlsafe(32)`` handles (consent handle, connect poll token).
OPAQUE_TOKEN = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # pragma: allowlist secret
