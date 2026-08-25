"""Shared permission-expansion helper.

Wraps the shared implication map (``compute_effective``) so non-web callers — e.g. a
service that must make an authorization decision *beyond* the route's declared scope —
can ask "does this identity effectively hold permission X?" without reaching up into the
admin tier (the module-boundary arch rules forbid a registry/broker/control → admin
import; the implication data now lives in ``shared.auth.permission_catalog``, #938).
This mirrors the expansion the request guard applies in ``shared/web/deps.py``: grants
are expanded through the static implication map (so ``org:admin`` implies everything and
``*:write`` implies ``*:read``) before the membership test.
"""

from __future__ import annotations

from collections.abc import Iterable

from jentic_one.shared.auth.permission_catalog import compute_effective


def has_effective_permission(granted: Iterable[str], required: str) -> bool:
    """True if *granted* (after implication-map expansion) includes *required*."""
    return required in compute_effective(set(granted))
