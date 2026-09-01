"""Unit tests for the redirect-URI-set fingerprint helper (design §4.1 / D8)."""

from __future__ import annotations

import hashlib

from jentic_one.admin.repos.oauth_client_repo import redirect_uris_fingerprint


def test_fingerprint_is_sha256_of_sorted_newline_joined_set() -> None:
    uris = ["https://b.example.com/cb", "https://a.example.com/cb"]
    expected = hashlib.sha256(b"https://a.example.com/cb\nhttps://b.example.com/cb").hexdigest()
    assert redirect_uris_fingerprint(uris) == expected
    assert len(redirect_uris_fingerprint(uris)) == 64  # fits the String(64) column


def test_fingerprint_is_order_insensitive() -> None:
    """D8: a reordered-but-equal redirect set must dedupe to the same row."""
    a = ["https://a.example.com/cb", "https://b.example.com/cb"]
    b = ["https://b.example.com/cb", "https://a.example.com/cb"]
    assert redirect_uris_fingerprint(a) == redirect_uris_fingerprint(b)


def test_fingerprint_is_exact_on_set_membership() -> None:
    """Any added, removed, or altered URI changes the fingerprint."""
    base = ["https://a.example.com/cb"]
    assert redirect_uris_fingerprint(base) != redirect_uris_fingerprint(
        ["https://a.example.com/cb", "https://b.example.com/cb"]
    )
    assert redirect_uris_fingerprint(base) != redirect_uris_fingerprint(
        ["https://a.example.com/cb2"]
    )
