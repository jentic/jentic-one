"""Project a stored ``raw_operation`` into structured, grouped inputs.

Spec import stores the operation's ``parameters`` and ``requestBody`` verbatim
in ``raw_operation`` (issue #768). This module turns that raw blob into the
``OperationInputs`` view — query / header / path parameters grouped by their
location plus a single request-body schema — so a client can build a complete
request rather than only supplying path parameters.

This is a pure projection over the stored operation dict. Any ``$ref`` inside a
parameter or request body cannot be resolved here (the surrounding components
document is not stored alongside the operation), so a ref-only parameter is
skipped and a ref-only body schema is passed through as-is.
"""

from __future__ import annotations

from typing import Any

from jentic_one.registry.services.inspect.models import (
    OperationInputs,
    OperationParameter,
    RequestBodySchema,
)

_PARAM_LOCATIONS = ("path", "query", "header")


def build_operation_inputs(raw_operation: dict[str, Any] | None) -> OperationInputs:
    """Build the grouped inputs view from a stored ``raw_operation`` dict."""
    if not isinstance(raw_operation, dict):
        return OperationInputs()

    grouped: dict[str, list[OperationParameter]] = {loc: [] for loc in _PARAM_LOCATIONS}
    for raw_param in raw_operation.get("parameters") or []:
        projected = _project_parameter(raw_param)
        if projected is None:
            continue
        location, param = projected
        grouped[location].append(param)

    body = _project_request_body(raw_operation.get("requestBody"))

    return OperationInputs(
        path=grouped["path"],
        query=grouped["query"],
        header=grouped["header"],
        body=body,
    )


def _project_parameter(raw_param: Any) -> tuple[str, OperationParameter] | None:
    """Project one OpenAPI parameter; return ``(location, param)`` or ``None``.

    Returns ``None`` for a malformed parameter, a location we don't group
    (e.g. ``cookie``), or a ``$ref``-only entry that can't be resolved without
    the components document.
    """
    if not isinstance(raw_param, dict):
        return None
    name = raw_param.get("name")
    location = raw_param.get("in")
    if not isinstance(name, str) or location not in _PARAM_LOCATIONS:
        return None
    schema = raw_param.get("schema")
    return location, OperationParameter(
        name=name,
        required=bool(raw_param.get("required", False)),
        description=raw_param.get("description"),
        schema_=schema if isinstance(schema, dict) else None,
    )


def _project_request_body(raw_body: Any) -> RequestBodySchema | None:
    """Project an OpenAPI ``requestBody`` to a single preferred-media schema.

    JSON media types are preferred; otherwise the first declared content entry
    is used. When no content is declared but a body block exists, its
    ``required``/``description`` still surface so a client knows a body is
    expected.
    """
    if not isinstance(raw_body, dict):
        return None

    content = raw_body.get("content")
    content_type: str | None = None
    schema: dict[str, Any] | None = None
    if isinstance(content, dict) and content:
        content_type = _select_media_type(content)
        media = content.get(content_type) if content_type else None
        if isinstance(media, dict):
            candidate = media.get("schema")
            schema = candidate if isinstance(candidate, dict) else None

    return RequestBodySchema(
        required=bool(raw_body.get("required", False)),
        description=raw_body.get("description"),
        content_type=content_type,
        schema_=schema,
    )


def _select_media_type(content: dict[str, Any]) -> str:
    """Pick the media type to project: prefer JSON, else the first declared."""
    for media_type in content:
        if isinstance(media_type, str) and "json" in media_type.lower():
            return media_type
    return next(iter(content))
