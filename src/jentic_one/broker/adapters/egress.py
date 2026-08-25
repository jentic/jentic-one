"""Egress safety adapter — re-export of the shared DNS-rebinding guard (§08 E2).

The implementation now lives in ``jentic_one.shared.egress`` because every
outbound-fetch site needs it (the broker execution runtime *and* the Flow-3
catalog/ingest fetchers), not just the broker. This module re-exports it so the
broker's existing imports (and the §00 layering references to a broker-owned
"egress adapter") keep resolving unchanged.
"""

from __future__ import annotations

from jentic_one.shared.egress import (
    DnsPinningTransport,
    build_pinned_transport,
    resolve_and_validate,
)

__all__ = ["DnsPinningTransport", "build_pinned_transport", "resolve_and_validate"]
