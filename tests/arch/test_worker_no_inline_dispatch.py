"""Arch test: the async worker holds no inline upstream dispatch.

"One pipeline, two callers": the design requires the async worker
to run upstream calls through the **shared** ``BrokerExecutionPipeline`` (via the
injected ``UpstreamExecutor``), not a second raw-``httpx`` path. This test fails
if ``shared/jobs/execution_handler.py`` ever reintroduces ``httpx`` (or any
inline client send/stream), which would silently bypass the circuit breaker,
per-host bulkhead, response-size cap, and post-response enrichment.
"""

from __future__ import annotations

import ast

import pytest

from .conftest import SRC_ROOT

_EXECUTION_HANDLER = SRC_ROOT / "shared" / "jobs" / "execution_handler.py"


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _parse() -> ast.AST:
    return ast.parse(
        _EXECUTION_HANDLER.read_text(encoding="utf-8"), filename=str(_EXECUTION_HANDLER)
    )


@pytest.mark.arch
def test_execution_handler_does_not_import_httpx() -> None:
    """The worker must dispatch through the injected executor, never httpx itself."""
    modules = _imported_modules(_parse())
    offending = {m for m in modules if m == "httpx" or m.startswith("httpx.")}
    assert not offending, (
        "shared/jobs/execution_handler.py imports httpx — the async worker must "
        "dispatch through the shared BrokerExecutionPipeline (UpstreamExecutor), "
        f"not a raw HTTP path. Offending imports: {sorted(offending)}"
    )


@pytest.mark.arch
def test_execution_handler_has_no_inline_client_dispatch() -> None:
    """No ``AsyncClient(...)``/``.send(``/``.stream(`` inline dispatch in the worker."""
    source = _EXECUTION_HANDLER.read_text(encoding="utf-8")
    for needle in ("AsyncClient(", ".send(", ".stream("):
        assert needle not in source, (
            f"shared/jobs/execution_handler.py contains inline dispatch '{needle}'. "
            "The async worker must go through the injected UpstreamExecutor."
        )


@pytest.mark.arch
def test_execution_handler_uses_the_upstream_executor_protocol() -> None:
    """Positive guard: the handler depends on the shared UpstreamExecutor seam."""
    modules = _imported_modules(_parse())
    assert "jentic_one.shared.jobs.protocols" in modules
    source = _EXECUTION_HANDLER.read_text(encoding="utf-8")
    assert "UpstreamExecutor" in source
