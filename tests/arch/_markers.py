"""AST helpers for suite marker guards (`test_arch_markers.py`, `test_suite_markers.py`)."""

from __future__ import annotations

import ast


def declares_module_marker(tree: ast.Module, marker: str = "arch") -> bool:
    """Whether ``tree`` sets a module-level ``pytestmark`` containing ``marker``."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            continue
        if f"mark.{marker}" in ast.unparse(node.value) or marker in ast.unparse(node.value):
            return True
    return False


def unmarked_tests(tree: ast.Module, marker: str = "arch") -> list[str]:
    """Top-level ``test_*`` functions and ``Test*`` class methods carrying no ``marker``."""
    unmarked: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.name.startswith("test_"):
                continue
            if not any(f"mark.{marker}" in ast.unparse(d) for d in node.decorator_list):
                unmarked.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            if any(f"mark.{marker}" in ast.unparse(d) for d in node.decorator_list):
                continue
            for item in node.body:
                if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if not item.name.startswith("test_"):
                    continue
                if not any(f"mark.{marker}" in ast.unparse(d) for d in item.decorator_list):
                    unmarked.append(f"{node.name}.{item.name}")
    return unmarked
