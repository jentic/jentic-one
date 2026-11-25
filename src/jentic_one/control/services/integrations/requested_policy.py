"""The binding policy a connect request asks for, as stored on the session.

A connect request can say how long the access it asks for should last. The
ask is kept on the session row (``connect_sessions.requested_policy``) as a
small JSON object, so later limits can add keys without a migration. It is
only a request: the approver decides the binding's actual policy on confirm.
"""

from __future__ import annotations

from typing import Any


def requested_policy(
    *, expires_in: int | None = None, max_idle_period: int | None = None
) -> dict[str, Any] | None:
    """The JSON to store for a request, or ``None`` when it asks for nothing."""
    policy: dict[str, Any] = {}
    if expires_in is not None:
        policy["expires_in"] = int(expires_in)
    if max_idle_period is not None:
        policy["max_idle_period"] = int(max_idle_period)
    return policy or None


def requested_expires_in_of(policy: dict[str, Any] | None) -> int | None:
    """The requested duration in seconds, or ``None`` when none was asked."""
    value = (policy or {}).get("expires_in")
    return int(value) if isinstance(value, int | float) and value > 0 else None


def requested_max_idle_period_of(policy: dict[str, Any] | None) -> int | None:
    """The requested idle limit in seconds, or ``None`` when none was asked."""
    value = (policy or {}).get("max_idle_period")
    return int(value) if isinstance(value, int | float) and value > 0 else None
