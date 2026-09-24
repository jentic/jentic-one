"""Loader for the pinned tool-surface spec (``docs/reference/mcp-tools.json``).

The Go stdio server's ``toolSpecs()`` is the single source of truth for the
MCP tool surface; ``cli/internal/cli/api/mcp_spec_test.go`` pins
it into ``docs/reference/mcp-tools.json``, and this module loads that file so
the mounted app **consumes** the spec instead of re-declaring it — names,
titles, descriptions, input schemas, and annotations can never drift from the
stdio server (divergence fails ``tests/unit/mcp/test_tool_surface.py``).

The file rides the sdist/wheel via the package-data copy under
``jentic_one/mcp/_spec/mcp-tools.json`` (kept in sync by the same drift test);
the repo-root copy stays the reviewable contract document.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import mcp.types as mcp_types

#: The lane this mount serves. The pinned spec's base renderings are the
#: stdio server's; a tool may carry ``lane_overrides[LANE]`` re-rendering
#: prose that would lie on this lane (e.g. recovery instructions naming
#: ``get_started``, which only stdio serves). Wire shape — names, input
#: schemas, annotations — is lane-invariant by design.
LANE = "http"

#: The tools this mount serves — the subset of the pinned surface whose
#: dispatch is clean in-process (registry search/inspect/catalog + the catalog
#: import loop, admin jobs, auth whoami, control access requests) or a
#: server-side broker proxy (the execute family). ``get_started`` never ports
#: — it diagnoses *the local machine's* CLI setup, and over HTTP there is no
#: local machine; it stays stdio-only.
SERVED_TOOLS: tuple[str, ...] = (
    "whoami",
    "search_apis",
    "inspect_operation",
    "execute",
    "execute_read",
    "get_execution_result",
    "search_catalog",
    "import_api",
    "request_access",
)


@dataclass(frozen=True)
class ToolSpec:
    """One pinned tool declaration from the spec document."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, bool]
    lane_overrides: dict[str, dict[str, str]]

    @property
    def served_description(self) -> str:
        """This lane's rendering: the ``LANE`` override when pinned, else base.

        Both renderings ride the one contract document
        (``docs/reference/mcp-tools.json``) and both are drift-guarded —
        the Go side pins the overrides' existence and content
        (``TestMCPToolSurfaceSpec`` / ``TestMCPToolSpecs_LaneOverridesAreDeliberate``),
        this side pins that the SERVED rendering never names an unserved tool
        (``tests/unit/mcp/test_tool_surface.py``).
        """
        return self.lane_overrides.get(LANE, {}).get("description") or self.description

    def as_mcp_tool(self) -> mcp_types.Tool:
        """Project the pinned declaration onto the SDK's ``Tool`` type."""
        annotations = mcp_types.ToolAnnotations(
            read_only_hint=self.annotations.get("read_only_hint"),
            destructive_hint=self.annotations.get("destructive_hint"),
            idempotent_hint=self.annotations.get("idempotent_hint"),
            open_world_hint=self.annotations.get("open_world_hint"),
        )
        return mcp_types.Tool(
            name=self.name,
            title=self.title,
            description=self.served_description,
            input_schema=self.input_schema,
            annotations=annotations,
        )


@cache
def load_spec() -> dict[str, ToolSpec]:
    """Load and cache the pinned spec, keyed by tool name."""
    raw = resources.files("jentic_one.mcp").joinpath("_spec/mcp-tools.json").read_text("utf-8")
    doc = json.loads(raw)
    specs = {}
    for tool in doc["tools"]:
        specs[tool["name"]] = ToolSpec(
            name=tool["name"],
            title=tool["title"],
            description=tool["description"],
            input_schema=tool["input_schema"],
            annotations=tool["annotations"],
            lane_overrides=tool.get("lane_overrides", {}),
        )
    return specs


def served_tools() -> list[mcp_types.Tool]:
    """The mount's ``tools/list`` payload: the served subset, spec order."""
    specs = load_spec()
    return [specs[name].as_mcp_tool() for name in SERVED_TOOLS]
