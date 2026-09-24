"""Architecture test: the broker execution path is runner-shaped, not inlined.

The web edge (``broker/web/routers``) and the execution pipeline
(``broker/services/execution``) must hold **no inline transport** — every
upstream request goes through the ``UpstreamRunner`` seam, whose only concrete
HTTP implementation is ``broker/adapters/runners/http.py``. This keeps the
roadmap's resilience capabilities (retry/circuit/deadline/idempotency) pluggable
as runner decorators / pipeline stages rather than handler branches.
"""

from __future__ import annotations

import ast

import pytest

from .conftest import SRC_ROOT, python_files_in

_TRANSPORT_MODULES = ("httpx",)

# The single sanctioned place the broker performs upstream HTTP.
_ALLOWED_TRANSPORT_FILES = {
    SRC_ROOT / "broker" / "adapters" / "runners" / "http.py",
    # The shared bounded outbound client provider — owns the
    # ``httpx.AsyncClient`` the runner wraps; infra adapter, not core/.
    SRC_ROOT / "broker" / "adapters" / "http_client.py",
    # The DNS-rebinding guard transport — an httpx transport wrapper
    # that resolves+validates+pins the host IP; infra adapter, no upstream call
    # of its own (it delegates to the inner transport).
    SRC_ROOT / "broker" / "adapters" / "egress.py",
    # Pre-existing OAuth token refresh transport (deliberately sanctioned).
    SRC_ROOT / "broker" / "services" / "credentials" / "refresh.py",
}


def _imports_transport(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in _TRANSPORT_MODULES]
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] in _TRANSPORT_MODULES
        ):
            found.append(node.module)
    return found


@pytest.mark.arch
def test_router_and_pipeline_hold_no_inline_transport() -> None:
    """Routers and the execution pipeline must not import a transport library."""
    targets = [
        SRC_ROOT / "broker" / "web" / "routers",
        SRC_ROOT / "broker" / "services" / "execution",
    ]
    violations: list[str] = []
    for target in targets:
        for py_file in python_files_in(target):
            if py_file in _ALLOWED_TRANSPORT_FILES:
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for mod in _imports_transport(tree):
                violations.append(f"{py_file} — inline transport import '{mod}'")
    assert not violations, (
        "Upstream HTTP must go through the UpstreamRunner seam, not inline:\n"
        + "\n".join(violations)
    )


@pytest.mark.arch
def test_only_runner_adapter_does_upstream_http() -> None:
    """No broker file outside the sanctioned set may import a transport library."""
    broker_dir = SRC_ROOT / "broker"
    violations: list[str] = []
    for py_file in python_files_in(broker_dir):
        if py_file in _ALLOWED_TRANSPORT_FILES:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for mod in _imports_transport(tree):
            violations.append(f"{py_file} — unsanctioned transport import '{mod}'")
    assert not violations, "Only the runner adapter may import a transport library:\n" + "\n".join(
        violations
    )
