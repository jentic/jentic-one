"""No test module installs a global OTel provider at import time.

Module-level OTel proxies (``trace.get_tracer(...)``, ``get_meter(...)``
instruments) only follow the per-test providers that ``otel_span_exporter`` /
``otel_metric_reader`` install if they were created while no concrete provider
was set. A ``set_meter_provider`` / ``set_tracer_provider`` call that runs when
a test module is imported binds every proxy created afterwards to that provider
for good, and pins it ahead of every fixture, which makes OTel assertions
depend on test order (#1732).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent.parent
_PROVIDER_SETTERS = frozenset({"set_meter_provider", "set_tracer_provider"})


def _import_time_calls(node: ast.AST) -> list[ast.Call]:
    """Calls under ``node`` that run on import (not inside a function or lambda body)."""
    calls: list[ast.Call] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            # Decorators and default values still run on import.
            calls.extend(
                call
                for expr in [*getattr(child, "decorator_list", []), *child.args.defaults]
                for call in _import_time_calls(expr)
            )
            continue
        if isinstance(child, ast.Call):
            calls.append(child)
        calls.extend(_import_time_calls(child))
    return calls


def _setter_name(call: ast.Call) -> str | None:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name if name in _PROVIDER_SETTERS else None


def _check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        f"{path.relative_to(TESTS_ROOT.parent)}:{call.lineno} calls {name}() at import time"
        for call in _import_time_calls(tree)
        if (name := _setter_name(call)) is not None
    ]


@pytest.mark.arch
def test_check_flags_import_time_provider_setters() -> None:
    """The scan catches a module-level setter and ignores one inside a fixture body."""
    source = (
        "from opentelemetry import metrics, trace\n"
        "trace.set_tracer_provider(object())\n"
        "def fixture():\n"
        "    metrics.set_meter_provider(object())\n"
    )
    calls = [_setter_name(c) for c in _import_time_calls(ast.parse(source))]
    assert [c for c in calls if c] == ["set_tracer_provider"]


@pytest.mark.arch
def test_no_import_time_otel_providers() -> None:
    """No file under ``tests/`` installs a global OTel provider when imported."""
    violations = [v for path in sorted(TESTS_ROOT.rglob("*.py")) for v in _check_file(path)]
    assert not violations, (
        "Install OTel providers in fixtures (otel_metric_reader / otel_span_exporter), "
        "never at import time:\n" + "\n".join(violations)
    )
