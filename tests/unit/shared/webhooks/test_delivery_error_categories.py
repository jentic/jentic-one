"""Unit tests for the delivery error categoriser.

``_categorize_error`` maps a send exception to a **stable, non-sensitive**
label from a closed set. These are pure-function tests (no DB, no network): each
asserts one exception type maps to the expected reason, plus the two properties
that make the mapping safe —

* an **allowlist** block is distinguished from an **egress-policy** block (the
  motivating bug: an operator's own ``allowed_cidrs`` rejecting a real IP must
  read differently from the SSRF policy rejecting a private/metadata range); and
* **no internal IP or raw exception text ever leaks** into the returned label —
  even when the exception message embeds one.
"""

from __future__ import annotations

import ssl

import httpx
import pytest

from jentic_one.shared.url_validation import (
    AllowlistBlockedError,
    DnsResolutionError,
    EgressBlockedError,
)
from jentic_one.shared.webhooks.delivery import _categorize_error

# The full closed set of categories the mapping may ever return from an
# exception path. If a new reason is added, this set (and the UI map) must grow
# with it — the test asserts membership so a stray raw string can't sneak in.
_SAFE_CATEGORIES = frozenset(
    {
        "blocked_by_allowlist",
        "blocked_egress_policy",
        "dns_unresolved",
        "connection_timeout",
        "read_timeout",
        "connect_failed",
        "tls_error",
        "protocol_error",
        "transport_error",
        "delivery_error",
    }
)


def test_allowlist_block_is_its_own_reason() -> None:
    """The endpoint's own allowlist rejecting an IP → ``blocked_by_allowlist``.

    Distinct from the egress-policy block below — this is the config the operator
    controls, so the UI points them at Settings → Advanced.
    """
    exc = AllowlistBlockedError("upstream URL resolves to a blocked address range")
    assert _categorize_error(exc) == "blocked_by_allowlist"


def test_egress_policy_block_is_distinct_from_allowlist() -> None:
    """A private/loopback/metadata refusal by the SSRF policy → ``blocked_egress_policy``."""
    exc = EgressBlockedError("upstream URL resolves to a blocked address range")
    assert _categorize_error(exc) == "blocked_egress_policy"


def test_allowlist_error_is_a_subclass_but_maps_specifically() -> None:
    """``AllowlistBlockedError`` ⊂ ``EgressBlockedError`` — order must favour the specific one."""
    assert issubclass(AllowlistBlockedError, EgressBlockedError)
    assert _categorize_error(AllowlistBlockedError("x")) == "blocked_by_allowlist"
    assert _categorize_error(EgressBlockedError("x")) == "blocked_egress_policy"


def test_dns_failure_maps_to_dns_unresolved() -> None:
    exc = DnsResolutionError("upstream host did not resolve: receiver.test")
    assert _categorize_error(exc) == "dns_unresolved"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectTimeout("t"), "connection_timeout"),
        (httpx.PoolTimeout("t"), "connection_timeout"),
        (httpx.ReadTimeout("t"), "read_timeout"),
        (httpx.WriteTimeout("t"), "read_timeout"),
        (httpx.ConnectError("refused"), "connect_failed"),
        (httpx.ProtocolError("bad"), "protocol_error"),
    ],
)
def test_transport_exceptions_map_to_stable_categories(exc: Exception, expected: str) -> None:
    assert _categorize_error(exc) == expected


def test_tls_handshake_failure_maps_to_tls_error() -> None:
    """A ConnectError whose cause is an ssl.SSLError → ``tls_error``.

    httpx wraps a handshake failure in a ConnectError; only the *type* of the
    cause is inspected, never its message.
    """
    connect = httpx.ConnectError("connection failed")
    connect.__cause__ = ssl.SSLError("CERTIFICATE_VERIFY_FAILED")
    assert _categorize_error(connect) == "tls_error"


def test_unknown_exception_falls_back_to_delivery_error() -> None:
    assert _categorize_error(RuntimeError("something odd")) == "delivery_error"


def test_no_category_leaks_ip_or_raw_text() -> None:
    """Every category is a fixed safe label — never the exception's message.

    Each exception below carries an internal-looking IP / host in its message;
    the returned label must be from the closed safe set and contain none of it.
    """
    leaky_cases = [
        AllowlistBlockedError("blocked 93.184.216.34 not in 192.0.2.0/24"),
        EgressBlockedError("resolves to 169.254.169.254"),
        DnsResolutionError("upstream host did not resolve: secret-internal.corp"),
        httpx.ConnectError("failed to connect to 10.0.0.5:443"),
        httpx.ConnectTimeout("timeout dialing 172.16.9.9"),
    ]
    for exc in leaky_cases:
        category = _categorize_error(exc)
        assert category in _SAFE_CATEGORIES
        # None of the sensitive fragments may appear in the stored label.
        for sensitive in ("93.184", "169.254", "10.0.0.5", "172.16", "corp", "192.0.2"):
            assert sensitive not in category
