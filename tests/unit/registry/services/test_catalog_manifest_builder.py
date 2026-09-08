"""Unit tests for the pure catalog manifest/preview projection helpers.

No network, no DB — these exercise ``manifest_builder`` over in-memory dicts.
"""

from __future__ import annotations

import pytest

from jentic_one.registry.services.catalog import manifest_builder as mb


def _include(url: str) -> dict[str, str]:
    return {"url": url}


def _manifest_url(domain: str, sub: str, version: str = "v1") -> str:
    return (
        "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/"
        f"apis/openapi/{domain}/{sub}/{version}/apis.json"
    )


# ── parse_apis_json ──────────────────────────────────────────────────────────


def test_parse_simple_domain_entry() -> None:
    data = {"include": [_include(_manifest_url("stripe.com", "main", "2024-01-01"))]}
    [entry] = mb.parse_apis_json(data)
    assert entry.api_id == "stripe.com"
    assert entry.path == "apis/openapi/stripe.com/main"
    assert entry.spec_url and entry.spec_url.endswith("/openapi.json")
    assert "github.com/jentic/jentic-public-apis/tree/main/" in entry.github_url
    assert entry.vendor == "stripe.com"


def test_parse_umbrella_subname_folds_into_api_id() -> None:
    data = {"include": [_include(_manifest_url("googleapis.com", "admin"))]}
    [entry] = mb.parse_apis_json(data)
    assert entry.api_id == "googleapis.com/admin"
    assert entry.path == "apis/openapi/googleapis.com/admin"


@pytest.mark.parametrize("ver", ["main", "master", "latest", "v2", "1"])
def test_parse_version_subdir_is_not_treated_as_umbrella(ver: str) -> None:
    data = {"include": [_include(_manifest_url("acme.io", ver, "2024"))]}
    [entry] = mb.parse_apis_json(data)
    assert entry.api_id == "acme.io"


def test_parse_dedup_and_sort() -> None:
    data = {
        "include": [
            _include(_manifest_url("zeta.com", "main")),
            _include(_manifest_url("alpha.com", "main")),
            _include(_manifest_url("zeta.com", "main")),
        ]
    }
    entries = mb.parse_apis_json(data)
    assert [e.api_id for e in entries] == ["alpha.com", "zeta.com"]


def test_parse_ignores_malformed_and_non_dict() -> None:
    data = {"include": ["not-a-dict", {"url": "https://example.com/nope.json"}, {}]}
    assert mb.parse_apis_json(data) == []


def test_parse_missing_include_key() -> None:
    assert mb.parse_apis_json({}) == []


# ── extract_vendor ───────────────────────────────────────────────────────────


def test_extract_vendor_host_with_path() -> None:
    assert mb.extract_vendor("api.stripe.com/v1") == "stripe.com"


def test_extract_vendor_two_label_host() -> None:
    assert mb.extract_vendor("slack.com") == "slack.com"


def test_extract_vendor_bare_label() -> None:
    assert mb.extract_vendor("stripe") == "stripe"


def test_extract_vendor_empty() -> None:
    assert mb.extract_vendor("") is None


# ── search ───────────────────────────────────────────────────────────────────


def _search_entries() -> list[mb.ManifestEntry]:
    return [
        mb.ManifestEntry(api_id="stripe.com", path="p", spec_url=None, github_url="g"),
        mb.ManifestEntry(api_id="slack.com", path="p", spec_url=None, github_url="g"),
        mb.ManifestEntry(api_id="github.com", path="p", spec_url=None, github_url="g"),
    ]


def test_search_empty_query_returns_all() -> None:
    assert len(mb.score_entries(_search_entries(), "")) == 3
    assert len(mb.score_entries(_search_entries(), None)) == 3


def test_search_filters_by_token() -> None:
    result = mb.score_entries(_search_entries(), "stripe")
    assert [e.api_id for e, _ in result] == ["stripe.com"]


def test_search_no_match_returns_empty() -> None:
    assert mb.score_entries(_search_entries(), "zzz") == []


def test_whole_word_match_outranks_substring_match() -> None:
    # The real #604 pain: an exact-name match must beat an entry that merely
    # contains the query as a substring of a longer token. "stripe" should rank
    # stripe.com (whole token "stripe") above soundstripe.com (buried in the
    # token "soundstripe"), even though "soundstripe" sorts first alphabetically.
    entries = _entries("soundstripe.com", "stripe.com")
    scored = mb.score_entries(entries, "stripe")
    assert [e.api_id for e, _ in scored] == ["stripe.com", "soundstripe.com"]
    # same coverage (1 of 1 tokens, base 2), whole-token hit breaks the tie
    assert scored[0][1] == 3  # coverage 1 x 2 + whole 1
    assert scored[1][1] == 2  # coverage 1 x 2 + whole 0


def test_whole_word_match_wins_regardless_of_alphabetical_order() -> None:
    # "box" — box.com is the wanted API but Mapbox sorts earlier; the old flat
    # scorer tied them and alphabetised, burying box.com. Quality tiers fix it.
    entries = _entries("Mapbox", "box.com")
    scored = mb.score_entries(entries, "box")
    assert [e.api_id for e, _ in scored] == ["box.com", "Mapbox"]


def test_whole_and_substring_hits_on_same_id_count_once_at_the_higher_tier() -> None:
    # "box" matches box.mapbox.com both as the whole token "box" AND as a
    # substring of "mapbox" — per query token it must count once, at the
    # whole-token tier, never both (the if/elif in score_entry).
    assert mb.score_entry("box.mapbox.com", ["box"]) == 3  # coverage 1 x 2 + whole 1


def test_coverage_dominates_match_quality_on_multi_word_queries() -> None:
    # Covering MORE of the query must always outrank covering less of it more
    # "cleanly": for "google api", googleapis.com (both words, as substrings)
    # must beat rest-api.com (one word, as a whole token) — otherwise every
    # *-api.com entry in the catalog buries the obvious intent match.
    entries = _entries("rest-api.com", "googleapis.com")
    scored = mb.score_entries(entries, "google api")
    assert [e.api_id for e, _ in scored] == ["googleapis.com", "rest-api.com"]
    assert scored[0][1] == 6  # coverage 2 x 3 + whole 0
    assert scored[1][1] == 4  # coverage 1 x 3 + whole 1


def test_duplicate_query_tokens_are_deduplicated() -> None:
    # "stripe stripe" ranks and scores exactly like "stripe" — duplicates would
    # scale every entry uniformly (no ranking effect) at double the scan cost.
    entries = _entries("soundstripe.com", "stripe.com")
    assert mb.score_entries(entries, "stripe stripe") == mb.score_entries(entries, "stripe")


def test_score_entry_empty_api_id_scores_zero() -> None:
    assert mb.score_entry("", ["stripe"]) == 0


def test_search_membership_unchanged_only_order_differs() -> None:
    # Guardrail: grading match quality must never change *which* entries appear
    # (recall), only their order. Any entry with at least one substring hit on a
    # query token still qualifies, exactly as before.
    entries = _entries("stripe.com", "soundstripe.com", "unrelated.com")
    result_ids = {e.api_id for e, _ in mb.score_entries(entries, "stripe")}
    assert result_ids == {"stripe.com", "soundstripe.com"}


# ── score_entries / paginate_entries (keyset paging) ──────────────────────────


def _entries(*api_ids: str) -> list[mb.ManifestEntry]:
    return [mb.ManifestEntry(api_id=a, path="p", spec_url=None, github_url="g") for a in api_ids]


def test_score_entries_browse_is_api_id_sorted_with_none_score() -> None:
    scored = mb.score_entries(_entries("zeta.com", "alpha.com", "mid.com"), None)
    assert [e.api_id for e, _ in scored] == ["alpha.com", "mid.com", "zeta.com"]
    assert all(s is None for _, s in scored)


def test_score_entries_search_sorted_by_score_then_api_id() -> None:
    entries = _entries("api.foo.com", "foo.com", "bar.com")
    scored = mb.score_entries(entries, "foo")
    # both foo entries score equally on the single token; api_id breaks the tie
    assert [e.api_id for e, _ in scored] == ["api.foo.com", "foo.com"]
    assert all(s is not None and s > 0 for _, s in scored)


def test_score_entries_distinct_scores_rank_higher_first() -> None:
    # A 2-token query yields distinct scores: covering both tokens as whole
    # id-tokens (2x3+2=8) outranks covering only one (1x3+1=4).
    entries = _entries("foo-bar.com", "foo-only.com", "bar-only.com", "nope.com")
    scored = mb.score_entries(entries, "foo bar")
    ids = [e.api_id for e, _ in scored]
    # full match (8) first, then the two single-token hits (4) in api_id order, nope excluded
    assert ids == ["foo-bar.com", "bar-only.com", "foo-only.com"]
    assert scored[0][1] == 8
    assert scored[1][1] == 4


def _paginate_all(scored: list[tuple[mb.ManifestEntry, int | None]], limit: int) -> list[str]:
    """Walk every page via cursors and return the flat api_id sequence."""
    out: list[str] = []
    after_id: str | None = None
    after_score: float | None = None
    guard = 0
    while True:
        guard += 1
        assert guard < 1000, "pagination did not terminate"
        page = mb.paginate_entries(
            scored, after_api_id=after_id, after_score=after_score, limit=limit
        )
        out.extend(e.api_id for e in page.items)
        if not page.has_more:
            break
        assert page.next_api_id is not None
        after_id, after_score = page.next_api_id, page.next_score
    return out


def test_paginate_browse_walks_every_entry_once_in_order() -> None:
    scored = mb.score_entries(_entries(*[f"api-{i:03d}.com" for i in range(25)]), None)
    walked = _paginate_all(scored, limit=10)
    expected = [f"api-{i:03d}.com" for i in range(25)]
    assert walked == expected


def test_paginate_browse_has_more_and_cursor_at_boundary() -> None:
    scored = mb.score_entries(_entries("a.com", "b.com", "c.com"), None)
    page1 = mb.paginate_entries(scored, after_api_id=None, after_score=None, limit=2)
    assert [e.api_id for e in page1.items] == ["a.com", "b.com"]
    assert page1.has_more is True
    assert page1.next_api_id == "b.com"

    page2 = mb.paginate_entries(
        scored, after_api_id=page1.next_api_id, after_score=page1.next_score, limit=2
    )
    assert [e.api_id for e in page2.items] == ["c.com"]
    assert page2.has_more is False
    assert page2.next_api_id is None


def test_paginate_last_full_page_reports_no_more() -> None:
    scored = mb.score_entries(_entries("a.com", "b.com"), None)
    page = mb.paginate_entries(scored, after_api_id=None, after_score=None, limit=2)
    assert [e.api_id for e in page.items] == ["a.com", "b.com"]
    assert page.has_more is False
    assert page.next_api_id is None


def test_paginate_search_walks_ranked_results_in_order_across_pages() -> None:
    # Distinct scores via a 2-token query: the full match (both tokens whole, 8)
    # must come out before the single-token hits (4), and the walk order must be
    # preserved across pages.
    entries = _entries("foo-bar.com", "foo-x.com", "bar-y.com", "unrelated.com")
    scored = mb.score_entries(entries, "foo bar")
    walked = _paginate_all(scored, limit=1)
    # foo-bar (8) first; then the two single-token hits (4) in api_id order
    assert walked == ["foo-bar.com", "bar-y.com", "foo-x.com"]
    assert "unrelated.com" not in walked


def test_paginate_search_equal_scores_walk_in_api_id_order() -> None:
    # Single-token query => all matches score equally (whole-token hit, 3);
    # the (score, api_id) tie-break must yield ascending api_id order, with no
    # skips/dups, across pages.
    entries = _entries("foo-c.com", "foo-a.com", "foo-b.com", "nope.com")
    scored = mb.score_entries(entries, "foo")
    walked = _paginate_all(scored, limit=1)
    assert walked == ["foo-a.com", "foo-b.com", "foo-c.com"]


def test_paginate_cursor_past_end_returns_empty_terminal_page() -> None:
    scored = mb.score_entries(_entries("a.com", "b.com"), None)
    page = mb.paginate_entries(scored, after_api_id="zzz.com", after_score=None, limit=10)
    assert page.items == []
    assert page.has_more is False
    assert page.next_api_id is None


def test_paginate_zero_limit_returns_empty_terminal_page() -> None:
    scored = mb.score_entries(_entries("a.com", "b.com"), None)
    page = mb.paginate_entries(scored, after_api_id=None, after_score=None, limit=0)
    assert page.items == []
    # an empty window is terminal — never strand the caller with has_more/no-cursor
    assert page.has_more is False
    assert page.next_api_id is None


# ── coverage / registered (spec_url keyed) ───────────────────────────────────


def _entry(api_id: str, spec_url: str | None) -> mb.ManifestEntry:
    return mb.ManifestEntry(api_id=api_id, path="p", spec_url=spec_url, github_url="g")


def test_is_registered_matches_on_spec_url() -> None:
    e = _entry("stripe.com", "https://x/stripe/openapi.json")
    assert mb.is_registered(e, {"https://x/stripe/openapi.json"}) is True
    assert mb.is_registered(e, {"https://x/other/openapi.json"}) is False


def test_is_registered_false_without_spec_url() -> None:
    assert mb.is_registered(_entry("nospec.com", None), {"https://x"}) is False


def test_sub_api_coverage_does_not_leak_to_siblings() -> None:
    """Importing one googleapis.com/* must not mark its siblings registered.

    This is the umbrella regression the old eTLD+1 vendor match caused: coverage
    is keyed on the exact spec_url, so gmail being imported leaves admin unregistered.
    """
    gmail = _entry("googleapis.com/gmail", "https://x/googleapis.com/gmail/openapi.json")
    admin = _entry("googleapis.com/admin", "https://x/googleapis.com/admin/openapi.json")
    covered = {"https://x/googleapis.com/gmail/openapi.json"}
    assert mb.is_registered(gmail, covered) is True
    assert mb.is_registered(admin, covered) is False


def test_filter_unregistered_drops_only_exact_matches() -> None:
    gmail = _entry("googleapis.com/gmail", "https://x/gmail.json")
    admin = _entry("googleapis.com/admin", "https://x/admin.json")
    out = mb.filter_unregistered([gmail, admin], {"https://x/gmail.json"})
    assert [e.api_id for e in out] == ["googleapis.com/admin"]


def test_manifest_entry_round_trips_through_dict() -> None:
    e = mb.ManifestEntry(
        api_id="stripe.com", path="p", spec_url="https://x", github_url="g", vendor="stripe.com"
    )
    assert mb.ManifestEntry.from_dict(e.to_dict()) == e


# ── preview projection ───────────────────────────────────────────────────────


def _preview_doc() -> dict[str, object]:
    return {
        "info": {"title": "Demo", "version": "1.0", "description": "d"},
        "security": [{"globalKey": []}],
        "components": {
            "parameters": {
                "Shared": {
                    "name": "shared",
                    "in": "query",
                    "required": True,
                    "description": "s",
                }
            },
            "securitySchemes": {
                "globalKey": {"type": "apiKey", "in": "header", "name": "X-Key"},
                "oauth": {"type": "oauth2", "flows": {"authorizationCode": {}}},
            },
        },
        "paths": {
            "/things": {
                "parameters": [{"$ref": "#/components/parameters/Shared"}],
                "get": {
                    "summary": "list",
                    "operationId": "listThings",
                    "tags": ["things"],
                    "parameters": [{"name": "limit", "in": "query", "required": False}],
                },
                "post": {
                    "summary": "create",
                    "security": [{"oauth": ["write"]}],
                    "tags": ["things"],
                },
            }
        },
    }


def test_preview_operations_extracted() -> None:
    proj = mb.project_preview(_preview_doc())
    methods = sorted(op.method for op in proj.operations)
    assert methods == ["GET", "POST"]


def test_preview_path_params_merged_into_op() -> None:
    proj = mb.project_preview(_preview_doc())
    get_op = next(op for op in proj.operations if op.method == "GET")
    names = {p.name for p in get_op.parameters}
    assert names == {"limit", "shared"}


def test_preview_op_security_overrides_doc_security() -> None:
    proj = mb.project_preview(_preview_doc())
    get_op = next(op for op in proj.operations if op.method == "GET")
    post_op = next(op for op in proj.operations if op.method == "POST")
    assert get_op.security == ["globalKey"]
    assert post_op.security == ["oauth"]


def test_preview_security_schemes_slimmed() -> None:
    proj = mb.project_preview(_preview_doc())
    assert proj.security_schemes["globalKey"]["type"] == "apiKey"
    assert proj.security_schemes["globalKey"]["name"] == "X-Key"
    assert proj.security_schemes["oauth"]["flows"] == ["authorizationCode"]


def test_preview_tag_filter() -> None:
    proj = mb.project_preview(_preview_doc(), tag="THINGS")
    assert len(proj.operations) == 2
    proj_none = mb.project_preview(_preview_doc(), tag="missing")
    assert proj_none.operations == []


def test_preview_q_filter_matches_summary_path_method_opid() -> None:
    # operationId match (case-insensitive).
    by_opid = mb.project_preview(_preview_doc(), q="listthings")
    assert [op.method for op in by_opid.operations] == ["GET"]
    # path match.
    by_path = mb.project_preview(_preview_doc(), q="/things")
    assert len(by_path.operations) == 2
    # summary match.
    by_summary = mb.project_preview(_preview_doc(), q="create")
    assert [op.method for op in by_summary.operations] == ["POST"]
    # method match.
    by_method = mb.project_preview(_preview_doc(), q="post")
    assert [op.method for op in by_method.operations] == ["POST"]


def test_preview_q_no_match_is_empty() -> None:
    assert mb.project_preview(_preview_doc(), q="nope-zzz").operations == []


def test_preview_q_and_tag_combine() -> None:
    # tag matches both ops, q narrows to the POST.
    proj = mb.project_preview(_preview_doc(), tag="things", q="create")
    assert [op.method for op in proj.operations] == ["POST"]


def test_preview_blank_q_is_noop() -> None:
    assert len(mb.project_preview(_preview_doc(), q="   ").operations) == 2


def test_preview_q_survives_non_string_summary_and_operation_id() -> None:
    # A malformed upstream spec can carry a non-string summary/operationId.
    # The q-filter must coerce them instead of crashing on the join (TypeError).
    doc = {
        "paths": {
            "/widgets": {
                "get": {"summary": 123, "operationId": {"bad": "obj"}},
                "post": {"summary": "Create widget", "operationId": "createWidget"},
            }
        }
    }
    # No crash, and the projected values are normalised to str/None.
    all_ops = mb.parse_preview_operations(doc, q="widget")
    assert {op.method for op in all_ops} == {"GET", "POST"}
    get_op = next(op for op in all_ops if op.method == "GET")
    assert get_op.summary == ""
    assert get_op.operation_id is None
    # The well-formed POST still matches on its real fields.
    by_opid = mb.parse_preview_operations(doc, q="createwidget")
    assert [op.method for op in by_opid] == ["POST"]


def test_preview_info_projected() -> None:
    proj = mb.project_preview(_preview_doc())
    assert proj.info.title == "Demo"
    assert proj.info.version == "1.0"


def test_preview_unresolvable_ref_param_dropped() -> None:
    doc = {"paths": {"/x": {"get": {"parameters": [{"$ref": "#/nope/missing"}]}}}}
    proj = mb.project_preview(doc)
    [op] = proj.operations
    assert op.parameters == []
