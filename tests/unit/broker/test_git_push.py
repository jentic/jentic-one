"""How a push is decided ref by ref: the rule level each update needs, and its reasons."""

from __future__ import annotations

import pytest

from jentic_one.broker.core.git_protocol import ZERO_SHA, RefCommand
from jentic_one.broker.core.git_push import (
    THIN_PACK_RETRY,
    non_fast_forward_reason,
    plan_command,
    rules_denied_reason,
    thin_pack_reason,
)
from jentic_one.shared.permissions.git_rules import GitAction

_A = "a" * 40
_B = "b" * 40


@pytest.mark.parametrize(
    ("old", "new", "ref", "action", "force_check"),
    [
        (ZERO_SHA, _B, "refs/heads/feat", GitAction.PUSH, False),
        (_A, _B, "refs/heads/feat", GitAction.PUSH, True),
        (_A, _A, "refs/heads/feat", GitAction.PUSH, False),
        (_A, ZERO_SHA, "refs/heads/feat", GitAction.FORCE, False),
        (ZERO_SHA, _B, "refs/tags/v2", GitAction.PUSH, False),
        # Tags are immutable: even a fast-forward move needs force.
        (_A, _B, "refs/tags/v1", GitAction.FORCE, False),
        (_A, ZERO_SHA, "refs/tags/v1", GitAction.FORCE, False),
    ],
)
def test_plan_command(old: str, new: str, ref: str, action: GitAction, force_check: bool) -> None:
    plan = plan_command(RefCommand(old=old, new=new, ref=ref), owner="o", repo="r")

    assert plan.item.action is action
    assert (plan.item.owner, plan.item.repo, plan.item.ref) == ("o", "r", ref)
    assert plan.needs_ancestry is force_check
    if force_check:
        assert plan.force_item is not None and plan.force_item.action is GitAction.FORCE


def test_reasons_name_the_ref_and_what_is_missing() -> None:
    assert "creating refs/heads/x" in rules_denied_reason(RefCommand(ZERO_SHA, _B, "refs/heads/x"))
    assert "pushing to refs/heads/x" in rules_denied_reason(RefCommand(_A, _B, "refs/heads/x"))
    assert "deleting refs/heads/x" in rules_denied_reason(RefCommand(_A, ZERO_SHA, "refs/heads/x"))
    assert "tags are immutable" in rules_denied_reason(RefCommand(_A, _B, "refs/tags/v1"))
    assert "non-fast-forward update of refs/heads/x" in non_fast_forward_reason(
        RefCommand(_A, _B, "refs/heads/x")
    )
    assert THIN_PACK_RETRY in thin_pack_reason()
    for reason in (thin_pack_reason(), rules_denied_reason(RefCommand(_A, _B, "refs/tags/v1"))):
        assert "\n" not in reason
