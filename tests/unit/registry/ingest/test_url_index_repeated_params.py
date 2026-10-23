"""Unit tests for URL-index regexes built from a path that repeats a parameter.

Pins #1980: a path template that names the same parameter twice (Cloudflare's
``/accounts/{account_id}/.../accounts/{account_id}``) must compile into a regex
whose repeat is a back-reference to the first capture, so both segments carry
the same value and the parameter is reported once.
"""

from __future__ import annotations

import pytest

from jentic_one.registry.core.url_index import (
    build_index_entry,
    build_path_regex,
    build_server_index_entries,
    expand_path_template,
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
