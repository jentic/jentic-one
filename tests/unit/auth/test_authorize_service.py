"""Unit tests for the AuthorizeService PKCE and agent-scope rules."""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode

from jentic_one.auth.services.authorize_service import (
    _hash_code,
    _verify_pkce,
    candidate_agent_scopes,
    effective_agent_scopes,
)


def test_pkce_valid_verifier_passes() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert _verify_pkce(verifier, challenge) is True


def test_pkce_wrong_verifier_fails() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert _verify_pkce("wrong-verifier", challenge) is False


def test_pkce_empty_verifier_fails() -> None:
    challenge = urlsafe_b64encode(hashlib.sha256(b"x").digest()).rstrip(b"=").decode()
    assert _verify_pkce("", challenge) is False


def test_hash_code_deterministic() -> None:
    assert _hash_code("test") == _hash_code("test")


def test_hash_code_different_inputs_differ() -> None:
    assert _hash_code("a") != _hash_code("b")


def test_hash_code_is_sha256_hex() -> None:
    result = _hash_code("test")
    assert len(result) == 64
    int(result, 16)


# ---------- the agent-bound grant's scope intersection ----------


def test_candidate_scopes_strip_oidc_and_apply_the_client_allowlist() -> None:
    """The half the consent page shows: requested ∩ allowlist, OIDC stripped.

    ``openid`` must never survive — an agent-bound grant carries no OIDC
    identity — and order follows the request so the page and the grant agree.
    """
    assert candidate_agent_scopes(
        ["openid", "apis:write", "credentials:read", "apis:read"],
        frozenset({"apis:read", "apis:write"}),
    ) == ["apis:write", "apis:read"]


def test_candidate_scopes_treat_a_null_allowlist_as_unrestricted() -> None:
    """``allowed_scopes=None`` on the client row means "no allowlist", not "none"."""
    assert candidate_agent_scopes(["openid", "apis:read"], None) == ["apis:read"]


def test_effective_scopes_also_apply_the_agents_live_permission_ceiling() -> None:
    """The agent's own grants are the third operand: a scope the client may ask
    for and the agent does not hold is not granted."""
    assert effective_agent_scopes(
        ["openid", "apis:write", "apis:read"],
        frozenset({"apis:read", "apis:write"}),
        frozenset({"apis:read"}),
    ) == ["apis:read"]


def test_effective_scopes_empty_when_the_agent_holds_none_of_them() -> None:
    """An empty result is the ``no_grantable_scopes`` arm — no grant is minted."""
    assert (
        effective_agent_scopes(
            ["openid", "apis:read"],
            frozenset({"apis:read"}),
            frozenset({"credentials:read"}),
        )
        == []
    )


def test_effective_scopes_start_from_the_candidate_set() -> None:
    """The page's candidate list and the grant's scopes share their first two
    steps by construction, so the page cannot advertise a scope the grant drops."""
    requested = ["openid", "apis:write", "credentials:read", "apis:read"]
    allowlist = frozenset({"apis:read", "apis:write"})
    candidates = candidate_agent_scopes(requested, allowlist)
    assert effective_agent_scopes(requested, allowlist, frozenset(candidates)) == candidates
