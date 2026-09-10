"""Lane-aware ``next_tool`` rendering — the #1254 regression guard.

The HTTP mount serves the :data:`jentic_one.mcp.spec.SERVED_TOOLS` subset of
the pinned surface, but the coded soft errors carry the stdio lane's shared
pointer spellings (``get_started``, ``request_access``, …). A pointer at a
tool absent from this lane's ``tools/list`` is an unactionable dead end, so
:func:`soft_error_result` — the one seam every soft error renders through —
must drop any ``next_tool`` that does not resolve in ``SERVED_TOOLS``.

These tests pin that projection for every pointer the pinned spec knows
about, so serving (or unserving) a tool consciously changes which envelopes
carry pointers. The stdio lane is untouched: the Go server serves all ten
tools, and its envelope bytes stay pinned by ``mcp_golden_test.go``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from jentic_one.mcp.envelopes import (
    _DEFAULT_NEXT_TOOL_CODES,
    CODE_TRANSPORT_ERROR,
    ToolError,
    soft_error_result,
)
from jentic_one.mcp.spec import SERVED_TOOLS, load_spec
from jentic_one.shared.config import AuthConfig, ServerConfig


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    return ctx


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


@pytest.mark.parametrize("code", sorted(_DEFAULT_NEXT_TOOL_CODES))
def test_default_get_started_pointer_is_dropped_on_this_lane(code: str) -> None:
    """The code-keyed stdio default (``get_started``) never reaches a rendered
    envelope while the tool stays unserved here — no pointer beats a dangling
    one."""
    payload = _payload(soft_error_result(_ctx(), ToolError(code, "boom")))
    assert "next_tool" not in payload


@pytest.mark.parametrize("pointer", [*sorted(set(load_spec()) - set(SERVED_TOOLS)), "made_up_tool"])
def test_unserved_explicit_pointers_are_dropped_on_this_lane(pointer: str) -> None:
    """An explicit pointer at a tool outside SERVED_TOOLS (``get_started``
    today, or anything unknown) is stripped at render time."""
    err = ToolError(CODE_TRANSPORT_ERROR, "boom", next_tool=pointer)
    payload = _payload(soft_error_result(_ctx(), err))
    assert "next_tool" not in payload


@pytest.mark.parametrize("pointer", sorted(SERVED_TOOLS))
def test_served_pointers_ride_the_envelope_unchanged(pointer: str) -> None:
    """Pointers that resolve in this lane's tools/list pass through verbatim
    (whoami on broker denials, search_apis on inspect 404s, …)."""
    err = ToolError(CODE_TRANSPORT_ERROR, "boom", next_tool=pointer)
    payload = _payload(soft_error_result(_ctx(), err))
    assert payload["next_tool"] == pointer


def test_every_emittable_pointer_resolves_in_the_served_surface() -> None:
    """The #1254 invariant, as a property of the rendering seam: whatever
    pointer a handler raises — every spec-declared name and a garbage one —
    the rendered envelope's ``next_tool`` is always in SERVED_TOOLS."""
    for pointer in [*load_spec(), "made_up_tool", ""]:
        for code in ["NOT_AUTHENTICATED", "PENDING_APPROVAL", "RESOLVE_FAILED", "TRANSPORT_ERROR"]:
            err = ToolError(code, "boom", next_tool=pointer)
            payload = _payload(soft_error_result(_ctx(), err))
            emitted = payload.get("next_tool")
            assert emitted is None or emitted in SERVED_TOOLS


def test_extra_carried_pointers_cannot_bypass_the_lane_filter() -> None:
    """The ``extra`` pass-through is also an emission path: a call site
    passing ``extra={"next_tool": …}`` must hit the same SERVED_TOOLS
    projection, or the invariant above is only true for the ``next_tool``
    keyword. Unserved pointers are dropped; served ones still ride."""
    dangling = ToolError(CODE_TRANSPORT_ERROR, "boom", extra={"next_tool": "get_started"})
    assert "next_tool" not in _payload(soft_error_result(_ctx(), dangling))

    served = ToolError(CODE_TRANSPORT_ERROR, "boom", extra={"next_tool": "whoami"})
    assert _payload(soft_error_result(_ctx(), served))["next_tool"] == "whoami"
