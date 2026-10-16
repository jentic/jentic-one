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
    """Build a single-entry ``openapi_examples`` map for a parameter.

    ``summary`` defaults to the label of the canonical constant ``value`` is
    (see ``_VALUE_SUMMARIES``). Any other value gets one from the parameter it
    is attached to, when the spec is generated (``install_openapi_metadata``).
    """
    entry: Example = {"value": value}
    if summary is None and isinstance(value, str):
        summary = _VALUE_SUMMARIES.get(value)
    if summary is not None:
        entry["summary"] = summary
    return {"example": entry}


# --- Entity identifiers (KSUID-style unless noted) ---------------------------

AGENT_ID = "agnt_6968b3984d1c238dbe7e2e29"
USER_ID = "usr_6968b3987ee4c3ec0b4ee3b9"
CREDENTIAL_ID = "cred_6968b39855d91a3561684b32"
CREDENTIAL_BINDING_ID = "acb_6968b3989a4f1c2e7d3b5a60"  # direct agent↔credential binding
AGENT_API_KEY_ID = "agc_6968b398c1d2e3f4a5b6c7d8"  # agent API-key record (not the key)
RULE_SET_ID = "prs_6968b3987c22b63e87e79f05"
PERMISSION_RULE_ID = "apr_6968b3987c22b63e87e79f06"
OVERLAY_ID = "ovr_6968b398ae1c9a6e2fe6520a"
CONNECT_SESSION_ID = "cs_6968b3983b8b91c75627bee5"
CONNECT_SESSION_REVISION_ID = "csr_6968b3983b8b91c75627bee6"
OAUTH_APP_REGISTRATION_ID = "oar_6968b39821e5f89a6975f501"
OAUTH_CLIENT_RECORD_ID = "oac_6968b39840a935d9f7e2714b"
OAUTH_GRANT_ID = "ocg_6968b3985e21c5fdf643025d"
EXECUTION_ID = "exec_6968b3982706c619fe73f0cf"
EVENT_ID = "evt_6968b398b04f002b6dbff4d1"
AUDIT_ID = "aud_6968b3984275d71e90f7f09f"
JOB_ID = "job_6968b3985e8c9902207afaeb"
HELD_EXECUTION_ID = "held_6968b398a1c4e2d07b5f93c6"
NOTE_ID = "note_6968b398edb465624291e405"
TOOLKIT_ID = "tk_6968b3981ec22d56dbbc91e8"
REVISION_ID = "3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c"  # registry revisions are UUIDs
# A later revision of the same API (e.g. the one an overlay materialized).
NEXT_REVISION_ID = "6992059c-0abd-4d22-bd07-5287e1bf5e6a"
REGISTRY_API_ID = "debfc9ff-7c37-4a09-801a-575bd4963ddd"  # registry API rows are UUIDs
# Registry operation id: ``op_`` + 40 hex of sha256(revision:path:method).
REGISTRY_OPERATION_ID = "op_f92d69f1621f381bce8aaff47053c61b069ffe34"
# Public OAuth ``client_id`` (``oc_`` + 32 url-safe chars), distinct from the
# ``oac_`` record id used in admin paths.
OAUTH_PUBLIC_CLIENT_ID = "oc_Xk3vQ9mT2pLw8RzN5bYc1HdF7gJs4AeU"  # pragma: allowlist secret
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"  # W3C trace-context trace id
REQUEST_ID = "req_6968b3984d1c238d"  # minted ``req_`` + 16 hex (see ``shared/logging.py``)

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
# Public base URL of a deployment; response ``_links`` are absolute under it.
BASE_URL = "https://jentic.example.com"

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
# Connect-session review digest: unquoted SHA-256 hex of the review payload.
REVIEW_DIGEST = (
    "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"  # pragma: allowlist secret
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

#: ``summary`` labels for the canonical values above, applied by ``example()``.
#: Keyed by value, so two constants that share one (``API_VENDOR`` and
#: ``CATALOG_API_ID``) share a label too.
_VALUE_SUMMARIES: dict[str, str] = {
    AGENT_ID: "Agent ID",
    USER_ID: "User ID",
    CREDENTIAL_ID: "Credential ID",
    CREDENTIAL_BINDING_ID: "Credential binding ID",
    AGENT_API_KEY_ID: "Agent API key record ID",
    RULE_SET_ID: "Permission rule set ID",
    OVERLAY_ID: "Overlay ID",
    CONNECT_SESSION_ID: "Connect session ID",
    OAUTH_APP_REGISTRATION_ID: "OAuth app registration ID",
    OAUTH_CLIENT_RECORD_ID: "OAuth client record ID",
    OAUTH_GRANT_ID: "OAuth consent grant ID",
    EXECUTION_ID: "Execution ID",
    EVENT_ID: "Event ID",
    AUDIT_ID: "Audit entry ID",
    JOB_ID: "Job ID",
    NOTE_ID: "Note ID",
    TOOLKIT_ID: "Toolkit ID",
    REVISION_ID: "Revision ID (UUID)",
    NEXT_REVISION_ID: "Later revision ID (UUID)",
    REGISTRY_API_ID: "Registry API ID (UUID)",
    REGISTRY_OPERATION_ID: "Registry operation ID",
    OAUTH_PUBLIC_CLIENT_ID: "Public OAuth client_id",
    TRACE_ID: "W3C trace ID",
    REQUEST_ID: "Request ID",
    API_VENDOR: "API vendor domain",
    API_NAME: "API name",
    API_VERSION: "API version",
    API_REF: "API reference (vendor:name:version)",
    API_VENDOR_NAME: "API vendor and name (vendor/name)",
    OPERATION_ID: "Upstream operationId",
    OPERATION_METHOD_URL: "Upstream operation (method and URL)",
    VENDOR_KEY: "Vendor registry key",
    PROVIDER_NAME: "Credential provider name",
    BASE_URL: "Deployment base URL",
    CURSOR: "Opaque cursor from a previous page",
    DATETIME_FROM: "Start of a time window (RFC 3339)",
    DATETIME_TO: "End of a time window (RFC 3339)",
    NOTE_ETAG: "Note ETag (quoted revision)",
    GOVERNED_HOSTS_ETAG: "Governed-hosts ETag",
    REVIEW_DIGEST: "Connect-session review digest",
    REDIRECT_URI: "Registered redirect URI",
    OAUTH_STATE: "Client OAuth state",
    OIDC_NONCE: "OIDC nonce",
    PKCE_CODE_CHALLENGE: "PKCE S256 code challenge",
    AUTHORIZATION_CODE: "OAuth authorization code",
    SIGNED_FLOW_STATE: "Signed flow state blob",
    OPAQUE_TOKEN: "Opaque flow handle",
}
