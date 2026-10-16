"""Async broker egress code must not resolve DNS on the event loop (#1700).

``validate_upstream_url`` and ``resolve_and_validate`` call the blocking
``socket.getaddrinfo``; inside an ``async def`` a slow resolver stalls every
in-flight request. The broker and the execution-job worker use the async twins
(``validate_upstream_url_async`` / ``resolve_and_validate_async``) instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_BLOCKING_RESOLVERS = frozenset({"validate_upstream_url", "resolve_and_validate"})

_SCANNED_ROOTS = (SRC_ROOT / "broker", SRC_ROOT / "shared" / "jobs")


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and _called_name(node) in _BLOCKING_RESOLVERS:
                found.append(
                    f"{path}:{node.lineno} — blocking {_called_name(node)}() inside async "
                    f"{fn.name}() (use the *_async variant so getaddrinfo runs off the loop)"
                )
    return found


@pytest.mark.arch
def test_no_blocking_dns_resolution_in_async_broker_code() -> None:
    violations: list[str] = []
    for root in _SCANNED_ROOTS:
        for py_file in python_files_in(root):
            violations.extend(_violations(py_file))
    assert not violations, "Blocking DNS resolution on the event loop:\n" + "\n".join(violations)
