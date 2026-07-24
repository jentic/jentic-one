"""Canonical Jentic-* response header names and the set of headers the broker
filters when proxying.

These are reused literals that are *not* a closed enum domain (header names), so
per the "No magic strings" rule (00-overview) they live as module-level constants
rather than inline string literals scattered across the routers.
"""

from __future__ import annotations

from enum import StrEnum


class JenticHeader(StrEnum):
    """Response header names the broker adds to every proxied response."""

    EXECUTION_ID = "Jentic-Execution-Id"
    TOOLKIT_ID = "Jentic-Toolkit-Id"
    OPERATION = "Jentic-Operation"
    API_VENDOR = "Jentic-Api-Vendor"
    # The credential the broker resolved and used for this execution (#740). The
    # id is stable + non-secret (uuid); the name is the human-readable label
    # from the stored row. Both are emitted only when a credential was actually
    # used — broker-origin failures before credential resolution carry neither.
    # Note: ``Jentic-Credential-Name`` is *also* a request header that clients
    # send inbound to disambiguate ambiguous resolution — the outbound response
    # header shares the name intentionally (same convention as
    # ``Jentic-Toolkit-Id``, which is both request and response).
    CREDENTIAL_ID = "Jentic-Credential-Id"
    CREDENTIAL_NAME = "Jentic-Credential-Name"
    UPSTREAM_STATUS = "Jentic-Upstream-Status"
    ERROR_ORIGIN = "Jentic-Error-Origin"
    HINT = "Jentic-Hint"
    IDEMPOTENT_REPLAYED = "Idempotent-Replayed"
    IDEMPOTENCY_BODY_OMITTED = "Jentic-Idempotency-Body-Omitted"


# Diagnostic hint appended (via the ``Jentic-Hint`` header, never by rewriting
# the mirrored upstream body — §6b B-002 passthrough invariant) when an upstream
# 401/403 lands on an API whose spec uses a templated host / server variable
# (e.g. ``https://{region}.posthog.com``). A valid key that still 401s here is
# very often a region/server-variable mismatch (#638), so we surface the likely
# cause without pretending to know the key is valid.
REGION_MISMATCH_HINT = (
    "This API requires a specific region or server variable. If your API key is "
    "valid, ensure your credential is configured for the correct region."
)


# The W3C ``tracestate`` response header — not a ``Jentic-*`` header but a
# reused literal, so it lives as a constant (00-overview "No magic strings").
# The broker echoes the packed ``jentic=`` vendor member on every response so a
# caller correlating a synchronous response to its trace gets the same
# who-is-calling/what-is-called payload it would see on the outbound request.
TRACESTATE_HEADER = "tracestate"


# The inbound multi-valued revision-pin request header (§10). A reused literal
# (read in the router, stripped from outbound forwarding via
# ``BROKER_CONSUMED_HEADERS``), so it lives as a module-level constant.
JENTIC_REVISION_HEADER = "jentic-revision"


# Security-bearing response headers scrubbed before a response is serialized into
# any broker-side store (idempotency replay cache here; the async jobs store in
# §05 shares this list). The broker must never hoard a valid upstream session
# token / API key for the replay window. Lower-cased for case-insensitive match.
SENSITIVE_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "set-cookie",
        "authorization",
        "proxy-authenticate",
        "www-authenticate",
        "x-api-key",
        "x-jentic-api-key",
    }
)


# Hop-by-hop headers (RFC 7230 §6.1) — a conformant proxy must not forward these,
# in either direction. ``host`` is included so the broker never forwards its own
# Host; httpx sets the correct upstream Host automatically.
HOP_BY_HOP_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)

# Headers the broker consumes itself — they steer broker behaviour and must not
# leak upstream.
BROKER_CONSUMED_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "prefer",
        "idempotency-key",
        "jentic-revision",
        "jentic-toolkit-id",
        "x-jentic-api-key",
        "jentic-credential-name",
    }
)

# Client-supplied forwarding/topology headers: stripped inbound (anti-spoof). The
# broker never trusts the caller's claim about the original IP/proto/host, and
# never leaks its internal topology to arbitrary third-party upstreams.
SPOOFABLE_HEADERS: frozenset[str] = frozenset(
    {
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-forwarded-port",
        "x-real-ip",
        "via",
    }
)
