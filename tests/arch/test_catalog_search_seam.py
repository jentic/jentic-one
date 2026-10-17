"""Architecture test: catalog search ranking goes through the strategy seam.

``GET /catalog?q=`` ranks via ``CatalogSearchStrategy`` (see
``registry/services/catalog/search.py``). The built-in token scorer
(``manifest_builder.score_entries``) is wrapped by ``TokenSearchStrategy``;
calling it anywhere else would bypass the seam (no strategy selection, no
cursor ``st``, no fallback/validation), and touching the strategy table
directly would bypass the idempotent, non-shadowing registration hook.
"""

from __future__ import annotations

import ast

import pytest

from .conftest import SRC_ROOT, python_files_in

_CATALOG = SRC_ROOT / "registry" / "services" / "catalog"
_SEARCH = _CATALOG / "search.py"
_MANIFEST_BUILDER = _CATALOG / "manifest_builder.py"


def _names_used(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found.update(a.name for a in node.names)
    return found


@pytest.mark.arch
def test_token_scorer_is_only_called_through_the_strategy_seam() -> None:
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file in (_SEARCH, _MANIFEST_BUILDER):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        if "score_entries" in _names_used(tree):
            violations.append(str(py_file.relative_to(SRC_ROOT)))
    assert not violations, (
        "Catalog ranking must go through CatalogSearchStrategy "
        "(registry/services/catalog/search.py), not manifest_builder.score_entries:\n"
        + "\n".join(violations)
    )


@pytest.mark.arch
def test_catalog_strategy_table_is_private_to_the_seam() -> None:
    """Only ``search.py`` touches its registry; everyone else uses the hooks."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file == _SEARCH:
            continue
        source = py_file.read_text(encoding="utf-8")
        if "catalog.search" not in source and "catalog import search" not in source:
            continue
        tree = ast.parse(source, filename=str(py_file))
        if "_STRATEGIES" in _names_used(tree):
            violations.append(str(py_file.relative_to(SRC_ROOT)))
    assert not violations, (
        "Register catalog search strategies with register_catalog_search_strategy(), "
        "not by editing the strategy table:\n" + "\n".join(violations)
    )
