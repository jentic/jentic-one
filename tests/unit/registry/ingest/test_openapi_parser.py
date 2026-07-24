"""Unit tests for the OpenAPI operation parser."""

from typing import Any

from jentic_one.registry.core.url_index import reconcile_declared_path_params
from jentic_one.registry.ingest.parsers.openapi import OpenAPIOperationParser


def test_extracts_operations_from_minimal_spec() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "paths": {
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "summary": "List all pets",
                    "tags": ["pets"],
                },
                "post": {
                    "operationId": "createPet",
                    "summary": "Create a pet",
                    "tags": ["pets"],
                },
            },
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "summary": "Get a pet",
                    "description": "Get a single pet by ID",
                    "tags": ["pets"],
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert len(ops) == 3
    op_ids = {op["operation_id"] for op in ops}
    assert op_ids == {"listPets", "createPet", "getPet"}


def test_non_http_keys_in_path_items_are_skipped() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/items": {
                "parameters": [{"name": "limit", "in": "query"}],
                "summary": "Items endpoint",
                "get": {
                    "operationId": "listItems",
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert len(ops) == 1
    assert ops[0]["operation_id"] == "listItems"


def test_operation_servers_override_path_servers() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/data": {
                "servers": [{"url": "https://path-level.example.com"}],
                "get": {
                    "operationId": "getData",
                    "servers": [{"url": "https://op-level.example.com"}],
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert len(ops) == 1
    assert ops[0]["servers"] == [{"url": "https://op-level.example.com"}]


def test_path_servers_used_when_no_operation_servers() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/data": {
                "servers": [{"url": "https://path-level.example.com"}],
                "get": {
                    "operationId": "getData",
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert ops[0]["servers"] == [{"url": "https://path-level.example.com"}]


def test_missing_operation_id_yields_none() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/things": {
                "get": {
                    "summary": "Get things",
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert len(ops) == 1
    assert ops[0]["operation_id"] is None


def test_empty_paths_returns_empty_list() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {"paths": {}}
    ops = parser.extract_operations(spec)
    assert ops == []


def test_no_paths_key_returns_empty_list() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {"openapi": "3.1.0"}
    ops = parser.extract_operations(spec)
    assert ops == []


def test_method_is_uppercased() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/items": {
                "post": {"operationId": "create"},
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert ops[0]["method"] == "POST"


def test_openapi_parser_preserves_rfc6570_reserved_expansion_params() -> None:
    # Regression for #759: Google APIs (e.g. GA4 Data API) template path params
    # with the RFC 6570 reserved-expansion operator, `/v1beta/{+property}:runReport`,
    # while declaring the parameter plainly as `property`. The parser/reconciler must
    # match the declared `in: path` parameter to the `{+property}` token so the param
    # is retained rather than silently dropped (which makes the operation uncallable).
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "paths": {
            "/v1beta/{+property}:runReport": {
                "post": {
                    "operationId": "runReport",
                    "summary": "Run a GA4 report",
                    "parameters": [
                        {"in": "path", "name": "property", "required": True},
                    ],
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert len(ops) == 1
    op = ops[0]

    declared_path_params = [
        p["name"]
        for p in spec["paths"][op["path"]][op["method"].lower()]["parameters"]
        if p.get("in") == "path"
    ]
    reconciled = reconcile_declared_path_params(op["path"], declared_path_params)
    # The declared `property` parameter must be retained (matched to `{+property}`).
    assert "property" in reconciled


def test_empty_servers_when_none_defined() -> None:
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/items": {
                "get": {"operationId": "list"},
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert ops[0]["servers"] == []


def test_retains_parameters_and_request_body() -> None:
    # Regression for #768: header/query parameters and the request body must
    # survive import — without them every write / header-bearing operation is
    # uncallable because the stored operation declares no inputs.
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/v1/pages": {
                "post": {
                    "operationId": "createPage",
                    "parameters": [
                        {"name": "Notion-Version", "in": "header", "required": True},
                        {"name": "filter", "in": "query"},
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"parent": {}}}
                            }
                        },
                    },
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert len(ops) == 1
    op = ops[0]
    param_names = {p["name"] for p in op["parameters"]}
    assert param_names == {"Notion-Version", "filter"}
    assert op["requestBody"]["required"] is True
    assert op["requestBody"]["content"]["application/json"]["schema"]["type"] == "object"


def test_omits_parameters_and_body_keys_when_absent() -> None:
    # Operations with no declared inputs must not sprout empty keys — the
    # stored blob stays lean and the absence is unambiguous.
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/things": {
                "get": {"operationId": "listThings"},
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert "parameters" not in ops[0]
    assert "requestBody" not in ops[0]


def test_merges_path_level_parameters() -> None:
    # Path-item-level parameters apply to every operation on the path.
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/v1/pages/{page_id}": {
                "parameters": [
                    {"name": "page_id", "in": "path", "required": True},
                    {"name": "Notion-Version", "in": "header", "required": True},
                ],
                "get": {
                    "operationId": "getPage",
                    "parameters": [{"name": "filter", "in": "query"}],
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    assert len(ops) == 1
    param_index = {(p["name"], p["in"]) for p in ops[0]["parameters"]}
    assert param_index == {
        ("filter", "query"),
        ("page_id", "path"),
        ("Notion-Version", "header"),
    }


def test_operation_parameters_override_path_level_on_same_key() -> None:
    # When an operation redeclares a path-level parameter (same name + in),
    # the operation-level definition wins and there is no duplicate.
    parser = OpenAPIOperationParser()
    spec: dict[str, Any] = {
        "paths": {
            "/items": {
                "parameters": [{"name": "limit", "in": "query", "required": False}],
                "get": {
                    "operationId": "listItems",
                    "parameters": [{"name": "limit", "in": "query", "required": True}],
                },
            },
        },
    }
    ops = parser.extract_operations(spec)
    limit_params = [p for p in ops[0]["parameters"] if p["name"] == "limit"]
    assert len(limit_params) == 1
    assert limit_params[0]["required"] is True
