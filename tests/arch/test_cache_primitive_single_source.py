"""Enforce that in-process TTL caching has one implementation: shared/cache.

A TTL cache looks small enough to write inline, so copies accumulate: each one
re-derives expiry, LRU bounds, single-flight loading, invalidation fencing and
cancellation safety, and they drift apart on exactly those subtleties (#1955,
#1960). Two copies can even land without a merge conflict by sitting at
different paths. Any class whose name describes a TTL cache therefore has to be
the one in ``shared/cache/ttl_lru.py``; callers configure that primitive
(``ttl_seconds``, ``ttl_for``, ``failure_ttl_seconds``, ``metrics_*``) or wrap
it, rather than growing their own.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.arch.conftest import SRC_ROOT, python_files_in

TTL_CACHE_MODULE = SRC_ROOT / "shared" / "cache" / "ttl_lru.py"

# Class names that describe a TTL-bounded cache: a "ttl" next to a "cache" or an
# "lru", in either order (AsyncTTLCache, TTLLRU, TtlCacheStore, LruTtlMap, …).
_TTL_CACHE_NAME = re.compile(r"(ttl.*(cache|lru))|((cache|lru).*ttl)", re.IGNORECASE)


def _ttl_cache_classes(path: Path) -> list[str]:
    """Names of TTL-cache-shaped classes defined anywhere in ``path``."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:  # pragma: no cover - a syntax error fails other gates
        return []
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _TTL_CACHE_NAME.search(node.name)
    ]


@pytest.mark.arch
def test_ttl_cache_classes_are_defined_only_in_shared_cache() -> None:
    """Every TTL-cache class in the codebase is the shared primitive."""
    definitions: dict[str, list[Path]] = {}
    for path in python_files_in(SRC_ROOT):
        for name in _ttl_cache_classes(path):
            definitions.setdefault(name, []).append(path)

    violations = [
        f"{path.relative_to(SRC_ROOT)} — class {name}"
        for name, paths in sorted(definitions.items())
        for path in paths
        if path != TTL_CACHE_MODULE
    ]
    assert not violations, (
        "TTL caches must be the shared primitive in shared/cache/ttl_lru.py "
        "(configure it or wrap it — do not define another one):\n"
        + "\n".join(f"  {v}" for v in violations)
    )

    duplicates = [
        f"{name} defined {len(paths)}x: " + ", ".join(str(p.relative_to(SRC_ROOT)) for p in paths)
        for name, paths in sorted(definitions.items())
        if len(paths) > 1
    ]
    assert not duplicates, "A TTL cache class is defined more than once:\n" + "\n".join(
        f"  {d}" for d in duplicates
    )


@pytest.mark.arch
def test_async_ttl_cache_is_imported_from_shared_cache() -> None:
    """Callers reach the primitive through ``jentic_one.shared.cache``."""
    violations: list[str] = []
    for path in python_files_in(SRC_ROOT):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error fails other gates
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if any(alias.name in {"AsyncTTLCache", "TTLLRU"} for alias in node.names) and (
                node.module
                not in {
                    "jentic_one.shared.cache",
                    "jentic_one.shared.cache.ttl_lru",
                }
            ):
                violations.append(
                    f"{path.relative_to(SRC_ROOT)}:{node.lineno} — from {node.module}"
                )

    assert not violations, (
        "AsyncTTLCache/TTLLRU must be imported from jentic_one.shared.cache:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
