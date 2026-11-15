"""Property-based tests for shared URL-path normalization."""

from __future__ import annotations

import re
from urllib.parse import unquote

from hypothesis import given
from hypothesis import strategies as st

from jentic_one.shared.url_path import normalize_path

_KEPT_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"%2[Ff]"), "\x00SLASH\x00", "%2F"),
    (re.compile(r"%5[Cc]"), "\x00BSLASH\x00", "%5C"),
)

_SEGMENT_TOKENS = ("a", "b", "0", "9", "-", "_", "%2f", "%2F", "%5c", "%5C", "%25")
_SAFE_SEGMENTS = st.lists(
    st.lists(st.sampled_from(_SEGMENT_TOKENS), min_size=1, max_size=8).map("".join),
    min_size=1,
    max_size=6,
)


def _reference_normalize_no_pct25(path: str) -> str:
    """Reference single-pass normalizer using urllib.parse.unquote per segment."""
    shielded = path
    for pattern, sentinel, _escaped in _KEPT_PATTERNS:
        shielded = pattern.sub(sentinel, shielded)
    decoded_segments = [unquote(seg, encoding="latin-1") for seg in shielded.split("/")]
    restored_segments: list[str] = []
    for seg in decoded_segments:
        restored = seg
        for _pattern, sentinel, escaped in _KEPT_PATTERNS:
            restored = restored.replace(sentinel, escaped)
        restored_segments.append(restored)

    output: list[str] = []
    for seg in restored_segments:
        if seg == ".":
            continue
        if seg == "..":
            if output:
                output.pop()
        else:
            output.append(seg)
    resolved = "/".join(output)
    if resolved and not resolved.startswith("/"):
        resolved = "/" + resolved
    return resolved.rstrip("/") or "/"


@given(path=st.text(alphabet="%/.\\aA0123456789fF"))
def test_normalize_path_never_raises_and_is_absolute(path: str) -> None:
    """normalize_path is total and always returns a leading-slash path without trailing slash."""
    normalized = normalize_path(path)
    assert normalized.startswith("/")
    if len(normalized) > 1:
        assert not normalized.endswith("/")


@given(segments=_SAFE_SEGMENTS)
def test_kept_escapes_survive(segments: list[str]) -> None:
    """%2F, %5C, and %25 remain uppercase-encoded and never split into new path segments."""
    raw = "/" + "/".join(segments)
    normalized = normalize_path(raw)
    out_segments = normalized.split("/")[1:]

    assert len(out_segments) == len(segments)
    assert "%2f" not in normalized
    assert "%5c" not in normalized
    assert normalized.count("%2F") == sum(seg.lower().count("%2f") for seg in segments)
    assert normalized.count("%5C") == sum(seg.lower().count("%5c") for seg in segments)
    assert normalized.count("%25") == sum(seg.count("%25") for seg in segments)


@given(
    path=st.text(alphabet="%/.\\aA0123456789fF").filter(lambda s: "%25" not in s),
)
def test_single_decode_only(path: str) -> None:
    """For inputs without %25, normalize_path matches per-segment unquote with kept escapes."""
    assert normalize_path(path) == _reference_normalize_no_pct25(path)


def test_normalize_path_is_not_idempotent_on_constructed_escapes() -> None:
    """Pin single-pass behavior when a % precedes escapes that decode to hex digits."""
    once = normalize_path("/%%36%31dmin")
    assert once == "/%61dmin"
    assert normalize_path(once) == "/admin"
