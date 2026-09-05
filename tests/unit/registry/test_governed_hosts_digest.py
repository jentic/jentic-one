"""Unit tests for governed-hosts digest canonicalisation (#1278).

The digest is the change-poll contract: clients compare it (via ``ETag``)
across polls, so it must be insensitive to ordering, case, duplication, and
incidental whitespace — and stable for the empty set.
"""

from __future__ import annotations

import hashlib

from jentic_one.registry.services.governed_hosts_service import compute_hosts_digest


def test_digest_is_order_insensitive() -> None:
    assert compute_hosts_digest(["b.example.com", "a.example.com"]) == compute_hosts_digest(
        ["a.example.com", "b.example.com"]
    )


def test_digest_is_case_insensitive() -> None:
    assert compute_hosts_digest(["API.Example.COM"]) == compute_hosts_digest(["api.example.com"])


def test_digest_deduplicates() -> None:
    assert compute_hosts_digest(
        ["a.example.com", "A.example.com", "a.example.com"]
    ) == compute_hosts_digest(["a.example.com"])


def test_digest_strips_whitespace_and_drops_empties() -> None:
    assert compute_hosts_digest([" a.example.com ", "", "   "]) == compute_hosts_digest(
        ["a.example.com"]
    )


def test_empty_set_has_stable_documented_digest() -> None:
    """The empty set hashes the empty string — a stable sentinel clients can rely on."""
    assert compute_hosts_digest([]) == hashlib.sha256(b"").hexdigest()
    assert compute_hosts_digest([]) == compute_hosts_digest(())


def test_digest_is_canonical_newline_join() -> None:
    """Pin the canonical form (sorted, newline-joined) so it never drifts silently."""
    expected = hashlib.sha256(b"a.example.com\nb.example.com").hexdigest()
    assert compute_hosts_digest(["b.example.com", "a.example.com"]) == expected
