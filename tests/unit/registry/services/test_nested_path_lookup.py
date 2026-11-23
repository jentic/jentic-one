"""Unit tests for nested path parameter resolution, precedence, and ambiguity (#701, #1422)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from jentic_one.registry.core.url_index import (
    build_index_entry,
    build_server_index_entries,
    count_segments,
    expand_path_template,
    has_trailing_path_param,
    normalise_host,
    normalize_path,
    trailing_param_path_regex,
)
from jentic_one.registry.repos.url_index_repo import UrlIndexCandidate
from jentic_one.registry.services.inspect.url_lookup import (
    URLLookupService,
    _match_candidate_path,
    _RankedMatch,
)
from jentic_one.shared.discovery.errors import AmbiguousMatchError

REVISION_ID = uuid.UUID("00000000-0000-0000-0000-000000000701")
OLDER_REVISION_ID = uuid.UUID("00000000-0000-0000-0000-000000000700")


def _candidate(
    operation_id: str,
    path_template: str,
    *,
    host: str = "api.github.com",
    method: str = "GET",
    revision_id: uuid.UUID = REVISION_ID,
    server_url: str | None = None,
    variables: dict[str, dict[str, object]] | None = None,
    legacy: bool = False,
) -> list[UrlIndexCandidate]:
    """Build realistic UrlIndexCandidate entries using the real index builder."""
    if server_url is not None:
        entries = build_server_index_entries(server_url, variables, path_template).entries
    else:
        entries = [build_index_entry(host, path_template, "https", operation_path=path_template)]

    candidates: list[UrlIndexCandidate] = []
    for entry in entries:
        row = SimpleNamespace(
            operation_id=operation_id,
            revision_id=revision_id,
            method=method,
            host=entry.host_pattern,
            host_regex=entry.host_regex.pattern,
            path_template=entry.path_pattern,
            path_regex=(
                entry.path_regex.pattern.removeprefix("(?#sv)")
                if legacy
                else entry.path_regex.pattern
            ),
            param_names=entry.param_names,
            segment_count=entry.segment_count,
        )
        candidates.append(
            UrlIndexCandidate(
                row=row,  # type: ignore[arg-type]
                vendor="github.com",
                name="api.github.com",
                version="1.1.4",
                operation_path=path_template,
                operation_method=method,
            )
        )
    return candidates


def _rank_against(candidates: list[UrlIndexCandidate], url: str) -> _RankedMatch | None:
    parsed = urlparse(url)
    host = normalise_host(parsed.netloc, parsed.scheme)
    path = normalize_path(parsed.path or "/")
    return URLLookupService._match_and_rank(candidates, path, host)


@pytest.mark.parametrize(
    ("request_path", "expected_path_param", "expected_relative_path"),
    [
        (
            "/repos/octocat/Hello-World/contents/README.md",
            "README.md",
            "/repos/octocat/Hello-World/contents/README.md",
        ),
        (
            "/repos/octocat/Hello-World/contents/docs/README.md",
            "docs/README.md",
            "/repos/octocat/Hello-World/contents/docs/README.md",
        ),
        (
            "/repos/octocat/Hello-World/contents/docs/guide/intro.md",
            "docs/guide/intro.md",
            "/repos/octocat/Hello-World/contents/docs/guide/intro.md",
        ),
    ],
)
def test_github_contents_resolves_single_and_nested_paths(
    request_path: str,
    expected_path_param: str,
    expected_relative_path: str,
) -> None:
    """Pins #701 / #1422: trailing ``{path}`` matches single-segment and nested slash paths."""
    candidates = [
        *_candidate("op_repo", "/repos/{owner}/{repo}"),
        *_candidate("op_contents", "/repos/{owner}/{repo}/contents/{path}"),
    ]
    hit = _rank_against(candidates, f"https://api.github.com{request_path}")
    assert hit is not None
    assert hit.operation_id == "op_contents"
    assert hit.path_params == {
        "owner": "octocat",
        "repo": "Hello-World",
        "path": expected_path_param,
    }
    assert expand_path_template(hit.candidate.operation_path, hit.path_params) == (
        expected_relative_path
    )


def test_static_sibling_route_beats_trailing_param() -> None:
    """A static sibling ``/contents/special`` wins on exact match; deeper paths hit ``{path}``."""
    candidates = [
        *_candidate("op_contents", "/repos/{owner}/{repo}/contents/{path}"),
        *_candidate("op_special", "/repos/{owner}/{repo}/contents/special"),
    ]

    exact_hit = _rank_against(
        candidates, "https://api.github.com/repos/octocat/Hello-World/contents/special"
    )
    assert exact_hit is not None
    assert exact_hit.operation_id == "op_special"

    nested_hit = _rank_against(
        candidates, "https://api.github.com/repos/octocat/Hello-World/contents/special/nested.md"
    )
    assert nested_hit is not None
    assert nested_hit.operation_id == "op_contents"
    assert nested_hit.path_params["path"] == "special/nested.md"


def test_single_segment_sibling_route_beats_trailing_param() -> None:
    """An exact-segment route ``/items/{id}/meta`` beats greedy ``/items/{path}``."""
    candidates = [
        *_candidate("op_items_path", "/repos/{owner}/{repo}/items/{path}"),
        *_candidate("op_item_meta", "/repos/{owner}/{repo}/items/{id}/meta"),
    ]

    sibling_hit = _rank_against(
        candidates, "https://api.github.com/repos/octocat/Hello-World/items/42/meta"
    )
    assert sibling_hit is not None
    assert sibling_hit.operation_id == "op_item_meta"
    assert sibling_hit.path_params == {"owner": "octocat", "repo": "Hello-World", "id": "42"}

    nested_hit = _rank_against(
        candidates, "https://api.github.com/repos/octocat/Hello-World/items/42/meta/deep"
    )
    assert nested_hit is not None
    assert nested_hit.operation_id == "op_items_path"
    assert nested_hit.path_params == {
        "owner": "octocat",
        "repo": "Hello-World",
        "path": "42/meta/deep",
    }


@pytest.mark.parametrize(
    ("template_a", "template_b", "request_path"),
    [
        # Identical structure with distinct trailing parameter names.
        (
            "/repos/{owner}/{repo}/contents/{path}",
            "/repos/{owner}/{repo}/contents/{file_path}",
            "/repos/octocat/Hello-World/contents/docs/intro.md",
        ),
        # Different parameter counts with identical literal prefix.
        (
            "/files/{path}",
            "/files/{bucket}/{key}",
            "/files/my-bucket/dir/a.txt",
        ),
        # No literals at all, different parameter arity, both spanning slashes.
        (
            "/{a}/{path}",
            "/{a}/{b}/{path}",
            "/x/y/z/w",
        ),
        # Equal literal counts at incomparable segment positions.
        (
            "/v1/docs/{topic}/{path}",
            "/v1/{category}/files/{path}",
            "/v1/docs/files/guide/intro.md",
        ),
        # Different literal counts where the fewer-literal route pins a later segment
        # that the higher-literal route swallows into its greedy parameter.
        (
            "/v1/docs/files/{path}",
            "/v1/{x}/{y}/special/{path}",
            "/v1/docs/files/special/a/b.md",
        ),
    ],
)
def test_ambiguous_greedy_templates_fail_closed(
    template_a: str, template_b: str, request_path: str
) -> None:
    """Overlapping greedy matches where neither dominates fail closed with AmbiguousMatchError."""
    candidates = [
        *_candidate("op_a", template_a),
        *_candidate("op_b", template_b),
    ]
    with pytest.raises(AmbiguousMatchError):
        _rank_against(candidates, f"https://api.github.com{request_path}")


def test_rfc6570_reserved_expansion_preserved_alongside_trailing_param() -> None:
    """Explicit ``{+path}`` coexists with single-segment and trailing ``{param}`` routes."""
    candidates = [
        *_candidate("op_single", "/files/{name}"),
        *_candidate("op_catch_all", "/files/{+path}"),
    ]

    single_hit = _rank_against(candidates, "https://api.github.com/files/README.md")
    assert single_hit is not None
    assert single_hit.operation_id == "op_single"
    assert single_hit.path_params == {"name": "README.md"}

    nested_hit = _rank_against(candidates, "https://api.github.com/files/docs/guide/intro.md")
    assert nested_hit is not None
    assert nested_hit.operation_id == "op_catch_all"
    assert nested_hit.path_params == {"path": "docs/guide/intro.md"}


def test_more_specific_trailing_param_beats_broad_reserved_expansion() -> None:
    """More specific literal route ``.../contents/{path}`` beats broad ``/repos/{+rest}``."""
    candidates = [
        *_candidate("op_broad", "/repos/{+rest}"),
        *_candidate("op_contents", "/repos/{owner}/{repo}/contents/{path}"),
    ]
    hit = _rank_against(
        candidates, "https://api.github.com/repos/octocat/Hello-World/contents/docs/intro.md"
    )
    assert hit is not None
    assert hit.operation_id == "op_contents"
    assert hit.path_params["path"] == "docs/intro.md"


@pytest.mark.parametrize("raw_segment", ["docs%2Fguide%2Fintro.md", "docs%2fguide%2fintro.md"])
def test_encoded_slashes_are_not_decoded_to_path_separators(raw_segment: str) -> None:
    """Encoded slashes stay inside one segment (uppercased to ``%2F``) and never split segments."""
    candidates = _candidate("op_contents", "/repos/{owner}/{repo}/contents/{path}")
    hit = _rank_against(
        candidates, f"https://api.github.com/repos/octocat/Hello-World/contents/{raw_segment}"
    )
    assert hit is not None
    assert hit.operation_id == "op_contents"
    assert hit.path_params["path"] == "docs%2Fguide%2Fintro.md"
    assert (
        expand_path_template(hit.candidate.operation_path, hit.path_params)
        == "/repos/octocat/Hello-World/contents/docs%2Fguide%2Fintro.md"
    )


def test_dot_segment_traversal_normalizes_before_route_matching() -> None:
    """Dot segments resolve before lookup so ``/contents/docs/../../issues/7`` hits ``/issues``."""
    candidates = [
        *_candidate("op_contents", "/repos/{owner}/{repo}/contents/{path}"),
        *_candidate("op_issues", "/repos/{owner}/{repo}/issues/{number}"),
    ]
    hit = _rank_against(
        candidates,
        "https://api.github.com/repos/octocat/Hello-World/contents/docs/../../issues/7",
    )
    assert hit is not None
    assert hit.operation_id == "op_issues"
    assert hit.path_params == {"owner": "octocat", "repo": "Hello-World", "number": "7"}


def test_server_variable_at_end_of_base_path_does_not_become_greedy() -> None:
    """A server base-path variable ``/{version}`` with operation path ``/`` stays single-segment."""
    candidates = _candidate(
        "op_root",
        "/",
        server_url="https://api.github.com/{version}",
        variables={"version": {"default": "v1"}},
    )
    exact_hit = _rank_against(candidates, "https://api.github.com/v2")
    assert exact_hit is not None
    assert exact_hit.operation_id == "op_root"

    nested_hit = _rank_against(candidates, "https://api.github.com/v2/extra/subpath")
    assert nested_hit is None


def test_repeated_trailing_parameter_does_not_become_greedy() -> None:
    """A repeated parameter ``/accounts/{id}/.../accounts/{id}`` stays single-segment."""
    candidates = _candidate("op_repeat", "/accounts/{id}/mirror/{id}")
    nested_hit = _rank_against(candidates, "https://api.github.com/accounts/acc1/mirror/acc1/extra")
    assert nested_hit is None


def test_match_candidate_path_supports_any_method_nested_row() -> None:
    """Any-method row matching recognizes nested trailing-parameter paths."""
    put_row = _candidate("op_put_contents", "/repos/{owner}/{repo}/contents/{path}", method="PUT")[
        0
    ].row
    nested = "/repos/octocat/Hello-World/contents/docs/guide/intro.md"
    matched = _match_candidate_path(put_row, nested, count_segments(nested))
    assert matched is not None
    match, is_multi, is_implicit = matched
    assert is_multi is True
    assert is_implicit is True
    assert match.group("path") == "docs/guide/intro.md"


def test_equal_nested_matches_across_live_revisions_prefer_newest() -> None:
    """Two live revisions with identical trailing-param templates pick the newest revision."""
    newest = _candidate("op_new", "/repos/{owner}/{repo}/contents/{path}", revision_id=REVISION_ID)
    older = _candidate(
        "op_old", "/repos/{owner}/{repo}/contents/{path}", revision_id=OLDER_REVISION_ID
    )
    hit = _rank_against(
        [*newest, *older],
        "https://api.github.com/repos/octocat/Hello-World/contents/docs/guide/intro.md",
    )
    assert hit is not None
    assert hit.operation_id == "op_new"


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("/repos/{owner}/{repo}/contents/{path}", True),
        ("/{path}", True),
        ("/files/{file-path}/", True),
        ("/", False),
        ("/repos/{owner}/{repo}/contents/special", False),
        ("/repos/{owner}/{repo}/items/{id}/meta", False),
        ("/files/{+path}", False),
        ("/files/{#frag}", False),
        ("/items/id-{id}", False),
        ("/a/{~sv0}", False),
    ],
)
def test_has_trailing_path_param(template: str, expected: bool) -> None:
    """Only standalone final ordinary ``{param}`` segments qualify for nested matching."""
    assert has_trailing_path_param(template) is expected


def test_trailing_param_path_regex_rejects_empty_segments() -> None:
    """Widened trailing parameter regex requires non-empty slash-separated segments."""
    entry = build_index_entry("api.github.com", "/repos/{owner}/{repo}/contents/{path}")
    widened = trailing_param_path_regex(
        entry.path_pattern,
        entry.path_regex,
        operation_path="/repos/{owner}/{repo}/contents/{path}",
    )
    assert widened is not None
    assert widened.fullmatch("/repos/octocat/Hello-World/contents/docs/README.md") is not None
    assert widened.fullmatch("/repos/octocat/Hello-World/contents/README.md") is None


def test_server_variable_prefixed_operation_params_can_span_nested_paths() -> None:
    """Operation parameter names that resemble internal group prefixes still widen."""
    for name in ("sv__path", "svk__path"):
        candidates = _candidate("op_contents", f"/files/{{{name}}}")
        hit = _rank_against(candidates, "https://api.github.com/files/docs/README.md")
        assert hit is not None
        assert hit.path_params == {name: "docs/README.md"}
