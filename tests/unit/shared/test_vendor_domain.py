"""Unit tests for ``shared.vendor_domain`` — hostname → vendor derivation.

The vendor is the registrable domain (eTLD+1) per the Public Suffix List,
including its PRIVATE section, resolved from the bundled snapshot.
"""

from __future__ import annotations

import pytest

from jentic_one.shared.vendor_domain import registrable_domain, vendor_from_api_id


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        # plain ICANN suffixes
        ("stripe.com", "stripe.com"),
        ("api.stripe.com", "stripe.com"),
        ("a.b.api.stripe.com", "stripe.com"),
        # multi-label ICANN suffixes keep the company label
        ("finage.co.uk", "finage.co.uk"),
        ("api.finage.co.uk", "finage.co.uk"),
        ("apex27.co.uk", "apex27.co.uk"),
        ("api.example.com.au", "example.com.au"),
        # PRIVATE-section suffixes keep the tenant label
        ("acme.github.io", "acme.github.io"),
        ("docs.acme.github.io", "acme.github.io"),
        ("acme.herokuapp.com", "acme.herokuapp.com"),
        ("api.acme.herokuapp.com", "acme.herokuapp.com"),
        ("acme.azurewebsites.net", "acme.azurewebsites.net"),
        ("api.acme.azurewebsites.net", "acme.azurewebsites.net"),
        # single-operator PRIVATE-section suffixes resolve to the operator's
        # own namespace instead of splitting it per product subdomain
        ("googleapis.com", "googleapis.com"),
        ("blogger.googleapis.com", "googleapis.com"),
        ("generativelanguage.googleapis.com", "googleapis.com"),
        ("a.b.googleapis.com", "googleapis.com"),
        # ... but only on a label boundary
        ("evilgoogleapis.com", "evilgoogleapis.com"),
        # a host that is itself a public suffix is not widened
        ("co.uk", "co.uk"),
        ("github.io", "github.io"),
        # IP literals are returned verbatim
        ("10.0.0.1", "10.0.0.1"),
        ("192.168.1.20:8080", "192.168.1.20"),
        ("::1", "::1"),
        ("[2001:db8::1]:443", "2001:db8::1"),
        # single-label hosts are returned verbatim
        ("localhost", "localhost"),
        ("localhost:8000", "localhost"),
        ("stripe", "stripe"),
        # normalisation
        ("API.Stripe.COM", "stripe.com"),
        ("api.stripe.com.", "stripe.com"),
        ("  api.stripe.com  ", "stripe.com"),
        ("api.stripe.com:443", "stripe.com"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


@pytest.mark.parametrize("host", ["", "   ", ".", "..", "[]"])
def test_registrable_domain_empty_is_none(host: str) -> None:
    assert registrable_domain(host) is None


def test_distinct_companies_under_shared_suffix_get_distinct_vendors() -> None:
    for a, b in [
        ("finage.co.uk", "apex27.co.uk"),
        ("alpha.github.io", "beta.github.io"),
        ("alpha.herokuapp.com", "beta.herokuapp.com"),
        ("alpha.azurewebsites.net", "beta.azurewebsites.net"),
    ]:
        assert registrable_domain(a) != registrable_domain(b)


@pytest.mark.parametrize(
    ("api_id", "expected"),
    [
        ("api.stripe.com/v1", "stripe.com"),
        ("slack.com", "slack.com"),
        ("slack.com/api", "slack.com"),
        ("github.com/api.github.com", "github.com"),
        ("googleapis.com/admin", "googleapis.com"),
        ("blogger.googleapis.com", "googleapis.com"),
        ("generativelanguage.googleapis.com/gemini-api", "googleapis.com"),
        ("finage.co.uk/main", "finage.co.uk"),
        ("stripe", "stripe"),
        ("", None),
        ("/orphan", None),
    ],
)
def test_vendor_from_api_id(api_id: str, expected: str | None) -> None:
    assert vendor_from_api_id(api_id) == expected
