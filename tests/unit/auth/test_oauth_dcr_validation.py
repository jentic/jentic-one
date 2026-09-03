"""Unit tests for the anonymous OAuth-client DCR service validation (§4.2).

Pure-validation matrix — the DB-backed register flow is covered by
``tests/integration/auth/test_oauth_dcr_registration.py``.
"""

from __future__ import annotations

import pytest

from jentic_one.auth.services.errors import InvalidClientMetadataError
from jentic_one.auth.services.oauth_dcr_service import _cap_scopes, _validate_metadata
from jentic_one.shared.scopes import MCP_TOOL_SCOPES

_VALID_URIS = ["https://client.example.com/callback"]


def _validate(
    *,
    redirect_uris: list[str] | None = None,
    token_endpoint_auth_method: str | None = None,
    grant_types: list[str] | None = None,
    response_types: list[str] | None = None,
) -> None:
    _validate_metadata(
        redirect_uris=redirect_uris if redirect_uris is not None else _VALID_URIS,
        token_endpoint_auth_method=token_endpoint_auth_method,
        grant_types=grant_types,
        response_types=response_types,
    )


def test_minimal_metadata_accepted() -> None:
    _validate()


def test_auth_method_none_accepted() -> None:
    _validate(token_endpoint_auth_method="none")


@pytest.mark.parametrize(
    "method",
    ["client_secret_basic", "client_secret_post", "private_key_jwt"],
)
def test_confidential_auth_methods_rejected(method: str) -> None:
    """This door only mints public clients — confidential registration is 400."""
    with pytest.raises(InvalidClientMetadataError, match="public clients"):
        _validate(token_endpoint_auth_method=method)


def test_supported_grant_types_accepted() -> None:
    _validate(grant_types=["authorization_code", "refresh_token"])


@pytest.mark.parametrize(
    "grants",
    [
        ["client_credentials"],
        ["authorization_code", "implicit"],
        ["urn:ietf:params:oauth:grant-type:jwt-bearer"],
    ],
)
def test_unsupported_grant_types_rejected(grants: list[str]) -> None:
    with pytest.raises(InvalidClientMetadataError, match="unsupported grant_types"):
        _validate(grant_types=grants)


def test_response_type_code_accepted() -> None:
    _validate(response_types=["code"])


def test_unsupported_response_types_rejected() -> None:
    with pytest.raises(InvalidClientMetadataError, match="unsupported response_types"):
        _validate(response_types=["token"])


@pytest.mark.parametrize(
    "uris",
    [
        [],
        ["https://app.example.com/cb#fragment"],
        ["http://evil.example.com/cb"],
        ["not-a-url"],
        # Duplicates are rejected: the D8 dedupe key is the exact redirect-URI
        # *set*, and ["a", "a"] vs ["a"] must not mint two rows for one set.
        ["https://app.example.com/cb", "https://app.example.com/cb"],
    ],
)
def test_invalid_redirect_uris_rejected_as_client_metadata(uris: list[str]) -> None:
    """The canonical redirect-URI validator is reused; failures surface in the
    auth taxonomy (invalid_client_metadata), not the admin one."""
    with pytest.raises(InvalidClientMetadataError):
        _validate(redirect_uris=uris)


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:33418/callback",
        "http://127.0.0.1:8123/cb",
        "https://app.example.com/cb",
    ],
)
def test_localhost_http_and_https_redirects_accepted(uri: str) -> None:
    """application_type=native desktop apps use localhost http redirects (§2)."""
    _validate(redirect_uris=[uri])


@pytest.mark.parametrize(
    "uri",
    [
        # Cursor's real-world MCP OAuth callback (RFC 8252 §7.1).
        "cursor://anysphere.cursor-mcp/oauth/callback",
        # Reverse-DNS private-use scheme, both §7.1 shapes.
        "com.example.app:/oauth/callback",
        "com.example.app://callback",
    ],
)
def test_private_use_scheme_redirects_accepted_on_dcr_door(uri: str) -> None:
    """RFC 8252 §7.1: native apps (Cursor, Claude Code, …) register private-use
    redirect schemes on this door; PKCE S256 is the compensating control."""
    _validate(redirect_uris=[uri])


@pytest.mark.parametrize(
    "uri",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "cursor://anysphere.cursor-mcp/cb#fragment",
        "cursor://",
    ],
)
def test_dangerous_or_malformed_private_use_redirects_rejected(uri: str) -> None:
    """The §7.1 allowance keeps the denylist and well-formedness checks."""
    with pytest.raises(InvalidClientMetadataError):
        _validate(redirect_uris=[uri])


# ---------- scope capping (§4.2: capped to the MCP tool-scope set) ----------


def test_no_scope_claim_caps_to_full_mcp_tool_scope_set() -> None:
    """A DCR client's ceiling is never unrestricted (allowed_scopes=None)."""
    assert _cap_scopes(None) == sorted(MCP_TOOL_SCOPES)
    assert _cap_scopes("   ") == sorted(MCP_TOOL_SCOPES)


def test_scope_claim_intersected_with_mcp_tool_scope_set() -> None:
    assert _cap_scopes("apis:read capabilities:execute") == [
        "apis:read",
        "capabilities:execute",
    ]


def test_privileged_and_unknown_scopes_dropped() -> None:
    """org:admin/agents:write can never enter a DCR client's ceiling."""
    assert _cap_scopes("org:admin agents:write made:up apis:read") == ["apis:read"]


def test_all_scopes_outside_cap_rejected() -> None:
    """Zero overlap with the MCP tool-scope set is rejected, never stored.

    An empty ceiling ``[]`` is falsy and the admin view layer collapses it to
    ``None`` — the "no allowlist" sentinel — so storing it would make the
    client *unrestricted* at /authorize (the opposite of §4.2's "never
    unrestricted").
    """
    with pytest.raises(InvalidClientMetadataError, match="no overlap"):
        _cap_scopes("org:admin")
    with pytest.raises(InvalidClientMetadataError, match="no overlap"):
        _cap_scopes("made:up user")
