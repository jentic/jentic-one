"""Edit sessions: resolving a requested change against a binding's baseline.

An edit session changes an existing ``(agent, credential)`` binding instead of
creating a credential. When it opens, the binding is snapshot as its
**baseline**: the binding's own rules (with ids), the shared rule set it points
at (with that set's rules), whether it is suspended, and the credential's
granted scopes. Every revision of the edit resolves the initiator's request
against that baseline into one **result**, the access confirming it would
leave in place, so ``add``/``remove`` and ``set`` produce the same thing to
review. Rules and scopes are changed the same way.

While a shared rule set is attached its rules are the ones that apply, so an
edit changes the set's rules; otherwise it changes the binding's own rules.
Pure functions only: the service reads the baseline and writes the result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jentic_one.control.services.integrations.errors import (
    NothingToChangeError,
    ScopesNotSupportedError,
    UnknownRuleError,
)

#: The rule fields an edit compares and writes (ids and system flags excluded).
RULE_FIELDS: tuple[str, ...] = ("effect", "methods", "path", "match_mode", "operations", "comment")

#: Effects that let a call through, so adding one widens access.
_GRANTING_EFFECTS = frozenset({"allow", "require-approval"})

#: ``result.target``: which rule list the edit changes.
TARGET_BINDING = "binding"
TARGET_RULE_SET = "rule_set"


@dataclass(frozen=True)
class RuleAdd:
    """One rule to add, before the rule id ``before`` (appended when ``None``)."""

    rule: dict[str, Any]
    before: str | None = None


@dataclass(frozen=True)
class EditRequest:
    """What an edit asks for, relative to the baseline.

    For rules, either ``add``/``remove`` or ``set`` (the full rule list, which
    also covers re-ordering); for scopes, either ``add_scopes``/``remove_scopes``
    or ``set_scopes``. The router refuses a request carrying both of a pair.
    """

    add: list[RuleAdd] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    set_rules: list[dict[str, Any]] | None = None
    add_scopes: list[str] = field(default_factory=list)
    remove_scopes: list[str] = field(default_factory=list)
    set_scopes: list[str] | None = None
    resume: bool = False
    reason: str | None = None

    @classmethod
    def from_proposal(cls, proposal: Mapping[str, Any]) -> EditRequest:
        """Rebuild the request a revision stored with :meth:`as_proposal`."""
        rules = proposal.get("rules") or {}
        set_rules = rules.get("set")
        scopes = proposal.get("scopes") or {}
        return cls(
            add=[
                RuleAdd(rule=dict(a["rule"]), before=a.get("before")) for a in rules.get("add", [])
            ],
            remove=list(rules.get("remove", [])),
            set_rules=[dict(r) for r in set_rules] if set_rules is not None else None,
            add_scopes=list(scopes.get("add", [])),
            remove_scopes=list(scopes.get("remove", [])),
            set_scopes=list(scopes["set"]) if scopes.get("set") is not None else None,
            resume=bool(proposal.get("resume", False)),
            reason=proposal.get("reason"),
        )

    def as_proposal(self) -> dict[str, Any]:
        """The request as stored on its revision."""
        rules: dict[str, Any]
        if self.set_rules is not None:
            rules = {"set": [dict(r) for r in self.set_rules]}
        else:
            rules = {
                "add": [{"rule": dict(a.rule), "before": a.before} for a in self.add],
                "remove": list(self.remove),
            }
        scopes: dict[str, Any] = (
            {"set": list(self.set_scopes)}
            if self.set_scopes is not None
            else {"add": list(self.add_scopes), "remove": list(self.remove_scopes)}
        )
        return {
            "rules": rules,
            "scopes": scopes,
            "resume": self.resume,
            "reason": self.reason,
        }


def rule_content(rule: Mapping[str, Any]) -> dict[str, Any]:
    """A rule's comparable content: its fields, with ``None`` for each one unset.

    ``match_mode`` defaults to ``regex``, as the rule schemas do, so a rule
    sent without it equals the stored one.
    """
    content = {key: rule.get(key) for key in RULE_FIELDS}
    content["match_mode"] = content["match_mode"] or "regex"
    return content


def rule_to_write(rule: Mapping[str, Any]) -> dict[str, object]:
    """A result rule as the rule repositories write it (no id, no unset fields)."""
    return {key: value for key, value in rule_content(rule).items() if value is not None}


def target_rules(baseline: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """The rule list an edit changes, and which one it is."""
    rule_set = baseline.get("rule_set")
    if rule_set is not None:
        return TARGET_RULE_SET, [dict(r) for r in rule_set["rules"]]
    return TARGET_BINDING, [dict(r) for r in baseline["rules"]]


def resolve_edit(
    baseline: Mapping[str, Any], request: EditRequest, *, lenient: bool = False
) -> tuple[dict[str, Any], list[str]]:
    """Resolve ``request`` against ``baseline`` into the result to review.

    Returns ``(result, dropped)``. A ``remove`` or ``before`` naming a rule
    the target list does not hold is :class:`UnknownRuleError`, unless
    ``lenient`` (a rebase onto a changed binding), where that part is dropped
    and its rule id listed in ``dropped``. Each rule in the result keeps the
    ``id`` it had in the baseline; added rules carry ``id: None``.
    """
    target, rules = target_rules(baseline)
    known = {r["id"] for r in rules}
    dropped: list[str] = []

    def check(rule_id: str) -> bool:
        if rule_id in known:
            return True
        if not lenient:
            raise UnknownRuleError(rule_id)
        dropped.append(rule_id)
        return False

    if request.set_rules is not None:
        resolved = [{"id": None, **rule_content(r)} for r in request.set_rules]
    else:
        removed = {rule_id for rule_id in request.remove if check(rule_id)}
        resolved = [r for r in rules if r["id"] not in removed]
        for add in request.add:
            new = {"id": None, **rule_content(add.rule)}
            if add.before is None or not check(add.before):
                resolved.append(new)
                continue
            if add.before in removed:
                # The anchor itself is being removed: the rule takes its place.
                index = next(
                    (i for i, r in enumerate(resolved) if _follows(rules, r, add.before)),
                    len(resolved),
                )
            else:
                index = next(i for i, r in enumerate(resolved) if r["id"] == add.before)
            resolved.insert(index, new)

    granted = baseline.get("granted_scopes")
    asks_scopes = bool(request.add_scopes or request.remove_scopes) or (
        request.set_scopes is not None
    )
    if asks_scopes and granted is None:
        # Only an OAuth grant has scopes the platform can change.
        raise ScopesNotSupportedError()
    if request.set_scopes is not None:
        scopes = set(request.set_scopes)
    else:
        # Removing a scope the grant doesn't hold is already in place.
        scopes = (set(granted or []) | set(request.add_scopes)) - set(request.remove_scopes)
    result = {
        "target": target,
        "rules": resolved,
        "scopes": sorted(scopes),
        "suspended": bool(baseline["suspended"]) and not request.resume,
    }
    return result, dropped


def _follows(rules: Sequence[Mapping[str, Any]], rule: Mapping[str, Any], anchor: str) -> bool:
    """Whether ``rule`` sat after ``anchor`` in the baseline order (new rules never do)."""
    ids = [r["id"] for r in rules]
    if rule.get("id") not in ids:
        return False
    return ids.index(rule["id"]) > ids.index(anchor)


def require_change(baseline: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    """Refuse a result that leaves the binding exactly as it is."""
    _, rules = target_rules(baseline)
    same_rules = [rule_content(r) for r in rules] == [rule_content(r) for r in result["rules"]]
    same_scopes = set(result["scopes"]) == set(baseline.get("granted_scopes") or [])
    if same_rules and same_scopes and result["suspended"] == bool(baseline["suspended"]):
        raise NothingToChangeError()


def widens_access(baseline: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    """Whether confirming ``result`` could let the agent do more than today.

    Conservative: adding an allow or Ask rule, dropping a deny rule,
    re-ordering the rules (first match wins), adding scopes and resuming a
    suspended binding all count.
    """
    _, rules = target_rules(baseline)
    before = [rule_content(r) for r in rules]
    after = [rule_content(r) for r in result["rules"]]
    if bool(baseline["suspended"]) and not result["suspended"]:
        return True
    if added_scopes(baseline, result):
        return True
    if any(r not in before and r["effect"] in _GRANTING_EFFECTS for r in after):
        return True
    if any(r not in after and r["effect"] == "deny" for r in before):
        return True
    kept_before = [r for r in before if r in after]
    kept_after = [r for r in after if r in before]
    return kept_before != kept_after


def added_scopes(baseline: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
    """Scopes ``result`` holds that the credential's grant does not."""
    return sorted(set(result["scopes"]) - set(baseline.get("granted_scopes") or []))


def removed_scopes(baseline: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
    """Scopes the credential's grant holds that ``result`` drops."""
    return sorted(set(baseline.get("granted_scopes") or []) - set(result["scopes"]))


def scopes_changed(baseline: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    """Whether ``result`` changes the grant's scopes, so confirming runs a vendor sign-in.

    Adding or removing alike: the sign-in asks for exactly the result's scopes.
    """
    return set(result["scopes"]) != set(baseline.get("granted_scopes") or [])


def baseline_matches(stored: Mapping[str, Any], live: Mapping[str, Any]) -> bool:
    """Whether the binding still holds what the session's baseline recorded.

    Compares the rules (ids and content), the attached rule set and its
    rules, and suspension. Granted scopes are left out: a token refresh can
    rewrite them without anyone changing the binding.
    """

    def key(snapshot: Mapping[str, Any]) -> Any:
        rule_set = snapshot.get("rule_set")
        return (
            [(r["id"], rule_content(r)) for r in snapshot["rules"]],
            (
                rule_set["id"],
                [(r["id"], rule_content(r)) for r in rule_set["rules"]],
            )
            if rule_set is not None
            else None,
            bool(snapshot["suspended"]),
        )

    return bool(key(stored) == key(live))
