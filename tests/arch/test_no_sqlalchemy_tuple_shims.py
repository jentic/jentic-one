"""Forbid SQLAlchemy's typed-tuple shims (``Result.tuples()``/``.t``, ``Row._tuple()``/``._t``).

SQLAlchemy 2.1 deprecates them because ``Row`` unpacks as a typed tuple directly.
Each call emits a deprecation warning whose stack walk is a per-call CPU cost,
measurable when the call sits on the broker's hot path (#1820). The pytest
``filterwarnings`` entry catches this at runtime only on a SQLAlchemy that
deprecates the shims; this static check holds on every version.

Without type information the check scopes itself to receivers that are
SQLAlchemy results or rows by construction, and matches attribute *references*
(``Load`` context), so a bare reference such as ``map(Row._tuple, rows)`` counts
the same as a call. All four shims, including the short ``.t``, are flagged in
any module on:

- a name bound by an ``import``/``from`` of ``sqlalchemy`` (``Result.tuples(r)``,
  ``sa.engine.Row._t``);
- a result-returning call: ``x.execute(...)`` (awaited or not), an *awaited*
  ``x.stream(...)`` (``AsyncSession.stream`` is a coroutine, while httpx and
  runner ``stream()`` calls are context managers), and ``unique()``/
  ``columns()``/``yield_per()`` chained on one of those;
- a name assigned (``=`` / annotated ``=``) from such a call in the same
  function, so ``r = await db.execute(q); r.tuples()`` counts even in a module
  that never imports SQLAlchemy.

In a module that imports SQLAlchemy, ``tuples``, ``_tuple`` and ``_t`` (and
``getattr(x, "tuples")``) are also flagged on any other receiver built from a
plain name by attribute, subscript or call (``row._t``, ``rows[0]._t``,
``get_result().tuples()``), except:

- a chain rooted at ``self``/``cls`` (``self._t``, ``self.timer._t``), which is
  the class's own state;
- a call to a capitalised name (``Timer().tuples()``), which is a constructor;
- an assignment target (``row._t = ...``), which is not a use.

Known limits, accepted to keep the check a single cheap AST pass: results
reached through ``self``/``cls`` (``self.result.tuples()``), tuple-unpacking or
walrus targets, values passed across functions, rows taken from an assigned
result outside a SQLAlchemy-importing module (``r.all()[0]._t``), and ``.t`` on
a receiver that is not one of the result forms above are not followed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_SHIMS = frozenset({"tuples", "_tuple", "_t"})
_SHORT_SHIM = "t"
_OWN_RECEIVERS = frozenset({"self", "cls"})
#: Methods on a ``Result`` that return another ``Result``.
_RESULT_CHAIN_METHODS = frozenset({"unique", "columns", "yield_per"})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


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


def _scope_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes of *scope*, without descending into nested function/class scopes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _receiver_root(expr: ast.expr) -> str | None:
    """The leading name under any attribute/subscript/call chain, or ``None``."""
    while isinstance(expr, ast.Attribute | ast.Subscript | ast.Call):
        expr = expr.func if isinstance(expr, ast.Call) else expr.value
    return expr.id if isinstance(expr, ast.Name) else None


def _is_result_call(expr: ast.expr, result_names: set[str]) -> bool:
    """Whether ``expr`` evaluates to a SQLAlchemy ``Result`` by construction."""
    awaited = False
    if isinstance(expr, ast.Await):
        awaited, expr = True, expr.value
    if isinstance(expr, ast.Name):
        return expr.id in result_names
    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)):
        return False
    method = expr.func.attr
    if method == "execute" or (method == "stream" and awaited):
        return True
    return method in _RESULT_CHAIN_METHODS and _is_result_call(expr.func.value, result_names)


def _is_sqlalchemy_receiver(expr: ast.expr, sa_names: set[str], result_names: set[str]) -> bool:
    """Whether ``expr`` is a SQLAlchemy class/module or a ``Result``."""
    if _is_result_call(expr, result_names):
        return True
    while isinstance(expr, ast.Attribute):
        expr = expr.value
    return isinstance(expr, ast.Name) and expr.id in sa_names


def _is_plain_receiver(expr: ast.expr) -> bool:
    """A name-rooted receiver that is neither ``self``/``cls`` state nor a constructor call."""
    root = _receiver_root(expr)
    if root is None or root in _OWN_RECEIVERS:
        return False
    if isinstance(expr, ast.Call):
        func = expr.func
        callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return not callee[:1].isupper()
    return True


def _result_names(scope: ast.AST) -> set[str]:
    """Names in *scope* assigned from a ``Result``-returning call."""
    names: set[str] = set()
    for node in _scope_nodes(scope):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if _is_result_call(value, set()):
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _check_file(filepath: Path) -> list[str]:
    """Return violations for any typed-tuple shim usage in *filepath*."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    sa_names = _sqlalchemy_names(tree)
    imports_sqlalchemy = bool(sa_names)
    violations: list[str] = []
    scopes = [tree, *(n for n in ast.walk(tree) if isinstance(n, _SCOPES))]
    for scope in scopes:
        result_names = _result_names(scope)
        for node in _scope_nodes(scope):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                if node.attr not in _SHIMS and node.attr != _SHORT_SHIM:
                    continue
                if _is_sqlalchemy_receiver(node.value, sa_names, result_names) or (
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
                violations.append(
                    f"{filepath}:{node.lineno} — getattr(..., {node.args[1].value!r})"
                )
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
        pytest.param(
            "async def f(db, q):\n    r = await db.execute(q)\n    return r.tuples()\n",
            id="assigned-execute-tuples-no-sqlalchemy-import",
        ),
        pytest.param(
            "async def f(db, q):\n    r: object = await db.execute(q)\n    return r.t.all()\n",
            id="annotated-assigned-execute-short-t-no-sqlalchemy-import",
        ),
        pytest.param(
            "async def f(s, q):\n    return (await s.stream(q)).t\n", id="awaited-stream-short-t"
        ),
        pytest.param(
            "def f(s, q):\n    return s.execute(q).unique().tuples()\n",
            id="chained-unique-tuples",
        ),
        pytest.param(_SA_IMPORT + "first = rows[0]._t\n", id="subscript-row-t"),
        pytest.param(_SA_IMPORT + "rows = results[0].tuples()\n", id="subscript-tuples"),
        pytest.param(_SA_IMPORT + "rows = get_result().tuples()\n", id="call-receiver-tuples"),
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
        pytest.param(
            _SA_IMPORT + "class T:\n    def read(self):\n        return self.timer._t\n",
            id="self-chain-t-read",
        ),
        pytest.param(
            _SA_IMPORT + "class T:\n    def read(self):\n        return self.rows[0]._t\n",
            id="self-subscript-t-read",
        ),
        pytest.param(
            _SA_IMPORT + "async def f(client, url):\n"
            "    async with client.stream('GET', url) as r:\n"
            "        return r.t\n",
            id="httpx-stream-short-t",
        ),
        pytest.param(
            _SA_IMPORT + "def f(client, url):\n    return client.stream('GET', url).t\n",
            id="unawaited-stream-short-t",
        ),
        pytest.param(
            "async def f(db, q):\n    r = await db.execute(q)\n\n"
            "def g():\n    r = timer()\n    return r._t\n",
            id="result-name-does-not-leak-across-functions",
        ),
        pytest.param("rows = get_result().tuples()\n", id="call-receiver-no-sqlalchemy-import"),
    ],
)
def test_check_file_ignores_unrelated_attributes(tmp_path: Path, source: str) -> None:
    """Same-named attributes that cannot be a SQLAlchemy result/row are not violations."""
    path = tmp_path / "mod.py"
    path.write_text(source, encoding="utf-8")
    assert _check_file(path) == []
