"""Render an OperationInspectResult as an OpenAPI YAML fragment."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import yaml

from jentic_one.registry.services.inspect.models import (
    OperationInputs,
    OperationInspectResult,
)


def render_openapi_yaml(result: OperationInspectResult) -> str:
    """Render a minimal OpenAPI 3.1 YAML document for the inspected operation."""
    path_key = _extract_path(result.url, result.server)
    operation: dict[str, Any] = {}
    if result.name:
        operation["summary"] = result.name
    if result.description:
        operation["description"] = result.description
    operation["operationId"] = result.operation_id

    if result.inputs:
        params = _render_parameters(result.inputs)
        if params:
            operation["parameters"] = params
        request_body = _render_request_body(result.inputs)
        if request_body is not None:
            operation["requestBody"] = request_body

    if result.response_schema:
        operation["responses"] = {
            "200": {
                "description": "Successful response",
                "content": {"application/json": {"schema": result.response_schema}},
            }
        }

    if result.auth:
        security: list[dict[str, list[str]]] = []
        for auth in result.auth:
            security.append({auth.type: []})
        operation["security"] = security

    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": f"{result.api.vendor}/{result.api.name}",
            "version": result.api.version,
        },
        "paths": {path_key: {result.method.lower(): operation}},
    }

    if result.server:
        spec["servers"] = [{"url": result.server}]

    return yaml.dump(spec, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _render_parameters(inputs: OperationInputs) -> list[dict[str, Any]]:
    """Emit OpenAPI parameter objects with their true ``in`` location."""
    params: list[dict[str, Any]] = []
    for location, entries in (
        ("path", inputs.path),
        ("query", inputs.query),
        ("header", inputs.header),
    ):
        for param in entries:
            entry: dict[str, Any] = {"name": param.name, "in": location}
            if param.required:
                entry["required"] = True
            if param.description:
                entry["description"] = param.description
            if param.schema_ is not None:
                entry["schema"] = param.schema_
            params.append(entry)
    return params


def _render_request_body(inputs: OperationInputs) -> dict[str, Any] | None:
    """Emit an OpenAPI requestBody object from the projected body schema."""
    body = inputs.body
    if body is None:
        return None
    request_body: dict[str, Any] = {}
    if body.required:
        request_body["required"] = True
    if body.description:
        request_body["description"] = body.description
    if body.schema_ is not None:
        media_type = body.content_type or "application/json"
        request_body["content"] = {media_type: {"schema": body.schema_}}
    return request_body or None


def _extract_path(url: str, server: str | None) -> str:
    if server and url.startswith(server):
        path = url[len(server) :]
        return path if path.startswith("/") else "/" + path
    parsed = urlparse(url)
    return parsed.path or "/"
