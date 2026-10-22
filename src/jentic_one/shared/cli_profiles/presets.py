"""Expand a profile preset into concrete permission rules.

A preset is manifest data (:class:`~jentic_one.shared.cli_profiles.models.Preset`):
rules per API role, with ``${param}`` references, ``for_each`` repetition over
a repository list, ``when`` flags and includes of rule sets kept in code.
Expansion fills the parameters in and returns rules in the shape the
credentials API accepts (``effect``, ``methods``, ``path``, ``match_mode``,
``operations``), each checked with the same validators a hand-written rule
goes through, so an expanded preset can never store a rule the API would
refuse.

Each API of a profile has its own binding today, so expansion is per role:
``expand_preset(profile, name, params, role=ApiRole.GIT)`` returns the git
rules only, and asks only for the parameters those rules use.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jentic_one.shared.cli_profiles.models import (
    TEMPLATE_RE,
    ApiRole,
    CliProfile,
    Preset,
    PresetParam,
    PresetRule,
)
from jentic_one.shared.cli_profiles.rule_sets import rule_set
from jentic_one.shared.permissions.git_rules import GIT_MATCH_MODE
from jentic_one.shared.permissions.matching import validate_path

#: Cap on a ``repo_list`` parameter's entries (a binding takes at most 100 rules).
MAX_REPOS = 50

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


class PresetError(ValueError):
    """A preset that cannot be expanded with the given name or parameters."""


def _repos(name: str, raw: str) -> list[str]:
    repos = [item.strip() for item in raw.split(",") if item.strip()]
    if not repos:
        raise PresetError(f"parameter {name!r} needs at least one '<owner>/<repo>'")
    if len(repos) > MAX_REPOS:
        raise PresetError(f"parameter {name!r} lists more than {MAX_REPOS} repositories")
    for repo in repos:
        owner, slash, rest = repo.partition("/")
        if not slash or not owner or not rest or "/" in rest or ":" in repo:
            raise PresetError(
                f"parameter {name!r}: {repo!r} is not '<owner>/<repo>' (globs '*' and '?' allowed)"
            )
    return repos


def _ref_glob(name: str, raw: str) -> str:
    value = raw.strip()
    if (
        not value
        or value.startswith(("/", "refs/"))
        or any(ch.isspace() for ch in value)
        or ":" in value
        or ".." in value
    ):
        raise PresetError(
            f"parameter {name!r}: {raw!r} must be a branch glob below refs/heads/, e.g. 'agent/*'"
        )
    return value


def _flag(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise PresetError(f"parameter {name!r} must be true or false, got {raw!r}")


def _needed(preset: Preset, role: ApiRole | None) -> list[PresetRule]:
    return [rule for rule in preset.rules if role is None or rule.api == role]


def _resolve_params(
    preset_name: str,
    preset: Preset,
    rules: list[PresetRule],
    given: Mapping[str, str],
) -> dict[str, Any]:
    unknown = sorted(set(given) - set(preset.params))
    if unknown:
        known = ", ".join(sorted(preset.params)) or "none"
        raise PresetError(
            f"preset {preset_name!r} takes no parameter {unknown[0]!r} (parameters: {known})"
        )
    used: set[str] = set()
    for rule in rules:
        used |= rule.references()
    values: dict[str, Any] = {}
    for name in sorted(used):
        spec: PresetParam = preset.params[name]
        raw = given.get(name, spec.default)
        if raw is None:
            raise PresetError(
                f"preset {preset_name!r} needs parameter {name!r}: {spec.description}"
            )
        if spec.kind == "repo_list":
            values[name] = _repos(name, raw)
        elif spec.kind == "ref_glob":
            values[name] = _ref_glob(name, raw)
        else:
            values[name] = _flag(name, raw)
    return values


def _fill(template: str, values: Mapping[str, Any]) -> str:
    return TEMPLATE_RE.sub(lambda match: str(values[match.group(1)]), template)


def _check(rule: dict[str, Any], origin: str) -> dict[str, Any]:
    err = validate_path(rule.get("path"), rule.get("match_mode", "regex"))
    if err is not None:
        raise PresetError(f"{origin}: {err.reason}")
    if rule["effect"] in ("allow", "require-approval") and not (
        rule.get("methods") or rule.get("path") or rule.get("operations")
    ):
        raise PresetError(f"{origin}: an {rule['effect']} rule must constrain something")
    if rule.get("match_mode") == GIT_MATCH_MODE and rule["effect"] == "require-approval":
        raise PresetError(f"{origin}: a git rule allows or denies only")
    return rule


def _expand_rule(rule: PresetRule, values: Mapping[str, Any], origin: str) -> list[dict[str, Any]]:
    if rule.when is not None and not values[rule.when]:
        return []
    if rule.include is not None:
        included = rule_set(rule.include)
        if included is None:
            raise PresetError(f"{origin}: unknown rule set {rule.include!r}")
        return [_check(dict(item), origin) for item in included]
    assert rule.effect is not None and rule.match_mode is not None
    items: list[Mapping[str, Any]] = [values]
    if rule.for_each is not None:
        items = [{**values, rule.for_each: repo} for repo in values[rule.for_each]]
    out: list[dict[str, Any]] = []
    for item in items:
        concrete: dict[str, Any] = {"effect": rule.effect, "match_mode": rule.match_mode}
        if rule.methods is not None:
            concrete["methods"] = list(rule.methods)
        if rule.path is not None:
            concrete["path"] = _fill(rule.path, item)
        if rule.operations is not None:
            concrete["operations"] = list(rule.operations)
        out.append(_check(concrete, origin))
    return out


def expand_preset(
    profile: CliProfile,
    preset_name: str,
    params: Mapping[str, str] | None = None,
    *,
    role: ApiRole | None = None,
) -> list[dict[str, Any]]:
    """The concrete rules ``preset_name`` grants, for one API role or for all of them.

    Raises :class:`PresetError` for an unknown preset, an unknown or missing
    parameter, a parameter value of the wrong shape, or a preset with no rule
    for ``role``.
    """
    preset = profile.presets.get(preset_name)
    if preset is None:
        known = ", ".join(sorted(profile.presets)) or "none"
        raise PresetError(
            f"profile {profile.profile!r} has no preset {preset_name!r} (presets: {known})"
        )
    rules = _needed(preset, role)
    if not rules:
        raise PresetError(
            f"preset {preset_name!r} of profile {profile.profile!r} grants nothing on the "
            f"{role} API"
        )
    values = _resolve_params(preset_name, preset, rules, params or {})
    expanded: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        expanded.extend(
            _expand_rule(rule, values, f"preset {preset_name!r} rule {index} ({rule.api})")
        )
    if not expanded:
        raise PresetError(
            f"preset {preset_name!r} grants nothing on the {role} API with these parameters"
        )
    return expanded


def expand_preset_by_role(
    profile: CliProfile, preset_name: str, params: Mapping[str, str] | None = None
) -> dict[ApiRole, list[dict[str, Any]]]:
    """Every role's concrete rules for ``preset_name`` (all parameters it uses required)."""
    preset = profile.presets.get(preset_name)
    if preset is None:
        raise PresetError(f"profile {profile.profile!r} has no preset {preset_name!r}")
    roles = list(dict.fromkeys(rule.api for rule in preset.rules))
    return {r: expand_preset(profile, preset_name, params, role=r) for r in roles}


__all__ = ["MAX_REPOS", "PresetError", "expand_preset", "expand_preset_by_role"]
