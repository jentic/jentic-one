"""Forbid module-level mutable caches in the modules that hold cross-request state.

Replay records, auth-failure counters, circuit-open dedupe and MCP session
counters must be shared by every process serving the deployment: the
``jti`` record lives in the admin database, the rest on the shared state
backend (``Context.state_backend``). A module-level ``dict``/``set``/cache in
these modules would be per-process again and silently diverge once more than
one replica runs. This test fails if one creeps back in.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch.conftest import SRC_ROOT

GUARDED_MODULES: tuple[Path, ...] = (
    SRC_ROOT / "auth" / "services" / "assertion_service.py",
    SRC_ROOT / "broker" / "web" / "deps.py",
    SRC_ROOT / "broker" / "adapters" / "runners" / "circuit.py",
    SRC_ROOT / "broker" / "services" / "execution" / "service.py",
    SRC_ROOT / "shared" / "resilience" / "circuit.py",
    SRC_ROOT / "shared" / "events" / "mcp_session.py",
    SRC_ROOT / "mcp" / "app.py",
)

# Constructors that build a mutable container or an in-process cache.
_MUTABLE_FACTORIES = frozenset(
    {
        "dict",
        "set",
        "list",
        "bytearray",
        "defaultdict",
        "OrderedDict",
        "Counter",
        "deque",
        "WeakValueDictionary",
        "WeakKeyDictionary",
        "WeakSet",
        "TTLCache",
        "LRUCache",
        "MemoryStateBackend",
    }
)


# Process-local by nature, not cross-request state: strong references to this
# process's running asyncio tasks (the event loop holds only weak ones). A task
# runs where it was created, so sharing it is meaningless.
ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("shared/events/mcp_session.py", "_background_tasks"),
        ("broker/web/deps.py", "_background_tasks"),
    },
)


def _callee_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_mutable(value: ast.expr | None) -> bool:
    if value is None:
        return False
    if isinstance(value, (ast.Dict, ast.Set, ast.List, ast.DictComp, ast.SetComp, ast.ListComp)):
        return True
    if isinstance(value, ast.Call):
        name = _callee_name(value.func) or ""
        # Any ``*Cache`` constructor (``_JTICache()``, ``TTLCache(...)``) is a cache.
        return name in _MUTABLE_FACTORIES or name.endswith("Cache")
    return False


def _targets(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _module_level_mutables(path: Path, *, root: Path = SRC_ROOT) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not _is_mutable(value):
            continue
        # ``__all__`` is an export list, not state.
        rel = path.relative_to(root).as_posix()
        names = [n for n in _targets(node) if n != "__all__" and (rel, n) not in ALLOWED]
        found.extend(f"{rel}:{node.lineno} {n}" for n in names)
    # A lazily-built module global (``global _cache; _cache = Cache()``) is the
    # same per-process state assigned later, so any ``global`` rebinding counts.
    rel = path.relative_to(root).as_posix()
    for scoped in ast.walk(tree):
        if isinstance(scoped, ast.Global):
            found.extend(f"{rel}:{scoped.lineno} global {n}" for n in scoped.names)
    return found


@pytest.mark.arch
@pytest.mark.parametrize("path", GUARDED_MODULES, ids=lambda p: str(p.relative_to(SRC_ROOT)))
def test_no_module_level_mutable_state(path: Path) -> None:
    assert path.is_file(), f"guarded module moved: {path}; update GUARDED_MODULES"
    offenders = _module_level_mutables(path)
    assert not offenders, (
        "Module-level mutable state is per-process; keep it on Context.state_backend "
        f"or in the database instead: {offenders}"
    )


@pytest.mark.arch
def test_the_detector_catches_a_module_level_cache(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from collections import defaultdict\n"
        "_seen: set[str] = set()\n"
        "_counts = defaultdict(int)\n"
        "_cache = {}\n"
        "_jti = _JTICache()\n"
        "def _get():\n"
        "    global _lazy\n"
        "LIMIT = 3\n"
        "__all__ = ['LIMIT']\n",
        encoding="utf-8",
    )
    flagged = [entry.rsplit(" ", 1)[1] for entry in _module_level_mutables(probe, root=tmp_path)]
    assert flagged == ["_seen", "_counts", "_cache", "_jti", "_lazy"]
