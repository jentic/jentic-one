"""Tool-surface drift tests: the mount CONSUMES the pinned phase-1 spec.

The Go stdio server's ``toolSpecs()`` is the single source of truth (master
§3.2); ``cli/internal/cli/api/mcp_spec_test.go`` pins it into
``docs/reference/mcp-tools.json``. These tests hold this side of the contract:

- the packaged copy (``jentic_one/mcp/_spec/mcp-tools.json``) is byte-identical
  to the pinned reference document (the wheel serves exactly what review saw);
- the mounted app's ``tools/list`` payload is the served subset of the pinned
  spec — names, titles, descriptions, input schemas, annotations, in spec
  order — so the two implementations can never drift apart silently;
- every served tool has a handler and every handler serves a pinned tool.

A deliberate tool change regenerates the Go pin first
(``UPDATE_MCP_SPEC=1 go test ./internal/cli/api -run TestMCPToolSurfaceSpec``),
copies it into the package data, and shows up in review as a doc diff.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from jentic_one.mcp.spec import SERVED_TOOLS, load_spec, served_tools
from jentic_one.mcp.tools import HANDLERS

_REPO_SPEC = Path(__file__).resolve().parents[3] / "docs" / "reference" / "mcp-tools.json"


def _packaged_spec_bytes() -> bytes:
    return resources.files("jentic_one.mcp").joinpath("_spec/mcp-tools.json").read_bytes()


def test_packaged_spec_is_byte_identical_to_the_pinned_reference() -> None:
    """The wheel's copy and the reviewable contract document are one file."""
    assert _packaged_spec_bytes() == _REPO_SPEC.read_bytes()


def test_served_tools_exist_in_the_pinned_spec() -> None:
    specs = load_spec()
    for name in SERVED_TOOLS:
        assert name in specs, f"served tool {name!r} is not in the pinned spec"


def test_served_tools_follow_spec_order() -> None:
    """tools/list order is the stdio server's declaration order (subset)."""
    pinned_order = [
        tool["name"]
        for tool in json.loads(_REPO_SPEC.read_bytes())["tools"]
        if tool["name"] in SERVED_TOOLS
    ]
    assert [tool.name for tool in served_tools()] == pinned_order


def test_tools_list_payload_matches_the_pinned_declarations() -> None:
    """Name/title/description/schema/annotations project verbatim from the pin."""
    pinned = {tool["name"]: tool for tool in json.loads(_REPO_SPEC.read_bytes())["tools"]}
    for tool in served_tools():
        want = pinned[tool.name]
        assert tool.title == want["title"]
        assert tool.description == want["description"]
        assert tool.input_schema == want["input_schema"]
        annotations = tool.annotations
        assert annotations is not None
        got_hints = {
            "read_only_hint": annotations.read_only_hint,
            "idempotent_hint": annotations.idempotent_hint,
            "destructive_hint": annotations.destructive_hint,
            "open_world_hint": annotations.open_world_hint,
        }
        for key, value in want["annotations"].items():
            assert got_hints[key] is value, f"{tool.name}: annotation {key} diverged"
        for key, value in got_hints.items():
            if key not in want["annotations"]:
                assert value is not True, f"{tool.name}: annotation {key} not pinned but set"


def test_handlers_cover_served_tools_exactly() -> None:
    assert set(HANDLERS) == set(SERVED_TOOLS)


def test_unserved_phase1_tools_stay_stdio_only_for_now() -> None:
    """The CLI-flavoured tools queue behind this PR (their dispatch is not
    clean in-process yet) — pinned so serving one is a conscious decision."""
    specs = load_spec()
    deferred = set(specs) - set(SERVED_TOOLS)
    assert deferred == {"get_started", "import_api", "request_access"}
