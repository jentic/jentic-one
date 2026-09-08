"""Unit tests for inspect output formatters."""

from __future__ import annotations

import yaml

from jentic_one.registry.services.inspect.formatters.markdown import render_markdown
from jentic_one.registry.services.inspect.formatters.openapi import render_openapi_yaml
from jentic_one.registry.services.inspect.models import (
    ApiContext,
    AuthInstruction,
    InspectLinks,
    OperationInputs,
    OperationInspectResult,
    OperationParameter,
    RequestBodySchema,
)


def _make_result(
    *,
    server: str | None = "https://api.example.com",
    auth: list[AuthInstruction] | None = None,
    inputs: OperationInputs | None = None,
    response_schema: dict[str, object] | None = None,
) -> OperationInspectResult:
    return OperationInspectResult(
        operation_id="op_abc",
        method="GET",
        url="https://api.example.com/v1/pets",
        name="List pets",
        description="Returns all pets",
        api=ApiContext(
            vendor="acme",
            name="Pets API",
            version="v1",
            description="A pet management API",
        ),
        inputs=inputs,
        response_schema=response_schema,
        auth=auth,
        server=server,
        links=InspectLinks(self_link="/inspect?id=GET%20https%3A%2F%2Fapi.example.com%2Fv1%2Fpets"),
    )


def test_render_markdown_includes_title() -> None:
    result = _make_result()
    md = render_markdown(result)
    assert "# List pets" in md


def test_render_markdown_includes_method_and_url() -> None:
    result = _make_result()
    md = render_markdown(result)
    assert "`GET`" in md
    assert "`https://api.example.com/v1/pets`" in md


def test_render_markdown_includes_server() -> None:
    result = _make_result(server="https://api.example.com")
    md = render_markdown(result)
    assert "https://api.example.com" in md


def test_render_markdown_includes_api_context() -> None:
    result = _make_result()
    md = render_markdown(result)
    assert "acme/Pets API v1" in md


def test_render_markdown_includes_description() -> None:
    result = _make_result()
    md = render_markdown(result)
    assert "Returns all pets" in md


def test_render_markdown_includes_parameters() -> None:
    result = _make_result(
        inputs=OperationInputs(
            query=[
                OperationParameter(
                    name="limit",
                    schema_={"type": "integer", "description": "Max items"},
                    description="Max items",
                )
            ]
        )
    )
    md = render_markdown(result)
    assert "`limit`" in md
    assert "integer" in md
    assert "Query Parameters" in md


def test_render_markdown_groups_parameters_by_location() -> None:
    result = _make_result(
        inputs=OperationInputs(
            path=[OperationParameter(name="page_id", required=True)],
            query=[OperationParameter(name="page_size", schema_={"type": "integer"})],
            header=[OperationParameter(name="Notion-Version", required=True)],
        )
    )
    md = render_markdown(result)
    assert "Path Parameters" in md
    assert "Query Parameters" in md
    assert "Header Parameters" in md
    assert "`page_id`" in md
    assert "`Notion-Version`" in md
    assert "(required)" in md


def test_render_markdown_includes_request_body() -> None:
    result = _make_result(
        inputs=OperationInputs(
            body=RequestBodySchema(
                required=True,
                content_type="application/json",
                schema_={"type": "object", "properties": {"parent": {"type": "object"}}},
            )
        )
    )
    md = render_markdown(result)
    assert "Request Body" in md
    assert "application/json" in md
    assert '"parent"' in md


def test_render_markdown_includes_response_schema() -> None:
    result = _make_result(response_schema={"type": "array", "items": {"type": "object"}})
    md = render_markdown(result)
    assert "Response Schema" in md
    assert '"type": "array"' in md


def test_render_markdown_includes_auth() -> None:
    result = _make_result(
        auth=[
            AuthInstruction(type="http", scheme="bearer", bearer_format="JWT"),
            AuthInstruction(type="apiKey", in_location="header", param_name="X-API-Key"),
        ]
    )
    md = render_markdown(result)
    assert "Authentication" in md
    assert "bearer" in md
    assert "X-API-Key" in md


def test_render_markdown_no_server_omits_server_line() -> None:
    result = _make_result(server=None)
    md = render_markdown(result)
    assert "**Server:**" not in md


def test_render_openapi_yaml_produces_valid_yaml() -> None:
    result = _make_result()
    output = render_openapi_yaml(result)
    parsed = yaml.safe_load(output)
    assert parsed["openapi"] == "3.1.0"


def test_render_openapi_yaml_includes_path_and_method() -> None:
    result = _make_result()
    output = render_openapi_yaml(result)
    parsed = yaml.safe_load(output)
    assert "/v1/pets" in parsed["paths"]
    assert "get" in parsed["paths"]["/v1/pets"]


def test_render_openapi_yaml_includes_server() -> None:
    result = _make_result(server="https://api.example.com")
    output = render_openapi_yaml(result)
    parsed = yaml.safe_load(output)
    assert parsed["servers"][0]["url"] == "https://api.example.com"


def test_render_openapi_yaml_includes_operation_id() -> None:
    result = _make_result()
    output = render_openapi_yaml(result)
    parsed = yaml.safe_load(output)
    op = parsed["paths"]["/v1/pets"]["get"]
    assert op["operationId"] == "op_abc"


def test_render_openapi_yaml_no_server_omits_servers() -> None:
    result = _make_result(server=None)
    output = render_openapi_yaml(result)
    parsed = yaml.safe_load(output)
    assert "servers" not in parsed


def test_render_openapi_yaml_emits_parameters_with_correct_location() -> None:
    result = _make_result(
        inputs=OperationInputs(
            path=[OperationParameter(name="page_id", required=True)],
            query=[OperationParameter(name="page_size", schema_={"type": "integer"})],
            header=[OperationParameter(name="Notion-Version", required=True)],
        )
    )
    output = render_openapi_yaml(result)
    parsed = yaml.safe_load(output)
    params = parsed["paths"]["/v1/pets"]["get"]["parameters"]
    by_name = {p["name"]: p for p in params}
    assert by_name["page_id"]["in"] == "path"
    assert by_name["page_size"]["in"] == "query"
    assert by_name["page_size"]["schema"] == {"type": "integer"}
    assert by_name["Notion-Version"]["in"] == "header"
    assert by_name["Notion-Version"]["required"] is True


def test_render_openapi_yaml_emits_request_body() -> None:
    result = _make_result(
        inputs=OperationInputs(
            body=RequestBodySchema(
                required=True,
                content_type="application/json",
                schema_={"type": "object"},
            )
        )
    )
    output = render_openapi_yaml(result)
    parsed = yaml.safe_load(output)
    request_body = parsed["paths"]["/v1/pets"]["get"]["requestBody"]
    assert request_body["required"] is True
    assert request_body["content"]["application/json"]["schema"] == {"type": "object"}
