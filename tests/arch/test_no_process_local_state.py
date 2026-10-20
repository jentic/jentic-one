"""Forbid module-level mutable caches in the modules that hold cross-request state.

Replay records, auth-failure counters, circuit-open dedupe and MCP session
counters must be shared by every process serving the deployment: the
``jti`` record lives in the admin database, the rest on the shared state
backend (``Context.state_backend``). A module-level ``dict``/``set``/cache in
these modules would be per-process and silently diverge once more than one
replica runs. This test fails on any such binding.

Two scopes: the modules that read and write this state are checked strictly
(any module-level mutable binding); the surrounding packages are checked for
lower-case module-level mutables, since an UPPER_CASE binding is by
convention a read-only lookup table. Anything genuinely process-local is
allowlisted below with the reason. Separately, only the state factory may
construct a ``MemoryStateBackend``: a surface building its own would hold a
private copy of the shared state.
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

#: Packages scanned for lower-case module-level mutables.
GUARDED_PACKAGES: tuple[Path, ...] = (
    SRC_ROOT / "auth" / "services",
    SRC_ROOT / "broker",
    SRC_ROOT / "mcp",
    SRC_ROOT / "shared" / "events",
    SRC_ROOT / "shared" / "resilience",
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


#: Process-local by nature, not cross-request state.
ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        # Strong references to this process's running asyncio tasks (the event
        # loop holds only weak ones); a task runs where it was created.
        ("shared/events/mcp_session.py", "_background_tasks"),
        ("broker/web/deps.py", "_background_tasks"),
        # asyncio locks serialising token refreshes on SQLite, which is
        # single-process by construction (PostgreSQL uses row locks instead).
        ("broker/services/credentials/refresh.py", "_sqlite_locks"),
        # Throttles a log line; log output is per process.
        ("broker/services/execution/authorization.py", "_divergence_last_logged"),
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


def _package_files() -> list[Path]:
    return sorted(f for pkg in GUARDED_PACKAGES for f in pkg.rglob("*.py"))


@pytest.mark.arch
@pytest.mark.parametrize("path", _package_files(), ids=lambda p: str(p.relative_to(SRC_ROOT)))
def test_no_lower_case_module_level_mutables(path: Path) -> None:
    offenders = [
        entry
        for entry in _module_level_mutables(path)
        if not entry.rsplit(" ", 1)[1].isupper() or " global " in entry
    ]
    assert not offenders, (
        "Lower-case module-level mutable state is a per-process cache; keep "
        "cross-request state on Context.state_backend or in the database, or "
        f"allowlist it here with the reason it is process-local: {offenders}"
    )


@pytest.mark.arch
def test_every_allowlisted_name_still_exists() -> None:
    """A stale allowlist entry would silently exempt a future binding of that name."""
    stale: list[str] = []
    for rel, name in sorted(ALLOWED):
        path = SRC_ROOT / rel
        if not path.is_file():
            stale.append(f"{rel} (file gone)")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if name not in {n for node in tree.body for n in _targets(node)}:
            stale.append(f"{rel}:{name}")
    assert not stale, f"Remove these stale ALLOWED entries: {stale}"


#: The one module that builds the in-process backend: the state factory.
_STATE_FACTORY = SRC_ROOT / "shared" / "state" / "factory.py"


@pytest.mark.arch
def test_only_the_state_factory_builds_a_memory_backend() -> None:
    """A surface that built its own ``MemoryStateBackend`` would hold a private
    copy of state every other surface of the process (and, on a shared
    backend, every other process) is meant to see."""
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path == _STATE_FACTORY:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee_name(node.func) == "MemoryStateBackend":
                offenders.append(f"{path.relative_to(SRC_ROOT).as_posix()}:{node.lineno}")
    assert not offenders, (
        "Build the shared-state backend through the state factory "
        f"(Context.state_backend), never directly: {offenders}"
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
