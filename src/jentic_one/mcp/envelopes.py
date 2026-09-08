"""Tool-result envelopes for the mounted MCP app — the coded result taxonomy in Python.

Mirrors the Go stdio server's ``result``/``softError`` helpers
(``cli/internal/cli/api/mcp_server.go``): every tool result is the payload
object with a top-level sibling ``instance`` stamp (a strict superset of the
CLI envelope — never a wrapper, never ``_meta``), and every failure that is a
diagnosable state reaches the model as an ``isError`` result carrying the SAME
coded contract keys the CLI agent envelope uses
(``{schema_version, error_code, error, actionable_step, next_tool, …}``).

The error-code strings are the CLI taxonomy (``cli/internal/cli/ux/contract.go``)
verbatim — the shared golden transcripts compare envelopes across the two
implementations, so the spellings must never drift.

The instance stamp here is built **in-process** from the live ``Context``
(``resolve_instance_identity`` — the same projection ``GET /instance`` serves),
so unlike the Go client's TTL-cached wire fetch it is always fresh and never
degrades to ``backend: "unreachable"``: the process answering the tool call
*is* the instance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import mcp.types as mcp_types

from jentic_one.shared.context import Context
from jentic_one.shared.web.instance_identity import resolve_instance_identity

#: Pins the machine-contract shape of the MCP tool payloads, mirroring the CLI
#: envelopes' ``schema_version`` (Go: ``mcpSchemaVersion``).
SCHEMA_VERSION = "1"

# The CLI's coded error taxonomy (cli/internal/cli/ux/contract.go), verbatim.
CODE_NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
CODE_PENDING_APPROVAL = "PENDING_APPROVAL"
CODE_RESOLVE_FAILED = "RESOLVE_FAILED"
CODE_BROKER_DENIED = "BROKER_DENIED"
CODE_TRANSPORT_ERROR = "TRANSPORT_ERROR"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"

#: error codes whose default recovery pointer is ``get_started`` (Go:
#: ``softErrorExtra``'s code-keyed mapping). ``get_started`` is not served by
#: this mount yet (it queues behind this PR with the other CLI-flavoured
#: tools), but the pointer strings are part of the shared envelope contract —
#: they must match the stdio server byte-for-byte.
_DEFAULT_NEXT_TOOL_CODES = frozenset(
    {CODE_NOT_AUTHENTICATED, CODE_PENDING_APPROVAL, CODE_RESOLVE_FAILED}
)


class ToolError(Exception):
    """A coded, diagnosable tool failure — becomes an ``isError`` result.

    The Python twin of the Go ``ux.CodedError`` for this surface. Raise it
    anywhere inside a tool handler; the dispatcher renders it through
    :func:`soft_error_result`. Protocol errors (malformed requests) are NOT
    ``ToolError``\\ s — raise ``MCPError(INVALID_PARAMS)`` for those.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        actionable: str = "",
        details: dict[str, Any] | None = None,
        next_tool: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.actionable = actionable
        self.details = details
        self.next_tool = next_tool
        self.extra = extra


def instance_stamp(ctx: Context) -> dict[str, Any]:
    """The identity stamp joined to every tool result.

    Same keys as the Go client's cached ``GET /instance`` projection
    (``backend``/``host``/``instance_id``/``fetched_at``); values come from the
    in-process identity resolver, and ``fetched_at`` is now — the stamp is
    read at answer time from the answering process itself.
    """
    identity = resolve_instance_identity(ctx)
    return {
        "backend": identity.backend,
        "host": identity.host,
        "instance_id": identity.instance_id,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _text_result(payload: dict[str, Any], *, is_error: bool = False) -> mcp_types.CallToolResult:
    data = json.dumps(payload, separators=(", ", ": "), ensure_ascii=False)
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=data)],
        is_error=is_error,
    )


def tool_result(ctx: Context, payload: dict[str, Any]) -> mcp_types.CallToolResult:
    """Build the one tool-result shape every tool returns (Go: ``result``)."""
    payload["instance"] = instance_stamp(ctx)
    return _text_result(payload)


def soft_error_result(ctx: Context, err: ToolError) -> mcp_types.CallToolResult:
    """Render a coded failure as an ``isError`` tool result (Go: ``softErrorExtra``)."""
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "error_code": err.code,
        "error": err.message,
    }
    if err.actionable:
        payload["actionable_step"] = err.actionable
    if err.details:
        payload["details"] = err.details
    next_tool = err.next_tool
    if not next_tool and err.code in _DEFAULT_NEXT_TOOL_CODES:
        next_tool = "get_started"
    if next_tool:
        payload["next_tool"] = next_tool
    for key, value in (err.extra or {}).items():
        payload.setdefault(key, value)
    payload["instance"] = instance_stamp(ctx)
    return _text_result(payload, is_error=True)
