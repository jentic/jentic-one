"""Regex permission rules are validated for linear-time matching.

The validator walks the parsed pattern, so shapes the old textual heuristic
missed (alternation under a quantifier, grouped repeats with a minimum count)
are refused at save time, and match-time input is bounded.
"""

from __future__ import annotations

import re
import time

import pytest

from jentic_one.shared.permissions.matching import (
    MAX_MATCH_PATH_LENGTH,
    compile_matcher,
    validate_path,
)


@pytest.mark.parametrize(
    "pattern",
    [
        "/x/(a|a)*",
        "/x/((a+))+",
        "/x/(a+){2,}",
        "/x/(?:a|aa)+",
        "/x/(a+)+",
        "/x/((a)*)*",
    ],
)
def test_rejects_nested_or_alternated_repeats(pattern: str) -> None:
    err = validate_path(pattern, "regex")
    assert err is not None
    assert err.code == "unsafe_regex"


@pytest.mark.parametrize(
    "pattern",
    [
        "/repos/[^/]+/issues",
        "/v1/.*",
        "/(foo|bar)/x",
        r"/items/\d+",
        "/repos/[^/]+/[^/]+/issues/\\d+",
    ],
)
def test_accepts_common_safe_shapes(pattern: str) -> None:
    assert validate_path(pattern, "regex") is None


@pytest.mark.parametrize("pattern", [r"/x/(a)\1", "/x/(?=a)a", "/x/(?!x)y", "/x/(?<=a)b"])
def test_rejects_backrefs_and_lookarounds(pattern: str) -> None:
    err = validate_path(pattern, "regex")
    assert err is not None
    assert err.code == "unsafe_regex"


def test_stored_unsafe_pattern_compiles_to_never_matcher() -> None:
    matcher = compile_matcher("/x/((a+))+", "regex")
    assert matcher is not None
    assert matcher.never is True


def test_unsafe_pattern_is_never_executed_against_hostile_input() -> None:
    matcher = compile_matcher("/x/((a+))+", "regex")
    assert matcher is not None
    started = time.perf_counter()
    assert matcher.matches("/x/" + "a" * 40 + "!") is False
    assert time.perf_counter() - started < 0.5


def test_match_time_is_bounded() -> None:
    matcher = compile_matcher("/x/[a-z]+", "regex")
    assert matcher is not None
    assert matcher.matches("/x/" + "a" * 100) is True
    assert matcher.matches("/x/" + "a" * 5000) is False
    assert len("/x/" + "a" * 5000) > MAX_MATCH_PATH_LENGTH


def test_private_parser_available() -> None:
    # The validator walks ``re._parser``; an interpreter that moves it must fail here.
    assert hasattr(re, "_parser")
    assert hasattr(re._parser, "parse")
