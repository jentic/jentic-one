"""Drift guard: ``jentic_one.composition`` public API and ``__main__`` backing symbols.

``jentic_one.composition`` exposes the process composition-root builders under
stable public names so external tests and integrators never need to import
``_``-prefixed symbols from ``jentic_one.__main__``. This gate pins:

- ``composition.__all__`` and the exact ``inspect.signature`` of each export;
- that each export is identical to the backing function in ``jentic_one.__main__``;
- that ``jentic_one.__main__`` continues to define ``_build_app``,
  ``_build_context``, and ``_expand_allowed_dbs`` at module top level.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import jentic_one.__main__ as main_module
import jentic_one.composition as composition_module

from .conftest import SRC_ROOT

pytestmark = pytest.mark.arch

EXPECTED_ALL = [
    "build_app",
    "build_context",
    "expand_allowed_dbs",
]

EXPECTED_PARAMETERS: dict[str, list[tuple[str, inspect._ParameterKind]]] = {
    "build_app": [
        ("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("apps", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ],
    "build_context": [
        ("config", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("apps", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ],
    "expand_allowed_dbs": [
        ("apps", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("config", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ],
}


def test_composition_all_and_identity() -> None:
    """``composition.__all__`` matches the contract and re-exports ``__main__`` builders."""
    assert list(composition_module.__all__) == EXPECTED_ALL
    assert composition_module.build_app is main_module._build_app
    assert composition_module.build_context is main_module._build_context
    assert composition_module.expand_allowed_dbs is main_module._expand_allowed_dbs


def test_composition_signatures() -> None:
    """Public composition functions keep their positional/keyword parameter contract."""
    for name, expected_params in EXPECTED_PARAMETERS.items():
        fn = getattr(composition_module, name)
        sig = inspect.signature(fn)
        actual = [(p.name, p.kind) for p in sig.parameters.values()]
        assert actual == expected_params, (
            f"jentic_one.composition.{name} signature drifted: "
            f"expected {expected_params}, got {actual}"
        )


def test_main_defines_backing_composition_functions() -> None:
    """``jentic_one.__main__`` defines the three backing composition functions."""
    main_path = SRC_ROOT / "__main__.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    top_level_funcs = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    required = {"_build_app", "_build_context", "_expand_allowed_dbs"}
    missing = sorted(required - top_level_funcs)
    assert not missing, (
        f"jentic_one.__main__ is missing expected composition function(s): {missing}"
    )
