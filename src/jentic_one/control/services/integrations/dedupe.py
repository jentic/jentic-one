"""Keys for the one-open-session-per-request dedupe of connect sessions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


def oauth_dedupe_key(
    *, resolved_flow: str, registration_id: str | None, requested_scopes: Iterable[str]
) -> str:
    """Digest of what an agent-started vendor session asks for.

    Two asks are the same when they run the same flow through the same OAuth
    app (``registration_id``; ``None`` is the platform config entry) for the
    same set of scopes — order and repeats do not matter. The control
    migration that backfills open sessions computes the same digest; the two
    must stay identical.
    """
    payload = json.dumps(
        [resolved_flow, registration_id or "", sorted(set(requested_scopes))],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
