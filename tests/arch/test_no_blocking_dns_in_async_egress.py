"""Async egress code must not resolve DNS on the event loop (#1700, #1734).

``validate_upstream_url`` and ``resolve_and_validate`` call the blocking
``socket.getaddrinfo``; inside an ``async def`` a slow resolver stalls every
in-flight request. Async code anywhere under ``src/jentic_one`` uses the async
twins (``validate_upstream_url_async`` / ``resolve_and_validate_async``)
instead.

A module-local sync helper that wraps a blocking resolver counts as blocking
too, so routing the call through ``def _validate(...)`` from an ``async def``
is flagged the same as a direct call. Helpers are matched by bare name, so
same-named sync defs in one module (``_validate`` on two classes) are merged:
if any of them reaches a resolver, a call to that name is flagged. Genuinely
sync callers (never reached from an ``async def`` in the same module) stay
allowed.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_BLOCKING_RESOLVERS = frozenset({"validate_upstream_url", "resolve_and_validate"})


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
    sync_calls: defaultdict[str, set[str]] = defaultdict(set)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            sync_calls[fn.name] |= _calls_in(fn)
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
    """Every call to a blocking name lexically inside an ``async def`` in *path*.

    A blocking resolver passed by reference (``asyncio.to_thread(validate_upstream_url, u)``)
    is not a call and so is allowed.
    """
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
    for py_file in python_files_in(SRC_ROOT):
        violations.extend(_violations(py_file))
    assert not violations, "Blocking DNS resolution on the event loop:\n" + "\n".join(violations)


_FLAGGED_SOURCES = {
    "direct": """
async def run(u):
    return validate_upstream_url(u)
""",
    "one_hop_helper": """
def _check(u):
    return validate_upstream_url(u)

async def run(u):
    return _check(u)
""",
    "two_hop_helper": """
def _inner(u):
    return resolve_and_validate(u)

def _outer(u):
    return _inner(u)

async def run(u):
    return _outer(u)
""",
    "method_helper": """
class Service:
    def _validate(self, u):
        return validate_upstream_url(u)

    async def run(self, u):
        return self._validate(u)
""",
    "same_name_defs_blocking_first": """
class A:
    def _validate(self, u):
        return validate_upstream_url(u)

class B:
    def _validate(self, u):
        return u

    async def run(self, u):
        return self._validate(u)
""",
    "same_name_defs_blocking_last": """
class A:
    def _validate(self, u):
        return u

class B:
    def _validate(self, u):
        return validate_upstream_url(u)

    async def run(self, u):
        return self._validate(u)
""",
}

_ALLOWED_SOURCES = {
    "sync_only_caller": """
def _check(u):
    return validate_upstream_url(u)

def run(u):
    return _check(u)
""",
    "to_thread_by_reference": """
import asyncio

async def run(u):
    return await asyncio.to_thread(validate_upstream_url, u)
""",
    "async_twin": """
async def run(u):
    return await validate_upstream_url_async(u)
""",
}


@pytest.mark.arch
@pytest.mark.parametrize("source", _FLAGGED_SOURCES.values(), ids=_FLAGGED_SOURCES.keys())
def test_violations_flags_blocking_calls_reached_from_async(tmp_path: Path, source: str) -> None:
    path = tmp_path / "mod.py"
    path.write_text(source, encoding="utf-8")
    assert len(_violations(path)) == 1


@pytest.mark.arch
@pytest.mark.parametrize("source", _ALLOWED_SOURCES.values(), ids=_ALLOWED_SOURCES.keys())
def test_violations_allows_code_that_keeps_dns_off_the_loop(tmp_path: Path, source: str) -> None:
    path = tmp_path / "mod.py"
    path.write_text(source, encoding="utf-8")
    assert _violations(path) == []
