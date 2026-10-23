"""Enforce that in-process TTL caching has one implementation: shared/cache.

A TTL cache looks small enough to write inline, so copies accumulate: each one
re-derives expiry, LRU bounds, single-flight loading, invalidation fencing and
cancellation safety, and they drift apart on exactly those subtleties (#1955,
#1960). Two copies can even land without a merge conflict by sitting at
different paths, which is how the duplicate this gate was written for arrived.

Scope: these tests catch a **copied or renamed** cache primitive — a class whose
name puts a "ttl" next to a "cache" or an "lru", or a ``SingleFlight`` — and any
import of one from outside ``shared/cache``. They do not recognise a cache
hand-rolled under an unrelated name (``ExpiringCache``, ``PreviewMemo``), so
they are a tripwire on the cheap failure mode, not proof that no second cache
exists; reviewers still own that. What the gate does guarantee is that callers
configure the shared primitive (``ttl_seconds``, ``ttl_for``,
``failure_ttl_seconds``, ``metrics_*``) or wrap it, rather than re-deriving it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.arch.conftest import SRC_ROOT, python_files_in

TTL_CACHE_MODULE = SRC_ROOT / "shared" / "cache" / "ttl_lru.py"
SINGLEFLIGHT_MODULE = SRC_ROOT / "shared" / "cache" / "singleflight.py"

# Class names that describe a TTL-bounded cache: a "ttl" next to a "cache" or an
# "lru", in either order (AsyncTTLCache, TTLLRU, TtlCacheStore, LruTtlMap, …).
_TTL_CACHE_NAME = re.compile(r"(ttl.*(cache|lru))|((cache|lru).*ttl)", re.IGNORECASE)
# The other primitive #1960 de-duplicated: per-key load coalescing, whose
# cancellation safety is the subtlety every copy gets wrong.
_SINGLEFLIGHT_NAME = re.compile(r"single.?flight", re.IGNORECASE)

# Each primitive's one home: a class matching the pattern belongs in this module.
_PRIMITIVES = ((_TTL_CACHE_NAME, TTL_CACHE_MODULE), (_SINGLEFLIGHT_NAME, SINGLEFLIGHT_MODULE))


def _classes_named(path: Path, pattern: re.Pattern[str]) -> list[str]:
    """Names of classes defined anywhere in ``path`` whose name ``pattern`` matches."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:  # pragma: no cover - a syntax error fails other gates
        return []
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and pattern.search(node.name)
    ]


@pytest.mark.arch
def test_cache_primitive_classes_are_defined_only_in_shared_cache() -> None:
    """A cache-primitive-shaped class name resolves to the shared primitive."""
    definitions: dict[str, list[Path]] = {}
    violations: list[str] = []
    for pattern, home in _PRIMITIVES:
        for path in python_files_in(SRC_ROOT):
            for name in _classes_named(path, pattern):
                definitions.setdefault(name, []).append(path)
                if path != home:
                    violations.append(
                        f"{path.relative_to(SRC_ROOT)} — class {name} "
                        f"(belongs in {home.relative_to(SRC_ROOT)})"
                    )

    assert not violations, (
        "Cache primitives must be the shared ones in shared/cache/ "
        "(configure them or wrap them — do not define another):\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
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
def test_cache_primitives_are_imported_from_shared_cache() -> None:
    """Callers reach the primitives through ``jentic_one.shared.cache``."""
    violations: list[str] = []
    for path in python_files_in(SRC_ROOT):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error fails other gates
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if any(
                alias.name in {"AsyncTTLCache", "TTLLRU", "SingleFlight"} for alias in node.names
            ) and (
                node.module
                not in {
                    "jentic_one.shared.cache",
                    "jentic_one.shared.cache.ttl_lru",
                    "jentic_one.shared.cache.singleflight",
                }
            ):
                violations.append(
                    f"{path.relative_to(SRC_ROOT)}:{node.lineno} — from {node.module}"
                )

    assert not violations, (
        "AsyncTTLCache/TTLLRU/SingleFlight must be imported from jentic_one.shared.cache:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
