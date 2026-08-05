"""Shared permission-expansion helper.

Wraps the admin implication map (``compute_effective``) so non-web callers — e.g. a
service that must make an authorization decision *beyond* the route's declared scope —
can ask "does this identity effectively hold permission X?" without importing the admin
tier directly (which the module-boundary arch rules forbid for registry/broker/control).
This mirrors the expansion the request guard applies in ``shared/web/deps.py``: grants
are expanded through the static implication map (so ``org:admin`` implies everything and
``*:write`` implies ``*:read``) before the membership test.
"""

from __future__ import annotations

from collections.abc import Iterable

from jentic_one.admin.core.permissions import compute_effective


def has_effective_permission(granted: Iterable[str], required: str) -> bool:
    """True if *granted* (after implication-map expansion) includes *required*."""
    return required in compute_effective(set(granted))
