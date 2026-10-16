"""Every path that runs repeated-failure detection threads the shared counter.

The counter in front of the repeated-failure scan is an upper bound on the
scan's count only if every caller that persists a failed execution and runs the
detection increments the *same* counter (see ``RepeatedFailureCounter``). A
call site that drops the counter adds records the scan counts but the buckets
don't, so gated callers could skip a due scan. These checks pin that each such
call passes the counter explicitly, so reshaping a call site cannot silently
drop it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

# Calls that always run detection: the counter kwarg is mandatory.
_ALWAYS = {
    "maybe_emit_repeated_failure": "failure_counter",
    "_persist_streaming_outcome": "repeated_failure_counter",
    "ExecutionHandler": "repeated_failure_counter",
}
# Calls that run detection only when handed a ``security_config``.
_WHEN_DETECTING = {
    "run_execution": "repeated_failure_counter",
    "execute_upstream": "repeated_failure_counter",
    "persist_streaming_execution": "repeated_failure_counter",
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _violations(path: Path, seen: set[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        kwargs = {kw.arg for kw in node.keywords}
        if None in kwargs:  # ``**kwargs`` forwarding can't be checked statically
            continue
        required = _ALWAYS.get(name or "")
        if required is None and "security_config" in kwargs:
            required = _WHEN_DETECTING.get(name or "")
        if required is None:
            continue
        seen.add(name or "")
        if required not in kwargs:
            found.append(
                f"{path.relative_to(SRC_ROOT)}:{node.lineno} — {name}() runs "
                f"repeated-failure detection without {required}= (pass the broker's "
                "shared RepeatedFailureCounter so the scan gate's bound holds)"
            )
    return found


@pytest.mark.arch
def test_repeated_failure_detection_call_sites_pass_the_counter() -> None:
    seen: set[str] = set()
    violations = [v for path in python_files_in(SRC_ROOT) for v in _violations(path, seen)]
    assert not violations, "\n".join(violations)
    # Guard against a rename turning this check vacuous.
    assert {"maybe_emit_repeated_failure", "ExecutionHandler", "run_execution"} <= seen, seen
