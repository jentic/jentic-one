"""Enforce that the MRO-walk error handling logic lives only in the shared module.

Surfaces must delegate to make_service_error_handler rather than implementing
their own MRO-walking handler loops.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from .conftest import SRC_ROOT

SURFACE_ERROR_FILES = [
    SRC_ROOT / "admin" / "web" / "errors.py",
    SRC_ROOT / "registry" / "web" / "errors.py",
    SRC_ROOT / "auth" / "web" / "errors.py",
    SRC_ROOT / "control" / "web" / "errors.py",
]

SURFACE_APP_MODULES = [
    "jentic_one.admin.web.app",
    "jentic_one.registry.web.app",
    "jentic_one.auth.web.app",
    "jentic_one.control.web.app",
]


pytestmark = pytest.mark.arch


@pytest.mark.parametrize("filepath", SURFACE_ERROR_FILES, ids=lambda p: p.name)
def test_no_mro_walk_in_surface_error_handlers(filepath: Path) -> None:
    """Surface error modules must not contain their own MRO-walking loop."""
    source = filepath.read_text(encoding="utf-8")
    assert "for error_cls in type(exc).__mro__" not in source, (
        f"{filepath.relative_to(SRC_ROOT)} contains an MRO-walk loop. "
        "Use make_service_error_handler from shared.web.errors instead."
    )


@pytest.mark.parametrize("module_path", SURFACE_APP_MODULES, ids=lambda m: m.split(".")[1])
def test_surface_get_exception_handlers_is_non_empty(module_path: str) -> None:
    """Every surface must register at least one exception handler."""
    mod = importlib.import_module(module_path)
    assert hasattr(mod, "get_exception_handlers"), (
        f"{module_path} is missing get_exception_handlers()"
    )
    handlers = mod.get_exception_handlers()
    assert len(handlers) > 0, f"{module_path}.get_exception_handlers() returned an empty list"
