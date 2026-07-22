"""Unit tests for the URL-index helper library."""

from types import SimpleNamespace

from jentic_one.registry.core.url_index import (
    build_index_entry,
    build_path_regex,
    count_segments,
    expand_server_variables,
    extract_param_names,
    normalise_host,
    normalize_path,
    structural_regex,
)


def test_build_index_entry_absolute_url_with_port() -> None:
    entry = build_index_entry("api.example.com:8080", "/v1/users/{userId}", "https")
    assert entry.host_pattern == "api.example.com:8080"
    assert entry.host_regex.match("api.example.com:8080")
    assert entry.path_regex.match("/v1/users/abc-123")
    assert entry.segment_count == 3
    assert entry.param_names == ["userId"]


def test_build_index_entry_relative_path_server() -> None:
    entry = build_index_entry("localhost", "/api/v2/items/{itemId}", "http")
    assert entry.host_pattern == "localhost"
    assert entry.path_regex.match("/api/v2/items/42")
    assert not entry.path_regex.match("/api/v2/items/42/extra")


def test_build_index_entry_variable_host_server() -> None:
    entry = build_index_entry("{tenant}.api.example.com", "/data", "https")
    assert entry.host_regex.match("acme.api.example.com")
    assert entry.host_regex.match("foo.api.example.com")
    assert not entry.host_regex.match("api.example.com")


def test_structural_regex_different_param_names_same_structure() -> None:
    regex1 = structural_regex("/users/{userId}/posts/{postId}")
    regex2 = structural_regex("/users/{id}/posts/{pid}")
    assert regex1 == regex2


def test_structural_regex_different_structures_differ() -> None:
    regex1 = structural_regex("/users/{userId}")
    regex2 = structural_regex("/users/{userId}/posts")
    assert regex1 != regex2


def test_count_segments_simple_path() -> None:
    assert count_segments("/api/v1/users") == 3


def test_count_segments_root_path() -> None:
    assert count_segments("/") == 0


def test_count_segments_parameterized_path() -> None:
    assert count_segments("/users/{userId}/posts") == 3


def test_count_segments_catch_all_returns_negative_one() -> None:
    assert count_segments("/files/{+path}") == -1


def test_count_segments_double_star_returns_negative_one() -> None:
    assert count_segments("/files/**") == -1


def test_normalise_host_strips_default_https_port() -> None:
    assert normalise_host("example.com:443", "https") == "example.com"


def test_normalise_host_strips_default_http_port() -> None:
    assert normalise_host("example.com:80", "http") == "example.com"


def test_normalise_host_preserves_non_default_port() -> None:
    assert normalise_host("example.com:8080", "https") == "example.com:8080"


def test_normalise_host_lowercases() -> None:
    assert normalise_host("API.Example.COM", "https") == "api.example.com"


def test_normalize_path_percent_encoding() -> None:
    assert normalize_path("/foo%20bar") == "/foo bar"


def test_normalize_path_dot_segment_resolution() -> None:
    assert normalize_path("/a/b/../c") == "/a/c"


def test_normalize_path_double_dot_at_start() -> None:
    assert normalize_path("/../a") == "/a"


def test_normalize_path_trailing_slash_stripped() -> None:
    assert normalize_path("/api/v1/") == "/api/v1"


def test_normalize_path_root_preserved() -> None:
    assert normalize_path("/") == "/"


def test_expand_server_variables_none_default_preserves_placeholder() -> None:
    variables = [SimpleNamespace(name="env", default_value=None)]
    result = expand_server_variables("https://{env}.api.example.com", variables)
    assert result == "https://{env}.api.example.com"


def test_expand_server_variables_with_default_replaces() -> None:
    variables = [SimpleNamespace(name="env", default_value="prod")]
    result = expand_server_variables("https://{env}.api.example.com", variables)
    assert result == "https://prod.api.example.com"


def test_build_path_regex_catch_all_matches_multi_segment() -> None:
    pattern = build_path_regex("/files/{+path}")
    assert pattern.match("/files/a/b/c")
    assert pattern.match("/files/single")
    assert not pattern.match("/files/")


def test_build_path_regex_normal_param_does_not_match_slash() -> None:
    pattern = build_path_regex("/files/{path}")
    assert pattern.match("/files/single")
    assert not pattern.match("/files/a/b/c")


def test_structural_regex_catch_all_differs_from_normal() -> None:
    catch_all = structural_regex("/x/{+y}")
    normal = structural_regex("/x/{y}")
    assert catch_all != normal


def test_extract_param_names_strips_reserved_expansion_operator() -> None:
    # RFC 6570 reserved-expansion path templates (Google APIs) declare a `property`
    # parameter but template the token as `{+property}`. The extracted name must be
    # the bare declared name so it reconciles with the OpenAPI `in: path` parameter.
    assert extract_param_names("/v1beta/{+property}:runReport") == ["property"]


def test_extract_param_names_strips_all_rfc6570_operators() -> None:
    # RFC 6570 level-2/3 operators (`+#./;?&`) are all prefixes on the expression,
    # never part of the variable name. Stripping them keeps the declared name intact.
    template = "/{+reserved}/{#frag}/{.label}/{/seg}/{;matrix}/{?form}/{&cont}"
    assert extract_param_names(template) == [
        "reserved",
        "frag",
        "label",
        "seg",
        "matrix",
        "form",
        "cont",
    ]
