"""Daemon-native MCP over Streamable HTTP — the phase-3 ``/mcp`` mount.

This package is **composition-layer code**, like ``jentic_one.wiring``: it
lives outside every surface package so it may import several surfaces to wire
the tool handlers in-process (registry search/inspect/catalog, admin jobs,
auth identity, control-plane config), which no single surface is allowed to
do. The architecture-boundary tests scan only the surface packages
(``broker``, ``registry``, ``admin``, ``control``, ``shared``, ``auth``) and
deliberately do not constrain this layer.

The mount is installed by :func:`jentic_one.mcp.installer.install_mcp_mount`
on **control-plane app shapes only** (the combined app / a standalone control
surface — never the broker: master plan §6 Q1, resolved 2026-09-02, the
broker stays MCP-free; ``execute`` proxies control-plane→broker server-side).

Tool surface: the phase-1 spec (``docs/reference/mcp-tools.json``, pinned from
the Go stdio server's ``toolSpecs()``). This package **consumes** that spec —
divergence is a bug, enforced by ``tests/unit/mcp/test_tool_surface.py`` on
this side and ``cli/internal/cli/api/mcp_spec_test.go`` on the Go side.
"""
