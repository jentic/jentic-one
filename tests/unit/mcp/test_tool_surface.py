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

import ast
import json
import re
from importlib import resources
from pathlib import Path

from jentic_one.mcp.spec import LANE, SERVED_TOOLS, load_spec, served_tools
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
    """Name/title/description/schema/annotations project verbatim from the pin.

    The description is the pinned LANE rendering: the ``lane_overrides``
    entry for this mount's lane when the spec pins one, else the base
    (stdio) rendering — BOTH ride the same pinned document, so either
    rendering drifting fails this side or the Go side against the same file.
    """
    pinned = {tool["name"]: tool for tool in json.loads(_REPO_SPEC.read_bytes())["tools"]}
    for tool in served_tools():
        want = pinned[tool.name]
        want_description = (
            want.get("lane_overrides", {}).get(LANE, {}).get("description") or want["description"]
        )
        assert tool.title == want["title"]
        assert tool.description == want_description
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
    """``get_started`` never ports (it diagnoses the local machine's CLI
    setup — over HTTP there is no local machine) — pinned so serving it is a
    conscious decision."""
    specs = load_spec()
    deferred = set(specs) - set(SERVED_TOOLS)
    assert deferred == {"get_started"}


def _tool_name_mentions(text: str, names: set[str]) -> set[str]:
    """Which of ``names`` appear in ``text`` as whole words."""
    return {name for name in names if re.search(rf"\b{re.escape(name)}\b", text)}


def test_served_descriptions_never_name_unserved_tools() -> None:
    """THE lane-honesty invariant (#1327): no description served on this lane
    may name a tool absent from this lane's ``tools/list``. A future tool
    whose prose routes the model at an unserved tool fails HERE, loudly —
    the fix is a ``lane_overrides`` entry in the Go ``toolSpecs()`` pin, not
    a silent fork."""
    specs = load_spec()
    unserved = set(specs) - set(SERVED_TOOLS)
    for tool in served_tools():
        assert tool.description is not None
        dangling = _tool_name_mentions(tool.description, unserved)
        assert not dangling, (
            f"{tool.name}: served description names unserved tool(s) {sorted(dangling)} — "
            f"add/extend a lane_overrides[{LANE!r}] rendering in the Go toolSpecs() pin"
        )


def test_actionable_prose_never_names_unserved_tools() -> None:
    """The same lane-honesty invariant for ``actionable_step`` prose: every
    ``actionable=…`` string literal in this mount's handler modules must not
    name a tool absent from ``SERVED_TOOLS``. (The machine-readable
    ``next_tool`` pointers are lane-filtered at render time in
    ``soft_error_result`` — #1254; prose cannot be rewritten at render time,
    so it is guarded at the source instead.)"""
    specs = load_spec()
    unserved = set(specs) - set(SERVED_TOOLS)
    mcp_pkg = Path(__file__).resolve().parents[3] / "src" / "jentic_one" / "mcp"

    def _literal_text(node: ast.expr) -> str:
        """Collect the string-constant parts of an expression (handles
        implicit/explicit concatenation and f-string literal segments)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp):
            return _literal_text(node.left) + _literal_text(node.right)
        if isinstance(node, ast.JoinedStr):
            return "".join(_literal_text(v) for v in node.values)
        return ""

    checked = 0
    for path in sorted(mcp_pkg.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "actionable" or kw.value is None:
                    continue
                text = _literal_text(kw.value)
                if not text:
                    continue
                checked += 1
                dangling = _tool_name_mentions(text, unserved)
                assert not dangling, (
                    f"{path.name}:{node.lineno}: actionable prose names unserved "
                    f"tool(s) {sorted(dangling)}: {text!r}"
                )
    assert checked >= 10, "the actionable= scan found suspiciously few call sites"
