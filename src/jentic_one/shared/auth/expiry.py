"""Expiry: the one place that decides whether an ``expires_at`` has passed.

Credential bindings (grants) are checked through this module by the broker,
the auth surface and the control plane, so a lapse lands at the same moment
everywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime | str) -> datetime:
    """A timezone-aware datetime from a stored expiry.

    A naive value, or the ISO string SQLite hands back from a raw query, is
    read as UTC.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def binding_expired(expires_at: datetime | str | None, *, now: datetime | None = None) -> bool:
    """``True`` when a credential binding's ``expires_at`` is set and at or before ``now``.

    ``None`` never expires; ``now`` defaults to the clock (the broker passes
    its injectable clock).
    """
    if expires_at is None:
        return False
    return as_utc(expires_at) <= (now or datetime.now(UTC))
