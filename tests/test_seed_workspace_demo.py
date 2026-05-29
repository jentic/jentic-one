"""Regression tests for seed_workspace_demo._derive_involved_apis.

Bug history (May 2027):
    The original regex `apis/openapi/([^/]+)/` only captured the first
    path segment after `apis/openapi/`. For sources like
    `./apis/openapi/hubspot.com/CRM-contacts/v3/openapi.json` it
    returned the bare hostname `hubspot.com` — but the public catalog
    has no leaf row at that id (only `hubspot.com/<sub>` rows). That
    bogus involved_api then drove a "Not in workspace" chip in the
    workflow detail page that linked to a Discover sheet which 404'd
    when fetching the spec.

The fix captures `<vendor>/<sub>` and treats `<sub>=='main'` as the
bare-vendor convention used by upstream Arazzo files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# `scripts/` isn't on the package path; add it explicitly.
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from seed_workspace_demo import _derive_involved_apis  # noqa: E402


def _doc_with_source(url: str) -> dict:
    return {"sourceDescriptions": [{"name": "x", "url": url, "type": "openapi"}]}


@pytest.mark.parametrize(
    "url, expected",
    [
        # `<vendor>/main/...` — drop the `main` segment, the catalog id
        # is the bare hostname (`stripe.com`, `slack.com`, etc.).
        ("./apis/openapi/stripe.com/main/2025-03-31.basil/openapi.json", ["stripe.com"]),
        ("./apis/openapi/slack.com/main/1.7.0/openapi.json", ["slack.com"]),
        ("./apis/openapi/zendesk.com/main/2.0.0/openapi.json", ["zendesk.com"]),
        ("./apis/openapi/asana.com/main/1.0/openapi.json", ["asana.com"]),
        # `<vendor>/<sub>/...` — keep the sub segment, the catalog id
        # is `<vendor>/<sub>`.
        (
            "./apis/openapi/hubspot.com/CRM-contacts/v3/openapi.json",
            ["hubspot.com/CRM-contacts"],
        ),
        (
            "./apis/openapi/ebay.com/sell-fulfillment/v1.20.6/openapi.json",
            ["ebay.com/sell-fulfillment"],
        ),
    ],
)
def test_derive_involved_apis_from_url(url: str, expected: list[str]) -> None:
    """Each URL convention maps to the correct catalog id."""
    assert _derive_involved_apis(_doc_with_source(url)) == expected


def test_derive_involved_apis_dedupes_across_workflows() -> None:
    """Multiple sources with overlapping vendors collapse to a unique list."""
    doc = {
        "sourceDescriptions": [
            {"url": "./apis/openapi/zendesk.com/main/2.0.0/openapi.json"},
            {"url": "./apis/openapi/zendesk.com/main/2.0.0/openapi.json"},
            {"url": "./apis/openapi/hubspot.com/CRM-contacts/v3/openapi.json"},
        ]
    }
    assert _derive_involved_apis(doc) == ["zendesk.com", "hubspot.com/CRM-contacts"]


def test_derive_involved_apis_falls_back_to_capability_id_style_op() -> None:
    """Plain operationIds with `METHOD/<host>/path` still extract the host."""
    doc = {
        "workflows": [
            {
                "steps": [
                    {"operationId": "GET/api.example.com/v1/users"},
                ]
            }
        ]
    }
    assert _derive_involved_apis(doc) == ["api.example.com"]
