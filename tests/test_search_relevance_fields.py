"""P2 — Search relevance fields (`matched_on`, `match_snippet`).

These tests pin the post-rank substring annotations attached to every
`/search` row. They share BM25 with the rest of the test suite (the
session-scoped DB persists across modules); the seed below adds a few
APIs whose summary/description/tag values exercise each match path.

The contract under test:
  - every row has a non-empty `matched_on` list
  - when the query lands inside `name | operation_summary | description |
    tag`, `match_snippet` carries the matched span wrapped in `\\u0001`
    sentinel chars so the frontend can highlight without HTML
  - if no field substring-matches (rare, e.g. BM25 stem hit), the
    snippet is `None`
"""

import json

import pytest
from src.routers.search import _compute_matches


_NAME_HIT_API = "stripe-charge-name.relevance.test"
_DESC_HIT_API = "noun.relevance.test"  # plain api_id, no token-level hit
_TAG_HIT_API = "tagged.relevance.test"


@pytest.fixture(scope="module", autouse=True)
def _seed_relevance_apis(admin_client):
    """Seed three APIs whose match shapes differ:
    - `_NAME_HIT_API`: query token appears in the api_id (host) and summary.
    - `_DESC_HIT_API`: query token is buried inside the long description only.
    - `_TAG_HIT_API`: query token appears as an OpenAPI tag.

    Also seed unrelated noise so BM25's IDF stays positive (see notes in
    `test_search_filters.py`)."""
    apis = [
        {
            "id": "noise-relevance-1.test.local",
            "summary": "First noise op",
            "description": "Unrelated content for BM25 variance.",
            "tags": ["Misc"],
        },
        {
            "id": "noise-relevance-2.test.local",
            "summary": "Second noise op",
            "description": "More unrelated content.",
            "tags": ["Misc"],
        },
        {
            "id": _NAME_HIT_API,
            "summary": "Issue a stripe-charge against the saved card",
            "description": "Idempotent payment capture.",
            "tags": ["Charges"],
        },
        {
            "id": _DESC_HIT_API,
            "summary": "Capture a payment",
            "description": (
                "Long form description that mentions stripe-charge buried "
                "in the middle of an otherwise generic paragraph."
            ),
            "tags": ["Payments"],
        },
        {
            "id": _TAG_HIT_API,
            "summary": "Generic-named operation",
            "description": "Generic description.",
            "tags": ["stripe-charge"],
        },
    ]

    for entry in apis:
        spec = {
            "openapi": "3.1.0",
            "info": {"title": entry["id"], "version": "1.0.0"},
            "servers": [{"url": f"https://{entry['id']}"}],
            "paths": {
                "/op": {
                    "post": {
                        "operationId": f"op_{entry['id'].replace('.', '_').replace('-', '_')}",
                        "summary": entry["summary"],
                        "description": entry["description"],
                        "tags": entry["tags"],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        resp = admin_client.post(
            "/import",
            json={
                "sources": [
                    {
                        "type": "inline",
                        "content": json.dumps(spec),
                        "filename": f"{entry['id']}.json",
                    }
                ]
            },
        )
        assert resp.status_code in (200, 201), f"seed failed for {entry['id']}: {resp.text}"
    yield


def _search_local(client, q):
    """Hit `/search` and return only `source=local` rows — keeps the
    assertions immune to whatever the catalog blender happens to do."""
    resp = client.get("/search", params={"q": q, "n": 50, "source": "workspace"})
    assert resp.status_code == 200, resp.text
    return [r for r in resp.json() if r.get("source") == "local"]


def test_every_row_has_matched_on(admin_client):
    rows = _search_local(admin_client, "stripe-charge")
    assert rows, "expected at least one local row for the seeded query"
    for row in rows:
        assert isinstance(row.get("matched_on"), list), row
        assert row["matched_on"], f"matched_on empty: {row}"


def test_summary_match_produces_sentinel_wrapped_snippet(admin_client):
    rows = _search_local(admin_client, "stripe-charge")
    name_hits = [r for r in rows if (r.get("summary") or "").lower().find("stripe-charge") >= 0]
    assert name_hits, "expected at least one row whose summary contains the query"
    sample = name_hits[0]
    snippet = sample.get("match_snippet")
    assert snippet, f"expected snippet, got {sample!r}"
    assert "\u0001" in snippet, f"snippet missing sentinel: {snippet!r}"
    # Sentinels come in pairs around the matched span.
    assert snippet.count("\u0001") == 2, snippet
    # `operation_summary` is the highest-priority field that should match here.
    assert "operation_summary" in sample["matched_on"], sample["matched_on"]


def test_tag_only_match_falls_through_to_tag_priority(admin_client):
    rows = _search_local(admin_client, "stripe-charge")
    # The tagged-only seed doesn't carry the query in its summary or
    # description, so the only field that should claim the match is `tag`.
    target = [r for r in rows if r.get("api_id") == _TAG_HIT_API]
    if not target:
        pytest.skip("tag-only seed didn't land in BM25 top-N for this corpus")
    sample = target[0]
    # The query string IS in the tag list — `tag` should be in matched_on
    # and the snippet should be derived from the tag (lowest priority but
    # the only field that hits).
    assert "tag" in sample.get("matched_on", []), sample
    assert sample.get("match_snippet"), sample


def test_compute_matches_falls_back_to_description_when_nothing_substring_matches():
    """Pure-function smoke test of `_compute_matches` for the rare case
    where BM25 hands back a doc whose fields don't share any literal
    substring with the query (e.g. token overlap via prefixes like
    `charge` in `charged`). The contract is `matched_on=['description']`
    + `match_snippet=None`, so the row still annotates *something*."""
    matched_on, snippet = _compute_matches(
        "stripe-charge",
        name="Some Operation",
        operation_summary="Capture a payment",
        description="Generic description with no overlap.",
        tags=["Misc"],
    )
    assert matched_on == ["description"]
    assert snippet is None
