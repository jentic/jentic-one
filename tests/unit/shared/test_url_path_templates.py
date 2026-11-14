"""Unit tests for the shared path-template toolkit in ``shared.url_path``."""

from __future__ import annotations

import pytest

from jentic_one.registry.core import url_index
from jentic_one.shared import url_path


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("/pets/{petId}", "/pets/{petId}"),
        ("/a/{+rest}", "/a/{+rest}"),
        ("/x/{a.b}/{a_b}", "/x/{a.b}/{a_b}"),
        ("/p/{id}/", "/p/{id}"),
        ("/%61dmin/{x}", "/admin/{x}"),
    ],
)
def test_normalize_path_template_parity_with_registry(template: str, expected: str) -> None:
    assert url_index.normalize_path_template is url_path.normalize_path_template
    assert url_path.normalize_path_template(template) == expected
    assert url_index.normalize_path_template(template) == expected


def test_regex_group_base_digit_and_empty() -> None:
    assert url_path.regex_group_base("1id") == "p_1id"
    assert url_path.regex_group_base("") == "p_"
    assert url_path.regex_group_base("a.b") == "a_b"


def test_allocate_group_names_handles_prefixes_and_collisions() -> None:
    groups = url_path.allocate_group_names(["1a", "p_1a", "+rest", "+", "a.b", "a_b"])
    assert groups == {
        "1a": "p_1a",
        "p_1a": "p_1a__2",
        "rest": "rest",
        "": "p_",
        "a.b": "a_b",
        "a_b": "a_b__2",
    }
