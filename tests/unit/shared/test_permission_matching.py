"""Unit tests for the shared permission path-matching seam."""

from __future__ import annotations

import pytest

from jentic_one.shared.permissions.matching import (
    MATCH_MODES,
    MAX_PATTERN_LENGTH,
    PathValidationError,
    compile_matcher,
    validate_path,
)

# ---------------------------------------------------------------------------
# validate_path
# ---------------------------------------------------------------------------


def test_none_path_is_always_valid() -> None:
    # ``None`` is the "no path constraint" sentinel and must be accepted in
    # every mode — that's the only way to author a rule that grants across
    # the whole vendor without listing paths explicitly.
    for mode in MATCH_MODES:
        assert validate_path(None, mode) is None


def test_empty_path_is_rejected_in_every_mode() -> None:
    # Empty string would satisfy the schema's condition-less-allow guard
    # (a set field is a "constraint") while matching every request in
    # prefix mode — an unrestricted grant disguised as a constraint.
    for mode in MATCH_MODES:
        err = validate_path("", mode)
        assert isinstance(err, PathValidationError)
        assert err.code == "empty_path"


def test_valid_regex_returns_none() -> None:
    assert validate_path(r"/v1/users/.*", "regex") is None


def test_invalid_regex_returns_structured_reason() -> None:
    err = validate_path("[unterminated", "regex")
    assert isinstance(err, PathValidationError)
    assert err.code == "invalid_regex"
    # The stored ``re.error`` reason is preserved so callers can fix the pattern
    # without guessing what tripped the parser.
    assert "invalid regex" in err.reason


def test_oversized_pattern_rejected() -> None:
    err = validate_path("a" * (MAX_PATTERN_LENGTH + 1), "regex")
    assert isinstance(err, PathValidationError)
    assert err.code == "pattern_too_long"


def test_unknown_mode_rejected() -> None:
    err = validate_path("/x", "glob")
    assert isinstance(err, PathValidationError)
    assert err.code == "unknown_mode"


def test_literal_modes_accept_any_string_shape() -> None:
    # Literal modes do not parse the string, so brackets that would blow up
    # regex compilation are fine.
    assert validate_path("[not-a-regex]", "prefix") is None
    assert validate_path("[not-a-regex]", "exact") is None


# ---------------------------------------------------------------------------
# compile_matcher — regex
# ---------------------------------------------------------------------------


def test_compile_none_returns_none() -> None:
    assert compile_matcher(None, "regex") is None


def test_regex_matches_full_path_only() -> None:
    # This is the anchoring migration: ``.match()`` used to accept
    # ``/v1/users/1/extra`` for the pattern ``/v1/users/\d+``; ``.fullmatch()``
    # (via the new matcher) rejects it.
    matcher = compile_matcher(r"/v1/users/\d+", "regex")
    assert matcher is not None
    assert matcher.matches("/v1/users/42") is True
    assert matcher.matches("/v1/users/42/roles") is False
    assert matcher.matches("/prefix/v1/users/42") is False


def test_regex_wildcard_full_matches_any_path() -> None:
    matcher = compile_matcher(".*", "regex")
    assert matcher is not None
    assert matcher.matches("/anything/goes") is True


def test_invalid_stored_regex_fails_closed() -> None:
    # A legacy row that predates ``validate_path`` returns a matcher that
    # never matches — the opposite of today's silent wildcard.
    matcher = compile_matcher("[unterminated", "regex")
    assert matcher is not None
    assert matcher.never is True
    assert matcher.matches("/x") is False


def test_oversized_stored_pattern_fails_closed() -> None:
    matcher = compile_matcher("a" * (MAX_PATTERN_LENGTH + 1), "regex")
    assert matcher is not None
    assert matcher.never is True


# ---------------------------------------------------------------------------
# compile_matcher — literal modes
# ---------------------------------------------------------------------------


def test_prefix_matches_by_string_prefix() -> None:
    matcher = compile_matcher("/v1/users", "prefix")
    assert matcher is not None
    assert matcher.matches("/v1/users") is True
    assert matcher.matches("/v1/users/42") is True
    assert matcher.matches("/v2/users") is False


def test_exact_requires_full_string_equality() -> None:
    matcher = compile_matcher("/v1/users", "exact")
    assert matcher is not None
    assert matcher.matches("/v1/users") is True
    assert matcher.matches("/v1/users/42") is False
    assert matcher.matches("/v1/user") is False


@pytest.mark.parametrize("mode", ["prefix", "exact"])
def test_literal_modes_do_not_interpret_regex_metachars(mode: str) -> None:
    matcher = compile_matcher(".*", mode)
    assert matcher is not None
    # In literal modes, ``.*`` is the literal two-character string — not
    # a wildcard — so it does not match ``/foo``.
    assert matcher.matches(".*") is True
    assert matcher.matches("/foo") is False


def test_empty_stored_path_fails_closed_in_every_mode() -> None:
    # Symmetry with ``validate_path``: even if an empty string got past
    # validation it must not silently match everything.
    for mode in MATCH_MODES:
        matcher = compile_matcher("", mode)
        assert matcher is not None
        assert matcher.never is True
