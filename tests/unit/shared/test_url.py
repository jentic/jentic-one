"""Tests for public base-URL helpers in shared/url.py."""

from __future__ import annotations

import pytest

from jentic_one.shared.url import normalize_base_url, origins_equivalent


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://jentic.example.com", "https://jentic.example.com"),
        ("https://jentic.example.com/", "https://jentic.example.com"),
        ("http://127.0.0.1:8020/", "http://127.0.0.1:8020"),
        ("https://gw.example.com/base/", "https://gw.example.com/base"),
    ],
)
def test_normalize_base_url_ok(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://host",
        "host-without-scheme",
        "https://",
        "http://user:pass@host",  # userinfo must never end up in a link  # pragma: allowlist secret
        "https://ex.com?x=1",  # query string would corrupt derived links
        "https://ex.com#frag",  # fragment likewise
        "http://h:99999",  # port out of range — reject at load, not at runtime
        "http://h:8a",  # non-numeric port
    ],
)
def test_normalize_base_url_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_base_url(raw)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("http://127.0.0.1:8000", "http://localhost:8000"),
        ("http://0.0.0.0:8000", "http://127.0.0.1:8000"),
        ("http://localhost:80", "http://localhost"),  # default port folds
        ("https://host:443/x", "https://host/y"),  # path ignored for origin
    ],
)
def test_origins_equivalent_true(a: str, b: str) -> None:
    assert origins_equivalent(a, b) is True


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("http://127.0.0.1:8000", "http://127.0.0.1:8020"),  # port differs
        ("http://example.com", "https://example.com"),  # scheme differs
        ("http://example.com", "http://other.com"),  # host differs
        ("not-a-url", "http://example.com"),  # unparseable never matches
    ],
)
def test_origins_equivalent_false(a: str, b: str) -> None:
    assert origins_equivalent(a, b) is False


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("http://h:99999", "http://h:8000"),  # out-of-range port
        ("http://h:8a", "http://h:8000"),  # non-numeric port
    ],
)
def test_origins_equivalent_never_raises_on_bad_port(a: str, b: str) -> None:
    # Defense-in-depth: even a value that skipped config validation must not
    # crash the comparison — a bad port yields a non-comparable origin.
    assert origins_equivalent(a, b) is False
