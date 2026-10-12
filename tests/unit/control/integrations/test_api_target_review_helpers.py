"""How a connect review reads an API target's declared OAuth scheme.

Scopes a spec declares carry no classification, so the name decides; and the
authorize/token endpoints are offered only for a public-catalog spec, since the
token URL receives the approver's client secret.
"""

from __future__ import annotations

import pytest

from jentic_one.control.services.integrations.api_targets import (
    catalog_oauth_endpoints,
    classify_spec_scope,
    oauth_scope_descriptions_of,
)
from jentic_one.shared.catalog import ApiProvenance, ApiSecurityView, DeclaredSecurityScheme


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("https://www.googleapis.com/auth/spreadsheets.readonly", "read"),
        ("channels:read", "read"),
        ("read", "read"),
        ("users.profile:read", "read"),
        ("https://www.googleapis.com/auth/spreadsheets", "write"),
        ("chat:write", "write"),
        ("repo:readwrite", "write"),
        ("files.write", "write"),
        ("admin", "admin"),
        ("admin.users:write", "admin"),
        ("bot", "write"),
    ],
)
def test_classify_spec_scope(name: str, expected: str) -> None:
    assert classify_spec_scope(name) == expected


def _view(origin: str | None, *, urls: bool = True) -> ApiSecurityView:
    scheme = DeclaredSecurityScheme(
        name="oauth",
        type="oauth2",
        oauth_scopes=("read", "write"),
        oauth_scope_descriptions=(("read", "Read your things"),),
        authorization_url="https://auth.example.com/authorize" if urls else None,
        token_url="https://auth.example.com/token" if urls else None,
    )
    return ApiSecurityView(
        vendor="example-com",
        name="api",
        version="1",
        display_name=None,
        revision_id="rev_1",
        schemes=(scheme,),
        hosts=("https://api.example.com",),
        provenance=ApiProvenance(
            origin=origin, catalog_api_id=None, submitted_by=None, source_url=None
        ),
    )


def test_catalog_spec_offers_its_endpoints() -> None:
    assert catalog_oauth_endpoints(_view("catalog")) == (
        "https://auth.example.com/authorize",
        "https://auth.example.com/token",
    )


@pytest.mark.parametrize("origin", [None, "uploaded", "overlay"])
def test_submitted_spec_never_offers_its_endpoints(origin: str | None) -> None:
    assert catalog_oauth_endpoints(_view(origin)) is None


def test_catalog_spec_without_authorization_code_endpoints_offers_none() -> None:
    assert catalog_oauth_endpoints(_view("catalog", urls=False)) is None
    assert catalog_oauth_endpoints(None) is None


def test_scope_descriptions_come_from_the_spec() -> None:
    assert oauth_scope_descriptions_of(_view("catalog")) == {"read": "Read your things"}
