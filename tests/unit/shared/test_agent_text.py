"""Unit tests for cleaning the free text an agent writes for a reviewer."""

from __future__ import annotations

import pytest

from jentic_one.shared.agent_text import MAX_AGENT_TEXT_LEN, clean_agent_text


@pytest.mark.parametrize("raw", [None, "", "   ", " \n\t ", "\x00\x07"])
def test_absent_or_blank_text_is_none(raw: str | None) -> None:
    assert clean_agent_text(raw) is None


def test_plain_text_passes_through() -> None:
    text = "Customer 4821 asked for a refund"
    assert clean_agent_text(text) == text


def test_non_ascii_text_is_kept() -> None:
    text = "Remboursement demandé — ticket #4821 (发布)"
    assert clean_agent_text(text) == text


def test_percent_signs_and_plus_are_literal() -> None:
    assert clean_agent_text("50% off, 1+1 is 2, %ZZ") == "50% off, 1+1 is 2, %ZZ"


def test_controls_dropped_newlines_and_tabs_kept() -> None:
    assert clean_agent_text("line one\r\nline\ttwo\x1b[31m") == "line one\nline\ttwo[31m"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Bidi overrides and zero-width marks (Cf) can reorder or hide text: dropped.
        ("approve \u202e0005 evoments", "approve 0005 evoments"),
        ("zero\u200bwidth\ufeff", "zerowidth"),
        # Carriage returns and line and paragraph separators read as line breaks.
        ("one\rtwo\u2028three\u2029four", "one\ntwo\nthree\nfour"),
        # Private-use and unassigned code points carry nothing a reviewer can read.
        ("x\ue000y\u0378z", "xyz"),
    ],
)
def test_format_separator_private_and_unassigned_characters(raw: str, expected: str) -> None:
    assert clean_agent_text(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "\U0001f469\u200d\U0001f4bb on call",  # an emoji sequence joined by ZWJ
        "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645",  # Persian, with a ZWNJ
    ],
)
def test_the_zero_width_joiner_and_non_joiner_are_kept(text: str) -> None:
    assert clean_agent_text(text) == text


def test_spaces_are_not_line_breaks() -> None:
    assert clean_agent_text("a b  c") == "a b  c"


def test_a_lone_surrogate_is_dropped_so_the_result_always_encodes() -> None:
    out = clean_agent_text("\ud800x")
    assert out == "x"
    assert out.encode("utf-8") == b"x"


def test_capped_at_max_length_and_trimmed() -> None:
    out = clean_agent_text("  " + "x" * (MAX_AGENT_TEXT_LEN + 50))
    assert out is not None
    assert len(out) == MAX_AGENT_TEXT_LEN


def test_a_cut_that_ends_in_space_is_trimmed() -> None:
    out = clean_agent_text("x" * (MAX_AGENT_TEXT_LEN - 1) + "  yyy")
    assert out == "x" * (MAX_AGENT_TEXT_LEN - 1)
