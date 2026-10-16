"""Async egress code must not resolve DNS on the event loop (#1700, #1734).

``validate_upstream_url`` and ``resolve_and_validate`` call the blocking
``socket.getaddrinfo``; inside an ``async def`` a slow resolver stalls every
in-flight request. The broker, the execution-job worker, the registry's
spec / catalog fetchers, and the control plane's OAuth / integration egress use
the async twins (``validate_upstream_url_async`` / ``resolve_and_validate_async``)
instead.

A module-local sync helper that wraps a blocking resolver counts as blocking
too, so routing the call through ``def _validate(...)`` from an ``async def``
is flagged the same as a direct call. Genuinely sync callers (never reached
from an ``async def`` in the same module) stay allowed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_BLOCKING_RESOLVERS = frozenset({"validate_upstream_url", "resolve_and_validate"})

_SCANNED_ROOTS = (
    SRC_ROOT / "broker",
    SRC_ROOT / "shared" / "jobs",
    SRC_ROOT / "registry",
    SRC_ROOT / "control",
    SRC_ROOT / "auth",
    SRC_ROOT / "admin",
)


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _calls_in(fn: ast.AST) -> set[str]:
    return {
        name
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and (name := _called_name(node)) is not None
    }


def _blocking_names(tree: ast.Module) -> frozenset[str]:
    """The blocking resolvers plus every sync function in *tree* that reaches one."""
    sync_calls = {
        fn.name: _calls_in(fn) for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
    }
    blocking = set(_BLOCKING_RESOLVERS)
    changed = True
    while changed:
        changed = False
        for name, calls in sync_calls.items():
            if name not in blocking and calls & blocking:
                blocking.add(name)
                changed = True
    return frozenset(blocking)


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    blocking = _blocking_names(tree)
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and _called_name(node) in blocking:
                found.append(
                    f"{path}:{node.lineno} — blocking {_called_name(node)}() inside async "
                    f"{fn.name}() (use the *_async variant so getaddrinfo runs off the loop)"
                )
    return found


@pytest.mark.arch
def test_no_blocking_dns_resolution_in_async_egress_code() -> None:
    violations: list[str] = []
    for root in _SCANNED_ROOTS:
        for py_file in python_files_in(root):
            violations.extend(_violations(py_file))
    assert not violations, "Blocking DNS resolution on the event loop:\n" + "\n".join(violations)
