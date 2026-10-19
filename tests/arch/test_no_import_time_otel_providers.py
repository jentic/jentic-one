"""No test module installs a global OTel provider at import time.

Module-level OTel proxies (``trace.get_tracer(...)``, ``get_meter(...)``
instruments) only follow the per-test providers that ``otel_span_exporter`` /
``otel_metric_reader`` install if they were created while no concrete provider
was set. A ``set_meter_provider`` / ``set_tracer_provider`` call that runs when
a test module is imported binds every proxy created afterwards to that provider
for good, and pins it ahead of every fixture, which makes OTel assertions
depend on test order (#1732). ``configure_tracing`` / ``configure_metrics``
install a provider through those setters, so they count as setters too.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent.parent
_PROVIDER_SETTERS = frozenset(
    {"set_meter_provider", "set_tracer_provider", "configure_metrics", "configure_tracing"}
)


def _import_time_calls(node: ast.AST) -> list[ast.Call]:
    """Calls under ``node`` that run on import (not inside a function or lambda body)."""
    calls: list[ast.Call] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            # Decorators and default values (positional and keyword-only) still
            # run on import; ``kw_defaults`` holds ``None`` for keyword-only
            # parameters without a default.
            import_time_exprs = [
                *getattr(child, "decorator_list", []),
                *child.args.defaults,
                *(d for d in child.args.kw_defaults if d is not None),
            ]
            for expr in import_time_exprs:
                if isinstance(expr, ast.Call):
                    calls.append(expr)
                calls.extend(_import_time_calls(expr))
            continue
        if isinstance(child, ast.Call):
            calls.append(child)
        calls.extend(_import_time_calls(child))
    return calls


def _setter_aliases(tree: ast.AST) -> dict[str, str]:
    """Local names bound to a provider setter, mapped to the setter's real name.

    Covers ``from ... import set_tracer_provider`` and its ``as`` aliases, so a
    bare-name call is flagged whatever it is called locally.
    """
    aliases = {name: name for name in _PROVIDER_SETTERS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _PROVIDER_SETTERS:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


def _setter_name(call: ast.Call, aliases: dict[str, str]) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr if func.attr in _PROVIDER_SETTERS else None
    if isinstance(func, ast.Name):
        return aliases.get(func.id)
    return None


def _import_time_setters(tree: ast.AST) -> list[tuple[ast.Call, str]]:
    aliases = _setter_aliases(tree)
    return [
        (call, name)
        for call in _import_time_calls(tree)
        if (name := _setter_name(call, aliases)) is not None
    ]


def _check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        f"{path.relative_to(TESTS_ROOT.parent)}:{call.lineno} calls {name}() at import time"
        for call, name in _import_time_setters(tree)
    ]


_HEADER = (
    "from opentelemetry import metrics, trace\n"
    "from jentic_one.shared.metrics import configure_metrics\n"
    "from jentic_one.shared.tracing import configure_tracing\n"
)


@pytest.mark.arch
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "trace.set_tracer_provider(object())\n",
            ["set_tracer_provider"],
            id="module-level",
        ),
        pytest.param(
            "class Holder:\n    metrics.set_meter_provider(object())\n",
            ["set_meter_provider"],
            id="class-body",
        ),
        pytest.param(
            "@deco(trace.set_tracer_provider(object()))\ndef f():\n    pass\n",
            ["set_tracer_provider"],
            id="decorator-argument",
        ),
        pytest.param(
            "@trace.set_tracer_provider(object())\ndef f():\n    pass\n",
            ["set_tracer_provider"],
            id="decorator-call",
        ),
        pytest.param(
            "def f(p=trace.set_tracer_provider(object())):\n    pass\n",
            ["set_tracer_provider"],
            id="positional-default",
        ),
        pytest.param(
            "def f(*, a, p=metrics.set_meter_provider(object())):\n    pass\n",
            ["set_meter_provider"],
            id="keyword-only-default",
        ),
        pytest.param(
            "async def f(*, p=trace.set_tracer_provider(object())):\n    pass\n",
            ["set_tracer_provider"],
            id="async-keyword-only-default",
        ),
        pytest.param(
            "configure_tracing('svc')\n",
            ["configure_tracing"],
            id="module-level-configure-tracing",
        ),
        pytest.param(
            "PROVIDER = configure_metrics('svc', object())\n",
            ["configure_metrics"],
            id="module-level-configure-metrics",
        ),
        pytest.param(
            "from opentelemetry.trace import set_tracer_provider as stp\nstp(object())\n",
            ["set_tracer_provider"],
            id="aliased-setter-import",
        ),
        pytest.param(
            "from jentic_one.shared.tracing import configure_tracing as ct\nct('svc')\n",
            ["configure_tracing"],
            id="aliased-configure-import",
        ),
        pytest.param(
            "import opentelemetry.trace as t\nt.set_tracer_provider(object())\n",
            ["set_tracer_provider"],
            id="aliased-module-import",
        ),
        pytest.param(
            "def fixture():\n"
            "    metrics.set_meter_provider(object())\n"
            "    configure_tracing('svc')\n"
            "def g(*, p=None):\n"
            "    trace.set_tracer_provider(object())\n"
            "h = lambda: configure_metrics('svc', object())\n",
            [],
            id="function-and-lambda-bodies-ignored",
        ),
    ],
)
def test_check_flags_import_time_provider_setters(source: str, expected: list[str]) -> None:
    """The scan flags every import-time provider install and ignores deferred ones."""
    found = [name for _, name in _import_time_setters(ast.parse(_HEADER + source))]
    assert found == expected


@pytest.mark.arch
def test_no_import_time_otel_providers() -> None:
    """No file under ``tests/`` installs a global OTel provider when imported."""
    violations = [v for path in sorted(TESTS_ROOT.rglob("*.py")) for v in _check_file(path)]
    assert not violations, (
        "Install OTel providers in fixtures (otel_metric_reader / otel_span_exporter), "
        "never at import time:\n" + "\n".join(violations)
    )
