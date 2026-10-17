"""Forbid SQLAlchemy's typed-tuple shims (``Result.tuples()``, ``Row._tuple()``, ``Row._t``).

SQLAlchemy 2.1 deprecates them because ``Row`` unpacks as a typed tuple directly.
Each call emits a deprecation warning whose stack walk is a per-call CPU cost,
measurable when the call sits on the broker's hot path (#1820). The pytest
``filterwarnings`` entry catches this at runtime only on a SQLAlchemy that
deprecates the shims; this static check holds on every version.

``Result.t`` (the shorter synonym) is not matched: a bare ``.t`` attribute is
too common to tell apart without type information.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_SHIM_METHODS = frozenset({"tuples", "_tuple"})
_SHIM_ATTRS = frozenset({"_t"})


def _check_file(filepath: Path) -> list[str]:
    """Return violations for any typed-tuple shim usage in *filepath*."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    violations: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SHIM_METHODS
            and not node.args
            and not node.keywords
        ):
            violations.append(f"{filepath}:{node.lineno} — '.{node.func.attr}()'")
        elif isinstance(node, ast.Attribute) and node.attr in _SHIM_ATTRS:
            violations.append(f"{filepath}:{node.lineno} — '.{node.attr}'")
    return violations


@pytest.mark.arch
def test_no_sqlalchemy_tuple_shims() -> None:
    """Source code iterates/unpacks ``Row`` directly instead of the typed-tuple shims."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        violations.extend(_check_file(py_file))
    assert not violations, (
        "Unpack Row directly (use typing.cast for column types) instead of the "
        "SQLAlchemy 2.1-deprecated typed-tuple shims:\n" + "\n".join(violations)
    )
