"""Unit tests for the nearest-miss ranking helper (``_axes_matched``).

``_axes_matched`` ranks near-miss credential identities so the #748 diagnostic
picks the *closest* miss deterministically. It is a pure scoring function — not
an authorization signal — so it is unit-tested in isolation from the cross-DB
query that feeds it.
"""

from __future__ import annotations

from jentic_one.broker.repos.toolkit_binding_resolver import _axes_matched
from jentic_one.shared.models.api_identity import CredentialScope


def test_axes_matched_all_three() -> None:
    scope = CredentialScope(vendor="acme", name="widgets", version="1.0.0")
    assert _axes_matched(scope, vendor="acme", name="widgets", version="1.0.0") == 3


def test_axes_matched_vendor_only() -> None:
    scope = CredentialScope(vendor="acme", name="gadgets", version="2.0.0")
    assert _axes_matched(scope, vendor="acme", name="widgets", version="1.0.0") == 1


def test_axes_matched_slugifies_vendor_and_name() -> None:
    # Stored scope is canonical; the operation identity is given raw here and must
    # be slugified before comparison, or a canonical scope would never match.
    scope = CredentialScope(vendor="acme-com", name="pets-api", version="v1")
    assert _axes_matched(scope, vendor="Acme.com", name="Pets-API", version="v1") == 3


def test_axes_matched_wildcard_axis_does_not_count() -> None:
    # An unscoped (NULL) axis would have *covered* the operation, so it never
    # reaches the near-miss path; for ranking it does not count as a match.
    scope = CredentialScope(vendor="acme", name=None, version=None)
    assert _axes_matched(scope, vendor="acme", name="widgets", version="1.0.0") == 1


def test_axes_matched_version_not_slugified() -> None:
    # Versions are trimmed, never slugified: '1.1.4' must compare intact.
    scope = CredentialScope(vendor="acme", name="widgets", version="1.1.4")
    assert _axes_matched(scope, vendor="acme", name="widgets", version=" 1.1.4 ") == 3
