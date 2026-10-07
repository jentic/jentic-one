"""Enforce that all routers use build_link for link construction.

Routers must not construct link URLs by concatenating request.base_url with
path strings directly. Instead, they must use the shared `build_link` helper
from `jentic_one.shared.web.links`.

The inspect router is allowed to pass base_url to the InspectService, which
needs a raw base URL string to construct self-referential links at the service
layer.
"""

from __future__ import annotations

from .conftest import SRC_ROOT

ROUTER_DIRS = [
    SRC_ROOT / "admin" / "web" / "routers",
    SRC_ROOT / "broker" / "web" / "routers",
    SRC_ROOT / "registry" / "web" / "routers",
    SRC_ROOT / "control" / "web" / "routers",
]

ALLOWED_FILES = frozenset({"inspect.py"})


def test_no_direct_base_url_usage_in_routers() -> None:
    """Routers must not access request.base_url directly — use build_link instead."""
    errors: list[str] = []
    for router_dir in ROUTER_DIRS:
        if not router_dir.exists():
            continue
        for py_file in router_dir.rglob("*.py"):
            if py_file.name in ALLOWED_FILES:
                continue
            source = py_file.read_text()
            if "request.base_url" not in source:
                continue
            rel = py_file.relative_to(SRC_ROOT.parent.parent)
            errors.append(f"{rel}: uses request.base_url directly (use build_link instead)")

    assert not errors, "Routers must use build_link, not raw request.base_url:\n" + "\n".join(
        errors
    )
