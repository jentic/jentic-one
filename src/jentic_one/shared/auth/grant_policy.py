"""A credential grant's policy: whether it has ended, when it will, and why.

A grant is the record that carries the policy: today the agent-to-credential
binding. It can carry any combination of limits, and it ends as soon as **any**
of them is reached:

- an end date (``expires_at``);
- an idle limit (``max_idle_period``, whole seconds): the grant ends once it
  has gone that long without a use. The idle clock runs from the later of the
  last use (``last_used_at``) and the last grant or renewal (``renewed_at``,
  falling back to ``bound_at``), so a grant that is never used ends one idle
  period after it was granted, and a renewal restarts the clock without
  counting as a use;
- a use count (``max_quota``): the grant ends once ``quota_remaining`` reaches
  zero. The broker takes one use per call through the grant, failed or not
  (a call its rules refuse included), with an atomic
  decrement, and a renewal restores the count. Single use is ``max_quota=1``.

This module is the one place that decides it, so the broker, the auth surface
and the control plane agree on the moment and on the reason they report
(``expired_reason``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from typing import Any

from jentic_one.shared.auth.expiry import as_utc

#: The shortest idle limit a grant may carry: ``last_used_at`` is written only
#: about once an hour, so shorter limits could not be honoured exactly.
MIN_IDLE_PERIOD_SECONDS = 86_400

#: The longest idle limit (999 days, the console's largest custom amount).
MAX_IDLE_PERIOD_SECONDS = 999 * 86_400

#: SQL column list a raw query selects to build a :class:`GrantPolicy`.
GRANT_POLICY_COLUMNS = (
    "expires_at, max_idle_period, last_used_at, renewed_at, bound_at, max_quota, quota_remaining"
)

#: The largest use count a grant may carry (the column is a 32-bit integer).
MAX_QUOTA = 2_147_483_647


class Unset(Enum):
    """A policy field a change leaves alone, as opposed to ``None``, which clears it."""

    TOKEN = 0


UNSET = Unset.TOKEN

#: The binding columns that make up its policy, as audits record them.
GRANT_POLICY_FIELDS = (
    "expires_at",
    "max_idle_period",
    "idle_warning",
    "max_quota",
    "after_expiry",
)


class AfterExpiry(StrEnum):
    """What a call through a binding gets once the binding has ended."""

    HOLD = "hold"
    """The first such call is held as a ``grant_renewal`` for the owner to renew and run."""
    REFUSE = "refuse"
    """A plain ``403 credential_binding_expired``: the agent asks its owner to renew."""


#: API description of a binding's ``idle_warning`` (shared by every surface).
WARNING_DESCRIPTION = (
    "Warn the agent's owner before the binding ends: once a day a job raises a"
    " `credential_grant.expiring` event when its end date is within 48 hours (for a"
    " binding granted for 4 days or more), and a `credential_grant.idle_expiring` event"
    " when it will go idle within 48 hours (for idle limits of 4 days or more); each"
    " once per end date or idle period. Off unless set."
)

#: API description of a binding's ``after_expiry`` (shared by every surface).
AFTER_EXPIRY_DESCRIPTION = (
    "What a call through the binding gets once it has ended. `hold` (the default): the"
    " first such call is held as a `grant_renewal` for the agent's owner to renew the"
    " binding and run it; later calls get `403 credential_binding_expired` while that"
    " review is pending. `refuse`: every call gets `403 credential_binding_expired`,"
    " whose directive tells the agent to ask its owner to renew."
)


class GrantEndReason(StrEnum):
    """Which limit ended a grant, as binding responses and denials report it."""

    END_DATE = "end_date"
    """``expires_at`` has passed."""
    IDLE = "idle"
    """It went ``max_idle_period`` without a use."""
    USES = "uses"
    """No uses remain (``quota_remaining`` reached zero)."""


@dataclass(frozen=True, slots=True)
class GrantPolicy:
    """The limits a grant carries and the clock they run against.

    Every limit ``None`` means the grant never ends. Datetimes may be naive or
    the ISO strings SQLite hands back from a raw query; both read as UTC.
    """

    expires_at: datetime | str | None = None
    max_idle_period: int | None = None
    last_used_at: datetime | str | None = None
    renewed_at: datetime | str | None = None
    bound_at: datetime | str | None = None
    max_quota: int | None = None
    quota_remaining: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> GrantPolicy:
        """The policy columns of an ORM object or a SQL row (missing columns read as unset)."""
        return cls(
            expires_at=getattr(row, "expires_at", None),
            max_idle_period=getattr(row, "max_idle_period", None),
            last_used_at=getattr(row, "last_used_at", None),
            renewed_at=getattr(row, "renewed_at", None),
            bound_at=getattr(row, "bound_at", None),
            max_quota=getattr(row, "max_quota", None),
            quota_remaining=getattr(row, "quota_remaining", None),
        )


def _utc(value: datetime | str | None) -> datetime | None:
    return as_utc(value) if value is not None else None


def idle_clock_start(policy: GrantPolicy) -> datetime | None:
    """When the idle clock last restarted: the later of the last use and the last renewal."""
    starts = [
        start
        for start in (_utc(policy.last_used_at), _utc(policy.renewed_at) or _utc(policy.bound_at))
        if start is not None
    ]
    return max(starts, default=None)


def idle_deadline(policy: GrantPolicy) -> datetime | None:
    """When the idle limit ends the grant if it goes unused, or ``None`` without one."""
    if policy.max_idle_period is None:
        return None
    start = idle_clock_start(policy)
    return start + timedelta(seconds=policy.max_idle_period) if start is not None else None


def counts_uses(policy: GrantPolicy) -> bool:
    """Whether the grant carries a use count (every call must reach the counter)."""
    return policy.max_quota is not None


def uses_exhausted(policy: GrantPolicy) -> bool:
    """Whether the grant's use count has run out."""
    return counts_uses(policy) and (policy.quota_remaining or 0) <= 0


def grant_deadline(policy: GrantPolicy) -> datetime | None:
    """The earliest moment a time-based limit ends the grant, or ``None``."""
    deadlines = [d for d in (_utc(policy.expires_at), idle_deadline(policy)) if d is not None]
    return min(deadlines, default=None)


def grant_end_reason(policy: GrantPolicy, *, now: datetime | None = None) -> GrantEndReason | None:
    """Why the grant has ended as of ``now`` (default: the clock), or ``None`` while it holds.

    When several limits have been reached, the one reached first is reported.
    A used-up count is dated by the grant's last recorded use (only roughly
    known: it is stamped about once an hour).
    """
    now = now or datetime.now(UTC)
    reached = [
        (deadline, reason)
        for deadline, reason in (
            (_utc(policy.expires_at), GrantEndReason.END_DATE),
            (idle_deadline(policy), GrantEndReason.IDLE),
        )
        if deadline is not None and deadline <= now
    ]
    if uses_exhausted(policy):
        used_up = idle_clock_start(policy) or datetime.min.replace(tzinfo=UTC)
        reached.append((min(used_up, now), GrantEndReason.USES))
    return min(reached, key=lambda pair: pair[0])[1] if reached else None


def grant_ended(policy: GrantPolicy, *, now: datetime | None = None) -> bool:
    """Whether any limit of the grant has been reached as of ``now``."""
    return grant_end_reason(policy, now=now) is not None


def idle_warning_due(
    policy: GrantPolicy,
    *,
    now: datetime,
    window: timedelta,
    min_idle_period: int,
) -> datetime | None:
    """The idle deadline of a binding due an idle warning, else ``None``.

    Due: the idle limit is at least ``min_idle_period`` seconds, its deadline
    is ahead of ``now`` but within ``window``, and no limit has ended the
    binding yet. The caller checks the binding's own ``idle_warning`` flag.
    """
    if policy.max_idle_period is None or policy.max_idle_period < min_idle_period:
        return None
    deadline = idle_deadline(policy)
    if deadline is None or not now < deadline <= now + window:
        return None
    if grant_end_reason(policy, now=now) is not None:
        return None
    return deadline


def expiry_warning_due(
    policy: GrantPolicy,
    *,
    now: datetime,
    window: timedelta,
    min_length: int,
) -> datetime | None:
    """The end date of a binding due an end-date warning, else ``None``.

    Due: the binding has an end date ahead of ``now`` but within ``window``,
    it was granted (or last renewed) at least ``min_length`` seconds before
    that end date, so the warning means something, and no limit has ended
    it yet. The caller checks the binding's own ``idle_warning`` flag.
    """
    end = _utc(policy.expires_at)
    if end is None or not now < end <= now + window:
        return None
    granted = _utc(policy.renewed_at) or _utc(policy.bound_at)
    if granted is not None and (end - granted).total_seconds() < min_length:
        return None
    if grant_end_reason(policy, now=now) is not None:
        return None
    return end
