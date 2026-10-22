"""Named rule sets kept in code that a profile preset can ``include``.

A preset refers to an existing rule list by name (``github_graphql:pr_write``)
instead of copying it, so the GraphQL presets in
:mod:`jentic_one.shared.permissions.github_graphql` stay the one definition of
which GitHub mutations a pull-request workflow sends.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from jentic_one.shared.permissions.github_graphql import GITHUB_GRAPHQL_PRESETS

#: The permission-rule fields a rule set entry carries over (``_comment`` and
#: any other annotation stay behind).
_RULE_FIELDS = ("effect", "methods", "path", "match_mode", "operations")


def _plain(rule: Mapping[str, Any]) -> dict[str, Any]:
    return {key: rule[key] for key in _RULE_FIELDS if rule.get(key) is not None}


#: Every includable rule set, by ``<set>:<name>``.
RULE_SETS: Mapping[str, tuple[dict[str, Any], ...]] = MappingProxyType(
    {
        f"github_graphql:{name}": tuple(_plain(rule) for rule in rules)
        for name, rules in GITHUB_GRAPHQL_PRESETS.items()
    }
)


def rule_set(ref: str) -> tuple[dict[str, Any], ...] | None:
    """The rules of rule set ``ref`` (fresh copies), or ``None`` for an unknown name."""
    rules = RULE_SETS.get(ref)
    return None if rules is None else tuple(dict(rule) for rule in rules)


__all__ = ["RULE_SETS", "rule_set"]
