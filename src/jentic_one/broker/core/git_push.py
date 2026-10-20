"""How the governed git pipeline decides a push, ref by ref.

A push carries one ``old → new`` command per ref. Each command is first held
to the limits no rule can lift (:func:`~jentic_one.broker.core.git_protocol.ref_hard_deny_reason`),
then classified by the rule level it needs (:func:`plan_command`):

- a create (``old`` all zeros) needs ``push``;
- a deletion (``new`` all zeros) needs ``force``;
- an update of an existing tag needs ``force``, even a fast-forward: tags
  are immutable unless a rule allows force on them;
- a branch update needs ``push``, and ``force`` unless it is shown to be a
  fast-forward: the force-push check proves it from the pack
  (``broker.core.git_pack``) and GitHub's compare API.

Any refused ref refuses the whole push: the pipeline forwards the client's
original request once or not at all, so ``--atomic`` and the old-SHA
compare-and-swap keep GitHub's own semantics, and every other ref of a
refused push is reported as not applied.

The ``*_reason`` helpers build the one-line reasons a refused ref carries in
report-status (``ng <ref> jentic: <reason>``), which git prints as
``! [remote rejected] <ref> (jentic: <reason>)``.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

from dataclasses import dataclass

from jentic_one.broker.core.git_protocol import RefCommand, is_tag
from jentic_one.shared.permissions.git_rules import GitAction, GitItem

#: The retry directive for a push whose pack the force-push check cannot resolve.
THIN_PACK_RETRY = "git -c pack.useThin=false push"


@dataclass(frozen=True, slots=True)
class RefPlan:
    """What one ref update needs from the rules.

    ``item`` is the update at the level it needs up front (``push``, or
    ``force`` for a deletion or a tag update). ``force_item`` is set for a
    branch update: allowed, it skips the force-push check; otherwise the
    update must be shown to be a fast-forward.
    """

    command: RefCommand
    item: GitItem
    force_item: GitItem | None = None

    @property
    def needs_ancestry(self) -> bool:
        """Whether the update must be proven a fast-forward when force is not allowed."""
        return self.force_item is not None


def plan_command(command: RefCommand, *, owner: str, repo: str) -> RefPlan:
    """The rule items one ref update of a push to ``owner/repo`` needs (names normalized)."""
    if command.is_delete or (is_tag(command.ref) and not command.is_create):
        return RefPlan(command, GitItem(GitAction.FORCE, owner, repo, command.ref))
    push = GitItem(GitAction.PUSH, owner, repo, command.ref)
    if command.is_create or command.old == command.new:
        return RefPlan(command, push)
    return RefPlan(command, push, GitItem(GitAction.FORCE, owner, repo, command.ref))


def rules_denied_reason(command: RefCommand) -> str:
    """The reason for a ref update the binding's git rules do not allow at its level."""
    ref = command.ref
    if command.is_delete:
        return f"deleting {ref} is not allowed by the binding's git rules (it needs force)"
    if is_tag(ref) and not command.is_create:
        return (
            f"tags are immutable: moving or deleting {ref} needs a force rule on it, "
            "which the binding's git rules do not have"
        )
    if command.is_create:
        return f"creating {ref} is not allowed by the binding's git rules"
    return f"pushing to {ref} is not allowed by the binding's git rules"


def non_fast_forward_reason(command: RefCommand) -> str:
    """The reason for a branch update shown not to be a fast-forward."""
    return (
        f"non-fast-forward update of {command.ref} (a force push) is not allowed by the "
        "binding's git rules; pull and rebase or merge, then push again"
    )


def thin_pack_reason() -> str:
    """The reason for an update whose commits the force-push check could not resolve."""
    return (
        "Jentic could not check this update is a fast-forward: the pack stores a commit "
        f"against an object outside it. Retry with: {THIN_PACK_RETRY}"
    )


def unknown_commit_reason(sha: str) -> str:
    """The reason for an update naming a commit neither the pack nor GitHub has."""
    return f"commit {sha[:12]} is neither in the push nor on GitHub"


def unverified_reason() -> str:
    """The reason for an update GitHub's compare API gave no usable answer on."""
    return (
        "Jentic could not verify this update is a fast-forward (GitHub's compare API did "
        "not answer); push again"
    )


def too_many_checks_reason(limit: int) -> str:
    """The reason for an update left unchecked once the compare-call budget ran out."""
    return (
        f"Jentic could not verify this update is a fast-forward within {limit} GitHub "
        "compare calls; push fewer refs or merges at once"
    )


def push_too_large_reason(limit: int) -> str:
    """The reason every ref of a push over the size limit carries."""
    return f"the push is larger than Jentic's {limit}-byte limit for git; push less at once"


def unreadable_pack_reason(detail: str) -> str:
    """The reason every ref of a push whose pack cannot be read carries."""
    return f"Jentic could not read the pushed pack: {detail}"


#: The reason every ref a push did not apply carries when another ref of it was refused.
NOT_APPLIED_REASON = "not applied: another ref in this push was refused, so nothing in it moved"


__all__ = [
    "NOT_APPLIED_REASON",
    "THIN_PACK_RETRY",
    "RefPlan",
    "non_fast_forward_reason",
    "plan_command",
    "push_too_large_reason",
    "rules_denied_reason",
    "thin_pack_reason",
    "too_many_checks_reason",
    "unknown_commit_reason",
    "unreadable_pack_reason",
    "unverified_reason",
]
