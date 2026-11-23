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

from tests.arch._markers import declares_module_marker, unmarked_tests

pytestmark = pytest.mark.arch

_ARCH_ROOT = Path(__file__).parent


@pytest.mark.parametrize("path", sorted(_ARCH_ROOT.glob("test_*.py")), ids=lambda p: Path(p).name)
def test_an_arch_module_is_collected_by_the_arch_marker(path: Path) -> None:
    tree = ast.parse(path.read_text())
    if declares_module_marker(tree, "arch"):
        return
    unmarked = unmarked_tests(tree, "arch")
    assert not unmarked, (
        f"{path.name} declares no module-level `pytestmark = pytest.mark.arch`, and "
        f"{', '.join(unmarked)} carry no marker either, so `make test-arch` (which filters "
        "-m arch) does not run them"
    )
