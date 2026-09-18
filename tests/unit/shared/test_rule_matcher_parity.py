"""Parity test — pins that the Python and TS rule matchers agree.

The connect-flow UI evaluates rules client-side (see
``ui/src/shared/credentials/lib/rule-matcher.ts``) so the operation-impact
preview doesn't require a network call per operation. The two matchers
share a JSON fixture (``tests/fixtures/rule-matcher-parity.json``); this
test runs the fixture through the Python side. The TS test at
``ui/src/shared/credentials/lib/__tests__/rule-matcher.test.ts`` runs
the same fixture through the TS side. Any divergence fails CI on both
sides.

Do NOT tweak this file to make a red test go green — fix the divergence
at the source (either the shared ``matching.py`` / ``rule_evaluator.py``
or the TS ``rule-matcher.ts``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jentic_one.broker.repos.rule_evaluator import PermissionRule as CompiledRule
from jentic_one.broker.repos.rule_evaluator import (
    _normalize_methods,
    evaluate_rules,
)
from jentic_one.shared.permissions.matching import compile_matcher

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "rule-matcher-parity.json"
# The TS test reads a vendored copy inside ui/ — the UI Docker build copies
# only ui/, so it cannot import across the module boundary. Byte-identity is
# enforced below so the two copies can't drift.
_UI_VENDORED_FIXTURE = (
    _REPO_ROOT
    / "ui"
    / "src"
    / "shared"
    / "credentials"
    / "lib"
    / "__tests__"
    / "rule-matcher-parity.json"
)


def _load_cases() -> list[dict[str, Any]]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return list(data["cases"])


def test_ui_vendored_fixture_matches_source() -> None:
    assert _UI_VENDORED_FIXTURE.read_bytes() == _FIXTURE.read_bytes(), (
        "The vendored TS parity fixture drifted from tests/fixtures/"
        "rule-matcher-parity.json. Edit the source fixture and copy it over "
        "ui/src/shared/credentials/lib/__tests__/rule-matcher-parity.json."
    )


def _compile(rule: dict[str, Any]) -> CompiledRule:
    return CompiledRule(
        effect=rule["effect"],
        methods=_normalize_methods(rule.get("methods")),
        path=compile_matcher(rule.get("path"), rule.get("match_mode", "regex")),
        operations=tuple(rule["operations"]) if rule.get("operations") else None,
    )


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_python_matcher_matches_shared_fixture(case: dict[str, Any]) -> None:
    rules = [_compile(r) for r in case["rules"]]
    req = case["request"]
    actual = evaluate_rules(
        rules,
        method=req["method"],
        path=req["path"],
        operation_id=req["operation_id"],
    )
    assert actual is case["allowed"], (
        f"parity divergence on case {case['name']!r}: python said {actual}, "
        f"fixture expects {case['allowed']}. If the intent changed, update the "
        f"fixture AND the TS test's expected snapshot."
    )
