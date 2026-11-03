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
from jentic_one.control.services.integrations.edits import rule_content


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
    previous: Mapping[str, Any] | None = None,
    actor: str | None = None,
) -> RevisionPayload:
    """A create session's revision: its full ask, with nothing to resolve against.

    Each rule in the result records who put it in the request (``added_by``):
    carried from the ``previous`` revision's result when it was already
    there, else ``actor``, the one making this revision.
    """
    rules = [dict(rule) for rule in permission_rules]
    proposal: dict[str, Any] = {
        "permission_rules": rules,
        "scopes": list(scopes),
        "reason": reason,
    }
    result: dict[str, Any] = {
        "rules": _attribute(rules, previous, actor),
        "scopes": sorted(set(scopes)),
    }
    return RevisionPayload(
        proposal=proposal,
        result=result,
        digest=revision_digest(baseline=None, result=result, reason=reason),
    )


def _attribute(
    rules: list[dict[str, Any]], previous: Mapping[str, Any] | None, actor: str | None
) -> list[dict[str, Any]]:
    """Each rule with ``added_by``: whoever first asked for that rule."""
    earlier = [(rule_content(r), r.get("added_by")) for r in (previous or {}).get("rules", [])]
    out: list[dict[str, Any]] = []
    for rule in rules:
        content = rule_content(rule)
        match = next((i for i, (c, _) in enumerate(earlier) if c == content), None)
        added_by = earlier.pop(match)[1] if match is not None else actor
        out.append({**rule, "added_by": added_by})
    return out
