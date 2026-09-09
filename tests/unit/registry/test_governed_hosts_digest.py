"""Unit tests for governed-hosts digest canonicalisation (#1278).

The digest is the change-poll contract: clients compare it (via ``ETag``)
across polls, so it must be insensitive to ordering, case, duplication, and
incidental whitespace — and stable for the empty set.
"""

from __future__ import annotations

import hashlib

from jentic_one.registry.services.governed_hosts_service import (
    canonical_hosts,
    compute_hosts_digest,
)


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


def test_digest_strips_fqdn_root_dot() -> None:
    """`api.example.com.` and `api.example.com` are one logical host — a spec
    importing the root-dot form must not fire a spurious ETag change."""
    assert compute_hosts_digest(["api.example.com."]) == compute_hosts_digest(["api.example.com"])
    assert canonical_hosts(["api.example.com.", "api.example.com"]) == ["api.example.com"]


def test_idn_hosts_canonicalise_to_the_a_label() -> None:
    """U-label and A-label spellings are one logical host, published in the
    A-label (punycode) form — the only one a TLS/SNI or DNS gate ever sees."""
    assert canonical_hosts(["münchen.example"]) == ["xn--mnchen-3ya.example"]
    assert compute_hosts_digest(["münchen.example"]) == compute_hosts_digest(
        ["xn--mnchen-3ya.example"]
    )


def test_unencodable_host_is_kept_not_dropped() -> None:
    """An entry IDNA cannot encode stays in the set verbatim (lowercased) —
    under-inclusion is the unsafe direction for a divert list."""
    weird = "münchen..example"  # empty label — the idna codec rejects it
    assert canonical_hosts([weird]) == [weird]


def test_canonicalisation_is_idempotent() -> None:
    hosts = ["API.Example.COM.", "münchen.example", "10.0.0.5", "api.example.com:8443"]
    once = canonical_hosts(hosts)
    assert canonical_hosts(once) == once


def test_empty_set_has_stable_documented_digest() -> None:
    """The empty set hashes the empty string — a stable sentinel clients can rely on."""
    assert compute_hosts_digest([]) == hashlib.sha256(b"").hexdigest()
    assert compute_hosts_digest([]) == compute_hosts_digest(())


def test_digest_is_canonical_newline_join() -> None:
    """Pin the canonical form (sorted, newline-joined) so it never drifts silently."""
    expected = hashlib.sha256(b"a.example.com\nb.example.com").hexdigest()
    assert compute_hosts_digest(["b.example.com", "a.example.com"]) == expected
