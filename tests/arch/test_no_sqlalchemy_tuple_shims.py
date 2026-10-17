"""Forbid SQLAlchemy's typed-tuple shims (``Result.tuples()``/``.t``, ``Row._tuple()``/``._t``).

SQLAlchemy 2.1 deprecates them because ``Row`` unpacks as a typed tuple directly.
Each call emits a deprecation warning whose stack walk is a per-call CPU cost,
measurable when the call sits on the broker's hot path (#1820). The pytest
``filterwarnings`` entry catches this at runtime only on a SQLAlchemy that
deprecates the shims; this static check holds on every version.

Without type information the check scopes itself to receivers that are
SQLAlchemy results or rows by construction, and matches attribute *references*
(``Load`` context), so a bare reference such as ``map(Row._tuple, rows)`` counts
the same as a call:

- a name bound by an ``import``/``from`` of ``sqlalchemy`` (``Result.tuples(r)``,
  ``sa.engine.Row._t``) or a result-returning call such as ``session.execute(...)``
  (awaited or not): all four shims, including the short ``.t``;
- any other ``name.attr`` receiver in a module that imports SQLAlchemy:
  ``tuples``, ``_tuple`` and ``_t`` (and ``getattr(x, "tuples")``). ``.t`` is
  too generic a name to match there, ``self``/``cls`` receivers are the class's
  own attributes, and an assignment target (``self._t = ...``) is not a use.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_SHIMS = frozenset({"tuples", "_tuple", "_t"})
_SHORT_SHIM = "t"
_OWN_RECEIVERS = frozenset({"self", "cls"})
#: Methods whose return value is a SQLAlchemy ``Result`` (or a ``Row`` source).
_RESULT_METHODS = frozenset({"execute", "stream", "unique", "columns", "yield_per"})


def _sqlalchemy_names(tree: ast.Module) -> set[str]:
    """Names the module binds from a ``sqlalchemy`` import."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlalchemy" or alias.name.startswith("sqlalchemy."):
                    names.add(alias.asname or alias.name.split(".")[0])
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and (node.module == "sqlalchemy" or node.module.startswith("sqlalchemy."))
        ):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _root_name(expr: ast.expr) -> str | None:
    """The leading name of a dotted ``a.b.c`` chain, or ``None`` for anything else."""
    while isinstance(expr, ast.Attribute):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


def _is_sqlalchemy_receiver(expr: ast.expr, sa_names: set[str]) -> bool:
    """Whether ``expr`` is a SQLAlchemy class/module or a ``Result``-returning call."""
    if isinstance(expr, ast.Await):
        expr = expr.value
    if isinstance(expr, ast.Call):
        return isinstance(expr.func, ast.Attribute) and expr.func.attr in _RESULT_METHODS
    return _root_name(expr) in sa_names


def _is_plain_receiver(expr: ast.expr) -> bool:
    """A ``name`` / ``name.attr`` receiver that is not the class's own ``self``/``cls``."""
    if isinstance(expr, ast.Name):
        return expr.id not in _OWN_RECEIVERS
    return isinstance(expr, ast.Attribute) and _root_name(expr) is not None


def _check_file(filepath: Path) -> list[str]:
    """Return violations for any typed-tuple shim usage in *filepath*."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    sa_names = _sqlalchemy_names(tree)
    imports_sqlalchemy = bool(sa_names)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            if node.attr not in _SHIMS and node.attr != _SHORT_SHIM:
                continue
            if _is_sqlalchemy_receiver(node.value, sa_names) or (
                node.attr in _SHIMS and imports_sqlalchemy and _is_plain_receiver(node.value)
            ):
                violations.append(f"{filepath}:{node.lineno} — '.{node.attr}'")
        elif (
            imports_sqlalchemy
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _SHIMS
        ):
            violations.append(f"{filepath}:{node.lineno} — getattr(..., {node.args[1].value!r})")
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


_SA_IMPORT = "from sqlalchemy.engine import Result, Row\n"


@pytest.mark.arch
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(_SA_IMPORT + "rows = result.tuples().all()\n", id="instance-tuples-call"),
        pytest.param(_SA_IMPORT + "rows = Result.tuples(result)\n", id="unbound-tuples-call"),
        pytest.param(_SA_IMPORT + "rows = list(map(Row._tuple, rows))\n", id="bare-row-tuple"),
        pytest.param(_SA_IMPORT + "first = row._t\n", id="instance-row-t"),
        pytest.param(_SA_IMPORT + "fn = getattr(result, 'tuples')\n", id="getattr-tuples"),
        pytest.param(
            "import sqlalchemy as sa\nfn = sa.engine.Result.tuples\n", id="module-alias-chain"
        ),
        pytest.param(
            "async def f(s, q):\n    return (await s.execute(q)).t.all()\n",
            id="awaited-execute-short-t",
        ),
        pytest.param("def f(s, q):\n    return s.execute(q).tuples()\n", id="sync-execute-tuples"),
    ],
)
def test_check_file_flags_shim_usage(tmp_path: Path, source: str) -> None:
    """Calls and bare references to a shim on a result/row receiver are violations."""
    path = tmp_path / "mod.py"
    path.write_text(source, encoding="utf-8")
    assert len(_check_file(path)) == 1


@pytest.mark.arch
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            _SA_IMPORT + "class T:\n    def start(self):\n        self._t = 1\n",
            id="self-t-assignment",
        ),
        pytest.param(
            _SA_IMPORT + "class T:\n    def read(self):\n        return self._t\n",
            id="self-t-read",
        ),
        pytest.param(
            _SA_IMPORT + "class T:\n    @classmethod\n    def read(cls):\n        return cls._t\n",
            id="cls-t-read",
        ),
        pytest.param(_SA_IMPORT + "row._t = 1\n", id="t-store"),
        pytest.param(_SA_IMPORT + "pairs = Timer().tuples()\n", id="domain-call-receiver"),
        pytest.param(_SA_IMPORT + "x = point.t\n", id="short-t-on-plain-name"),
        pytest.param(_SA_IMPORT + "x = value.tuple()\n", id="other-method"),
        pytest.param("pairs = timer.tuples()\nfirst = timer._t\n", id="no-sqlalchemy-import"),
        pytest.param("fn = getattr(timer, 'tuples')\n", id="getattr-no-sqlalchemy-import"),
    ],
)
def test_check_file_ignores_unrelated_attributes(tmp_path: Path, source: str) -> None:
    """Same-named attributes that cannot be a SQLAlchemy result/row are not violations."""
    path = tmp_path / "mod.py"
    path.write_text(source, encoding="utf-8")
    assert _check_file(path) == []
