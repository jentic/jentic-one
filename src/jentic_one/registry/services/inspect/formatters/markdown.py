"""Render an OperationInspectResult as Markdown."""

from __future__ import annotations

import json

from jentic_one.registry.services.inspect.models import (
    OperationInputs,
    OperationInspectResult,
    OperationParameter,
    RequestBodySchema,
)


def render_markdown(result: OperationInspectResult) -> str:
    """Render an operation inspect result as markdown documentation."""
    return _render_operation_markdown(result)


def _render_operation_markdown(result: OperationInspectResult) -> str:
    lines: list[str] = []
    title = result.name or f"{result.method} {result.url}"
    lines.append(f"# {title}")
    lines.append("")

    if result.description:
        lines.append(result.description)
        lines.append("")

    lines.append(f"**Method:** `{result.method}`")
    lines.append(f"**URL:** `{result.url}`")
    if result.server:
        lines.append(f"**Server:** `{result.server}`")
    lines.append("")

    lines.append(f"**API:** {result.api.vendor}/{result.api.name} {result.api.version}")
    if result.api.description:
        lines.append(f"> {result.api.description}")
    lines.append("")

    if result.inputs:
        _describe_inputs(lines, result.inputs)

    if result.response_schema:
        lines.append("## Response Schema")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(result.response_schema, indent=2))
        lines.append("```")
        lines.append("")

    if result.auth:
        lines.append("## Authentication")
        lines.append("")
        for auth in result.auth:
            lines.append(f"- **{auth.type}**")
            if auth.scheme:
                lines.append(f"  - Scheme: `{auth.scheme}`")
            if auth.in_location and auth.param_name:
                lines.append(f"  - In: `{auth.in_location}` (`{auth.param_name}`)")
            if auth.bearer_format:
                lines.append(f"  - Bearer format: `{auth.bearer_format}`")
        lines.append("")

    return "\n".join(lines)


def _describe_inputs(lines: list[str], inputs: OperationInputs) -> None:
    groups = (
        ("Path Parameters", inputs.path),
        ("Query Parameters", inputs.query),
        ("Header Parameters", inputs.header),
    )
    if any(params for _, params in groups):
        lines.append("## Parameters")
        lines.append("")
        for heading, params in groups:
            if not params:
                continue
            lines.append(f"### {heading}")
            lines.append("")
            _describe_params(lines, params)
            lines.append("")

    if inputs.body is not None:
        _describe_body(lines, inputs.body)


def _describe_params(lines: list[str], params: list[OperationParameter]) -> None:
    for param in params:
        type_str = _schema_type(param.schema_)
        required = " (required)" if param.required else ""
        desc = f" — {param.description}" if param.description else ""
        lines.append(f"- `{param.name}` ({type_str}){required}{desc}")


def _describe_body(lines: list[str], body: RequestBodySchema) -> None:
    lines.append("## Request Body")
    lines.append("")
    if body.content_type:
        lines.append(f"**Content-Type:** `{body.content_type}`")
    lines.append(f"**Required:** {'yes' if body.required else 'no'}")
    if body.description:
        lines.append(f"> {body.description}")
    lines.append("")
    if body.schema_:
        lines.append("```json")
        lines.append(json.dumps(body.schema_, indent=2))
        lines.append("```")
        lines.append("")


def _schema_type(schema: dict[str, object] | None) -> str:
    if isinstance(schema, dict):
        return str(schema.get("type", "object"))
    return "unknown"
