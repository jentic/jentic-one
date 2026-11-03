"""Enforce the error-module conventions (#1962).

- Error modules are named ``errors.py`` — never ``exc.py`` or ``exceptions.py``.
- The broker's error taxonomy module (``broker/core/errors.py``) holds only
  exception classes; the agent-recovery directives live in
  ``broker/core/directives.py``.
- Each operation-discovery error is defined once, in
  ``shared/discovery/errors.py``, so the registry and the broker raise and
  render the same types.
"""

from __future__ import annotations

import ast
import importlib
import inspect

from .conftest import SRC_ROOT, python_files_in

_BANNED_ERROR_MODULE_NAMES = {"exc.py", "exceptions.py"}

_DISCOVERY_ERRORS = {
    "DiscoveryError",
    "OperationNotFoundError",
    "MethodNotAllowedError",
    "AmbiguousMatchError",
    "TooManyCandidatesError",
}
_DISCOVERY_MODULE = SRC_ROOT / "shared" / "discovery" / "errors.py"


def test_error_modules_are_named_errors_py() -> None:
    offenders = sorted(
        str(path.relative_to(SRC_ROOT))
        for path in python_files_in(SRC_ROOT)
        if path.name in _BANNED_ERROR_MODULE_NAMES
    )
    assert offenders == [], f"Name error modules errors.py: {offenders}"


def test_broker_core_errors_defines_only_exception_classes() -> None:
    module = importlib.import_module("jentic_one.broker.core.errors")
    defined_here = {
        name: obj
        for name, obj in vars(module).items()
        if getattr(obj, "__module__", None) == module.__name__
    }
    assert defined_here, "broker/core/errors.py defines nothing"
    not_exceptions = sorted(
        name
        for name, obj in defined_here.items()
        if not (inspect.isclass(obj) and issubclass(obj, BaseException))
    )
    assert not_exceptions == [], (
        "broker/core/errors.py must define only exception classes; move "
        f"{not_exceptions} (e.g. directive builders to broker/core/directives.py)"
    )


def test_discovery_errors_are_defined_once() -> None:
    definitions: dict[str, list[str]] = {name: [] for name in _DISCOVERY_ERRORS}
    for path in python_files_in(SRC_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in definitions:
                definitions[node.name].append(str(path.relative_to(SRC_ROOT)))
    expected = str(_DISCOVERY_MODULE.relative_to(SRC_ROOT))
    duplicated = {name: where for name, where in definitions.items() if where != [expected]}
    assert duplicated == {}, (
        f"Define the operation-discovery errors only in {expected}: {duplicated}"
    )
