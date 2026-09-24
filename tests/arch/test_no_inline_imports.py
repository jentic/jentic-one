"""Enforce that all imports are at module level — no inline imports.

Inline imports (inside functions, methods, or class bodies) hurt readability,
make dependency graphs harder to trace, and bypass linting tools.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

TESTS_ROOT = Path(__file__).resolve().parent.parent

ALLOWED_LAZY_IMPORTS: set[tuple[str, str]] = {
    ("registry/ingest/embeddings/model.py", "sentence_transformers"),
    ("registry/ingest/embeddings/text_processing.py", "spacy"),
    ("registry/ingest/embeddings/text_processing.py", "spacy.lang.en"),
    ("registry/ingest/embeddings/text_processing.py", "spacy.tokenizer"),
    (
        "shared/context.py",
        "jentic_one.control.services.credentials.providers.registry",
    ),
    (
        "shared/auth/verify.py",
        "jentic_one.admin.services.permission_service",
    ),
    (
        "shared/state/factory.py",
        "jentic_one.shared.state.redis",
    ),
    (
        "shared/config.py",
        "jentic_one.shared.crypto.signing",
    ),
}
"""(file suffix, module) pairs where lazy imports are intentionally allowed.

sentence_transformers and spacy are optional ML dependencies (registry extra only).
They must be lazy-loaded to avoid ImportError on surfaces that don't install them.

The ProviderRegistry import in context.py is lazy to break the circular dependency
(shared.context -> control.services -> shared.context).

The PermissionService lazy import in shared.auth.verify breaks the cycle where
shared.auth -> admin.services -> admin.services.schemas -> shared.auth.

The RedisStateBackend lazy import in shared.state.factory keeps the default
memory path free of the optional ``redis`` dependency (only the redis backend
selection pulls it in).
"""


def _is_allowed_lazy(filepath: Path, module: str) -> bool:
    """Check if an inline import is in the allowed lazy-import set."""
    path_str = str(filepath)
    return any(path_str.endswith(suffix) and module == mod for suffix, mod in ALLOWED_LAZY_IMPORTS)


def _check_file(filepath: Path) -> list[str]:
    """Return violation messages for any inline imports in the file."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        scope_name = node.name
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, ast.Import):
                modules = ", ".join(alias.name for alias in child.names)
                if _is_allowed_lazy(filepath, modules):
                    continue
                violations.append(
                    f"{filepath}:{child.lineno} — inline import of '{modules}' "
                    f"inside {scope_name}() (move all imports to module level)"
                )
            elif isinstance(child, ast.ImportFrom):
                module = child.module or ""
                if _is_allowed_lazy(filepath, module):
                    continue
                violations.append(
                    f"{filepath}:{child.lineno} — inline import of '{module}' "
                    f"inside {scope_name}() (move all imports to module level)"
                )

    return violations


@pytest.mark.arch
def test_src_no_inline_imports() -> None:
    """No source file should contain inline imports."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        violations.extend(_check_file(py_file))
    assert not violations, (
        "Source files must not contain inline imports — move all imports to module level:\n"
        + "\n".join(violations)
    )


@pytest.mark.arch
def test_tests_no_inline_imports() -> None:
    """No test file should contain inline imports."""
    violations: list[str] = []
    for py_file in python_files_in(TESTS_ROOT):
        violations.extend(_check_file(py_file))
    assert not violations, (
        "Test files must not contain inline imports — move all imports to module level:\n"
        + "\n".join(violations)
    )
