"""Every integration, web, smoke, and unit test module declares the marker its CI job expects.

See ``tests/arch/test_arch_markers.py`` for background: ``make test-integration`` and
``make test-integration-sqlite`` filter on ``-m integration``, and ``make test-smoke``
filters on ``-m smoke``. An unmarked module outside ``tests/unit/`` is silently skipped
by every CI job. Conversely, a module under ``tests/unit/`` must never carry the
``integration`` marker because the unit CI job runs without database fixtures.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._markers import declares_module_marker, unmarked_tests

pytestmark = pytest.mark.arch

_TESTS_ROOT = Path(__file__).resolve().parent.parent

_INTEGRATION_PATHS = sorted(
    [
        *(_TESTS_ROOT / "integration").rglob("test_*.py"),
        *(_TESTS_ROOT / "web").rglob("test_*.py"),
    ]
)
_SMOKE_PATHS = sorted((_TESTS_ROOT / "smoke").rglob("test_*.py"))
_UNIT_PATHS = sorted((_TESTS_ROOT / "unit").rglob("test_*.py"))


def _rel_id(path: Path) -> str:
    return str(path.relative_to(_TESTS_ROOT))


def test_integration_module_count_floor() -> None:
    assert len(_INTEGRATION_PATHS) > 200, (
        f"Expected > 200 integration/web test modules, found {len(_INTEGRATION_PATHS)}"
    )


@pytest.mark.parametrize("path", _INTEGRATION_PATHS, ids=_rel_id)
def test_every_integration_module_carries_the_integration_marker(path: Path) -> None:
    tree = ast.parse(path.read_text())
    if declares_module_marker(tree, "integration"):
        return
    unmarked = unmarked_tests(tree, "integration")
    assert not unmarked, (
        f"{_rel_id(path)} declares no module-level `pytestmark = pytest.mark.integration`, and "
        f"{', '.join(unmarked)} carry no marker either, so `make test-integration` (which filters "
        "-m integration) does not run them"
    )


@pytest.mark.parametrize("path", _SMOKE_PATHS, ids=_rel_id)
def test_every_smoke_module_carries_the_smoke_marker(path: Path) -> None:
    tree = ast.parse(path.read_text())
    if declares_module_marker(tree, "smoke"):
        return
    unmarked = unmarked_tests(tree, "smoke")
    assert not unmarked, (
        f"{_rel_id(path)} declares no module-level `pytestmark = pytest.mark.smoke`, and "
        f"{', '.join(unmarked)} carry no marker either, so `make test-smoke` (which filters "
        "-m smoke) does not run them"
    )


@pytest.mark.parametrize("path", _UNIT_PATHS, ids=_rel_id)
def test_no_unit_module_declares_the_integration_marker(path: Path) -> None:
    tree = ast.parse(path.read_text())
    assert not declares_module_marker(tree, "integration"), (
        f"{_rel_id(path)} under tests/unit/ declares `pytest.mark.integration`; unit tests run "
        "without database fixtures"
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            assert not any("mark.integration" in ast.unparse(d) for d in node.decorator_list), (
                f"{_rel_id(path)}:{node.name} under tests/unit/ carries `@pytest.mark.integration`"
            )


def test_marker_guard_rejects_an_unmarked_module() -> None:
    tree = ast.parse("def test_x():\n    pass\n")
    assert not declares_module_marker(tree, "integration")
    assert unmarked_tests(tree, "integration") == ["test_x"]
