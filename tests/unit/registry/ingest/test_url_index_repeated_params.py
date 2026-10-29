"""Unit tests for URL-index regexes built from a path that repeats a parameter.

Pins #1980: a path template that names the same parameter twice (Cloudflare's
``/accounts/{account_id}/.../accounts/{account_id}``) must compile into a regex
whose repeat is a back-reference to the first capture, so both segments carry
the same value and the parameter is reported once. Distinct parameters whose
names sanitize to the same group name must still capture independently.
"""

from __future__ import annotations

import pytest

from jentic_one.registry.core.url_index import (
    URLIndexEntry,
    build_index_entry,
    build_path_regex,
    build_server_index_entries,
    expand_path_template,
    path_param_group_names,
    resolve_server_variable_groups,
)

_TEMPLATE = "/accounts/{account_id}/addressing/address_maps/{address_map_id}/accounts/{account_id}"


def test_build_path_regex_repeated_param_back_references_first_capture() -> None:
    regex = build_path_regex(_TEMPLATE)

    match = regex.fullmatch("/accounts/acc1/addressing/address_maps/map1/accounts/acc1")
    assert match is not None
    assert match.groupdict() == {"account_id": "acc1", "address_map_id": "map1"}
    assert regex.fullmatch("/accounts/acc1/addressing/address_maps/map1/accounts/acc2") is None


def test_build_path_regex_repeated_catch_all_param() -> None:
    regex = build_path_regex("/{+rest}/mirror/{+rest}")

    assert regex.fullmatch("/a/b/mirror/a/b") is not None
    assert regex.fullmatch("/a/b/mirror/a/c") is None


def test_build_index_entry_repeated_param() -> None:
    entry = build_index_entry("api.cloudflare.com", "/client/v4" + _TEMPLATE)

    assert entry.path_regex.fullmatch("/client/v4/accounts/a/addressing/address_maps/m/accounts/a")
    assert entry.param_names == ["account_id", "address_map_id", "account_id"]


@pytest.mark.parametrize(
    ("server_url", "variables"),
    [
        ("https://api.cloudflare.com/client/v4", None),
        ("https://api.cloudflare.com/{base}", {"base": {"default": "client/v4"}}),
        (
            "https://{region}.cloudflare.com/client/v4",
            {"region": {"default": "api", "enum": ["api", "eu"]}},
        ),
    ],
)
def test_build_server_index_entries_repeated_operation_param(
    server_url: str, variables: dict[str, dict[str, object]] | None
) -> None:
    expansion = build_server_index_entries(server_url, variables, _TEMPLATE)

    request_path = "/client/v4/accounts/acc1/addressing/address_maps/map1/accounts/acc1"
    mismatched_path = "/client/v4/accounts/acc1/addressing/address_maps/map1/accounts/acc2"
    matches = []
    for entry in expansion.entries:
        host_m = entry.host_regex.match("api.cloudflare.com")
        path_m = entry.path_regex.fullmatch(request_path)
        assert entry.path_regex.fullmatch(mismatched_path) is None
        if host_m is None or path_m is None:
            continue
        resolved = resolve_server_variable_groups(host_m.groupdict(), path_m.groupdict())
        if resolved is not None:
            matches.append(resolved)

    assert matches
    for resolved in matches:
        assert resolved.path_params == {"account_id": "acc1", "address_map_id": "map1"}
        assert (
            expand_path_template(_TEMPLATE, resolved.path_params)
            == "/accounts/acc1/addressing/address_maps/map1/accounts/acc1"
        )


def _resolve(expansion_entries: list[URLIndexEntry], host: str, path: str) -> list[dict[str, str]]:
    found = []
    for entry in expansion_entries:
        host_m = entry.host_regex.match(host)
        path_m = entry.path_regex.fullmatch(path)
        if host_m is None or path_m is None:
            continue
        resolved = resolve_server_variable_groups(host_m.groupdict(), path_m.groupdict())
        if resolved is not None:
            found.append(resolved.path_params)
    return found


def test_distinct_params_sanitizing_to_one_group_capture_separately() -> None:
    """``{a.b}`` and ``{a_b}`` are different parameters: each gets its own capture."""
    template = "/x/{a.b}/y/{a_b}"
    regex = build_path_regex(template)

    match = regex.fullmatch("/x/1/y/2")
    assert match is not None
    assert match.groupdict() == {"a_b": "1", "a_b__2": "2"}
    assert expand_path_template(template, match.groupdict()) == "/x/1/y/2"


def test_group_allocation_skips_a_suffix_another_param_already_holds() -> None:
    names = path_param_group_names("/{a.b}/{a_b__2}/{a_b}")
    assert names == {"a.b": "a_b", "a_b__2": "a_b__2", "a_b": "a_b__3"}


def test_operation_param_keeps_its_group_over_a_colliding_server_placeholder() -> None:
    """A server base-path placeholder that sanitizes onto an operation parameter
    takes the suffixed group, so ``expand_path_template`` on the operation path
    reads the operation's own value."""
    expansion = build_server_index_entries("https://api.example.com/{a.b}", None, "/y/{a_b}")

    found = _resolve(expansion.entries, "api.example.com", "/1/y/2")
    assert found == [{"a_b__2": "1", "a_b": "2"}]
    assert expand_path_template("/y/{a_b}", found[0]) == "/y/2"


def test_server_placeholder_and_operation_param_with_one_name_back_reference() -> None:
    expansion = build_server_index_entries("https://api.example.com/{acct}", None, "/x/{acct}")

    assert _resolve(expansion.entries, "api.example.com", "/1/x/1") == [{"acct": "1"}]
    assert _resolve(expansion.entries, "api.example.com", "/1/x/2") == []


def test_fallback_entry_names_operation_params_first() -> None:
    """The default-expanded fallback (a variable in the port) allocates the same way."""
    expansion = build_server_index_entries(
        "https://api.example.com:{port}/{a.b}", {"port": {"default": "8443"}}, "/y/{a_b}"
    )

    found = _resolve(expansion.entries, "api.example.com:8443", "/1/y/2")
    assert found == [{"a_b__2": "1", "a_b": "2"}]


def test_server_variable_token_does_not_raise() -> None:
    """A ``{~svN}`` token that no server variable resolves is a path position, not
    a path parameter: it matches without capturing rather than raising.

    ``path_param_group_names`` skips the reserved ``~sv`` prefix, so the group
    lookup has no entry for it — indexing that map unconditionally turned an odd
    template into a ``KeyError``, which surfaces as a dead-lettered import on the
    ingest side and a 500 on the permission-path binding on the lookup side.
    """
    for template in ("/a/{~sv0}", "/a/{~svX}", "/a/{~sv99}"):
        assert build_path_regex(template).fullmatch("/a/anything") is not None

    # The lookup side answers "cannot rebuild this path" instead of raising.
    assert expand_path_template("/a/{~sv0}", {}) is None

    # And the index builders compile a row for it.
    expansion = build_server_index_entries("https://api.example.com", None, "/x/{~sv0}")
    assert len(expansion.entries) == 1


def test_repeated_param_with_mixed_operators_takes_the_narrower_matcher() -> None:
    """A repeat is rendered once, so its single matcher must satisfy every
    occurrence: catch-all only when all of them are.

    Taking the first occurrence's operator let ``/{+rest}/mirror/{rest}`` widen
    the single-segment position to span ``/``, and
    ``/{rest}/mirror/{+rest}`` narrow the catch-all so it could never match.
    """
    narrow = "^/(?P<rest>[^/]+)/mirror/(?P=rest)$"
    assert build_path_regex("/{rest}/mirror/{+rest}").pattern == narrow
    assert build_path_regex("/{+rest}/mirror/{rest}").pattern == narrow

    # Unmixed occurrences keep their own operator.
    assert build_path_regex("/{+rest}/mirror/{+rest}").pattern == "^/(?P<rest>.+)/mirror/(?P=rest)$"
    assert build_path_regex("/a/{+rest}").pattern == "^/a/(?P<rest>.+)$"
    assert build_path_regex("/a/{rest}").pattern == "^/a/(?P<rest>[^/]+)$"
