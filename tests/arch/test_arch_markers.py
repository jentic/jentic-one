"""Every architecture test is one ``make test-arch`` actually runs.

The target filters on ``-m arch``, so a module declaring the marker nowhere is
collected by nobody: the suite reports green having skipped it. That is not a
hypothetical — a whole module of connect rate-limit guards sat unrun in CI,
and eight more modules (201 tests) were deselected behind the same gap. This
pins the marker rather than the files, so a new module cannot inherit it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.arch

_ARCH_ROOT = Path(__file__).parent


def _declares_module_marker(tree: ast.Module) -> bool:
    """Whether the module sets ``pytestmark`` to the arch marker."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            continue
        if "arch" in ast.unparse(node.value):
            return True
    return False


def _unmarked_tests(tree: ast.Module) -> list[str]:
    """Test functions carrying no arch marker of their own."""
    unmarked = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        if not any("mark.arch" in ast.unparse(d) for d in node.decorator_list):
            unmarked.append(node.name)
    return unmarked


@pytest.mark.parametrize("path", sorted(_ARCH_ROOT.glob("test_*.py")), ids=lambda p: Path(p).name)
def test_an_arch_module_is_collected_by_the_arch_marker(path: Path) -> None:
    tree = ast.parse(path.read_text())
    if _declares_module_marker(tree):
        return
    unmarked = _unmarked_tests(tree)
    assert not unmarked, (
        f"{path.name} declares no module-level `pytestmark = pytest.mark.arch`, and "
        f"{', '.join(unmarked)} carry no marker either, so `make test-arch` (which filters "
        "-m arch) does not run them"
    )
