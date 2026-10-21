"""Connect-session revisions: the versioned ask a reviewer approves.

A revision pairs what the initiator sent (``proposal``) with what confirming
it would leave in place (``result``), and a digest over the session's
baseline, that result and the reason. Revisions are written through
:class:`ConnectSessionRevisionRepository`; this module only shapes them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jentic_one.control.services.integrations.api_targets import review_digest


@dataclass(frozen=True)
class RevisionPayload:
    """The three stored parts of one revision."""

    proposal: dict[str, Any]
    result: dict[str, Any]
    digest: str


def revision_digest(
    *, baseline: Mapping[str, Any] | None, result: Mapping[str, Any], reason: str | None
) -> str:
    """Stable SHA-256 over the baseline, the resolved result and the reason."""
    return review_digest({"baseline": baseline, "result": result, "reason": reason})


def create_revision(
    *,
    permission_rules: Sequence[Mapping[str, Any]],
    scopes: Sequence[str],
    reason: str | None,
) -> RevisionPayload:
    """A create session's revision: its full ask, with nothing to resolve against."""
    rules = [dict(rule) for rule in permission_rules]
    proposal: dict[str, Any] = {
        "permission_rules": rules,
        "scopes": list(scopes),
        "reason": reason,
    }
    result: dict[str, Any] = {"rules": rules, "scopes": sorted(set(scopes))}
    return RevisionPayload(
        proposal=proposal,
        result=result,
        digest=revision_digest(baseline=None, result=result, reason=reason),
    )
