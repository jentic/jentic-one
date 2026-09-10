"""Forbid post-build MCP handler registration in application code.

The ``/mcp`` mount's pre-auth ``resources/read`` door is held closed by a
layered defense (see the ``PRE_AUTH_METHODS`` comment in
``jentic_one/mcp/app.py``): the resolver is characterized as two-armed, and
``build_mcp_server``'s ``on_read_resource`` is pinned to be a bare delegation
to it. Both pins inspect **source that exists today** — the SDK's
``Server.add_request_handler`` / ``add_notification_handler`` would replace a
registered handler *after* ``build_mcp_server`` returns (from the installer,
or anywhere else holding the ``Server`` instance), bypassing both pins without
touching the pinned code. Nothing in the platform needs post-build
registration: every handler is wired declaratively in ``build_mcp_server``.
This test keeps it that way — any attribute reference to either method (call
or alias) under ``src/`` is a violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_FORBIDDEN_ATTRS = frozenset({"add_request_handler", "add_notification_handler"})


def _check_file(filepath: Path) -> list[str]:
    """Return violations for post-build MCP handler registration."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            violations.append(
                f"{filepath}:{node.lineno} — '{node.attr}' replaces an MCP handler "
                f"after build_mcp_server returns, bypassing the pre-auth delegation "
                f"pins; wire handlers declaratively in build_mcp_server instead"
            )

    return violations


@pytest.mark.arch
def test_no_post_build_mcp_handler_registration() -> None:
    """No module under ``src/`` registers MCP handlers outside ``build_mcp_server``."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        violations.extend(_check_file(py_file))
    assert not violations, (
        "Post-build MCP handler registration bypasses the pre-auth delegation pins:\n"
        + "\n".join(violations)
    )
