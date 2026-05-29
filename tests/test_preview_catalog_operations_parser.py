"""Unit tests for the OpenAPI → preview-operations projection.

The preview parser in `src/routers/catalog.py` is a pure function over
the parsed OpenAPI doc — exercised here in isolation from the spec-fetch
+ FastAPI plumbing so the merge / override / $ref-resolution rules are
covered with minimal surface area.

Why a dedicated file: F8 (clickable directory ops) added parameter
projection, path/op param merging, security flattening, and $ref
resolution to a path that was previously trivial. Each of those rules
has at least one subtle OpenAPI gotcha worth pinning down.
"""

from src.routers.catalog import (
    _flatten_security,
    _parse_preview_operations,
    _project_parameter,
    _resolve_local_ref,
    _slim_security_schemes,
)


# ── _resolve_local_ref ───────────────────────────────────────────────────────


def test_resolve_local_ref_dereferences_component():
    doc = {"components": {"parameters": {"Limit": {"name": "limit", "in": "query"}}}}
    assert _resolve_local_ref(doc, "#/components/parameters/Limit") == {
        "name": "limit",
        "in": "query",
    }


def test_resolve_local_ref_returns_none_for_non_local_refs():
    doc = {"components": {}}
    # External refs (different file) are not resolved — preview tolerates them.
    assert _resolve_local_ref(doc, "external.yaml#/components/parameters/Limit") is None
    assert _resolve_local_ref(doc, "https://example.com/spec.yaml") is None


def test_resolve_local_ref_handles_json_pointer_escapes():
    # JSON Pointer: `~1` → `/`, `~0` → `~`. Rare but real for path-style keys.
    doc = {"paths": {"/users/{id}": {"get": {}}}}
    assert _resolve_local_ref(doc, "#/paths/~1users~1{id}/get") == {}


def test_resolve_local_ref_returns_none_on_broken_pointer():
    assert _resolve_local_ref({"a": {}}, "#/a/missing") is None


# ── _project_parameter ───────────────────────────────────────────────────────


def test_project_parameter_slims_to_renderable_fields():
    p = {
        "name": "limit",
        "in": "query",
        "required": True,
        "description": "Page size",
        "schema": {"type": "integer"},  # dropped — preview doesn't render schemas
        "example": 50,  # dropped
    }
    assert _project_parameter({}, p) == {
        "name": "limit",
        "in": "query",
        "required": True,
        "description": "Page size",
    }


def test_project_parameter_resolves_local_ref():
    doc = {
        "components": {
            "parameters": {
                "Limit": {
                    "name": "limit",
                    "in": "query",
                    "required": False,
                    "description": "Max results",
                }
            }
        }
    }
    assert _project_parameter(doc, {"$ref": "#/components/parameters/Limit"}) == {
        "name": "limit",
        "in": "query",
        "required": False,
        "description": "Max results",
    }


def test_project_parameter_returns_none_on_unresolvable_ref():
    # The preview must keep going on weird specs rather than 500-ing.
    assert _project_parameter({}, {"$ref": "external.yaml#/X"}) is None
    assert _project_parameter({}, {"$ref": "#/components/parameters/Missing"}) is None


def test_project_parameter_returns_none_when_name_or_in_missing():
    assert _project_parameter({}, {"name": "limit"}) is None
    assert _project_parameter({}, {"in": "query"}) is None


# ── _flatten_security ────────────────────────────────────────────────────────


def test_flatten_security_dedupes_scheme_names_preserving_order():
    # OAS shape: [{schemeA: [scopes]}, {schemeB: [], schemeA: []}].
    # Each entry is an AND-conjunction; entries are OR-disjunctions. The
    # Sheet only needs the union of scheme names referenced anywhere.
    raw = [
        {"oauth2_read": ["read"]},
        {"apiKey": []},
        {"oauth2_read": ["write"]},  # duplicate scheme name across entries
    ]
    assert _flatten_security(raw) == ["oauth2_read", "apiKey"]


def test_flatten_security_handles_missing_and_malformed():
    assert _flatten_security(None) == []
    assert _flatten_security([]) == []
    assert _flatten_security([{}]) == []  # empty entry = "no auth" — yields nothing
    assert _flatten_security(["bogus", 42]) == []  # ignore non-dict entries


# ── _slim_security_schemes ───────────────────────────────────────────────────


def test_slim_security_schemes_projects_type_specific_fields():
    doc = {
        "components": {
            "securitySchemes": {
                "apiKey": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "Static API key",
                    "extra": "dropped",
                },
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                },
                "oauth": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://example.com/auth",
                            "tokenUrl": "https://example.com/token",
                            "scopes": {"read": "", "write": ""},
                        }
                    },
                },
                "oidc": {
                    "type": "openIdConnect",
                    "openIdConnectUrl": "https://example.com/.well-known/openid-config",
                },
            }
        }
    }
    slim = _slim_security_schemes(doc)
    assert slim["apiKey"] == {
        "type": "apiKey",
        "description": "Static API key",
        "in": "header",
        "name": "X-API-Key",
    }
    assert slim["bearer"] == {
        "type": "http",
        "description": "",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    # OAuth flows projected to a name list — the full flow objects are too
    # heavy for the Sheet header strip.
    assert slim["oauth"]["flows"] == ["authorizationCode"]
    assert slim["oidc"]["openIdConnectUrl"] == "https://example.com/.well-known/openid-config"


def test_slim_security_schemes_returns_empty_for_missing_components():
    assert _slim_security_schemes({}) == {}
    assert _slim_security_schemes({"components": {}}) == {}


# ── _parse_preview_operations ────────────────────────────────────────────────


def test_parse_preview_operations_basic_op_shape():
    doc = {
        "paths": {
            "/items": {
                "get": {
                    "summary": "List items",
                    "description": "Returns all items.",
                    "operationId": "listItems",
                }
            }
        }
    }
    ops = _parse_preview_operations(doc)
    assert ops == [
        {
            "method": "GET",
            "path": "/items",
            "summary": "List items",
            "description": "Returns all items.",
            "operation_id": "listItems",
            "parameters": [],
            "security": [],
            "tags": [],
        }
    ]


def test_parse_preview_operations_projects_tags():
    # Tags come straight off the op (slim projection — no expansion of doc-level
    # `tags` definitions, which are rendered separately in the Sheet header).
    doc = {
        "paths": {
            "/users": {
                "get": {"summary": "List users", "tags": ["Users", "Admin"]},
                "post": {"summary": "Create user"},  # no tags → []
            }
        }
    }
    by_method = {o["method"]: o["tags"] for o in _parse_preview_operations(doc)}
    assert by_method == {"GET": ["Users", "Admin"], "POST": []}


def test_parse_preview_operations_filters_by_tag_case_insensitive():
    # Tag filter is a case-insensitive substring match — same semantics as
    # the BM25 search filter, so the UI doesn't have to teach two rules.
    doc = {
        "paths": {
            "/users": {"get": {"summary": "List users", "tags": ["Users"]}},
            "/posts": {"get": {"summary": "List posts", "tags": ["Posts"]}},
            "/admin/users": {"get": {"summary": "Admin users", "tags": ["UserAdmin"]}},
        }
    }
    paths = sorted(o["path"] for o in _parse_preview_operations(doc, tag="user"))
    assert paths == ["/admin/users", "/users"]
    # Untagged ops are filtered out when a tag is requested.
    doc2 = {"paths": {"/x": {"get": {"summary": "no tags"}}}}
    assert _parse_preview_operations(doc2, tag="users") == []


def test_parse_preview_operations_merges_path_and_op_level_parameters():
    # Path-level params apply to every method on the path; op-level may
    # add OR override on the (name, in) key per OAS rules.
    doc = {
        "paths": {
            "/users/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "description": "User id"},
                    {"name": "fields", "in": "query", "description": "from path-level"},
                ],
                "get": {
                    "summary": "Get user",
                    "parameters": [
                        # Overrides the path-level `fields` description.
                        {"name": "fields", "in": "query", "description": "from op-level"},
                        # Brand new op-level param.
                        {"name": "include_deleted", "in": "query", "required": False},
                    ],
                },
            }
        }
    }
    ops = _parse_preview_operations(doc)
    params = ops[0]["parameters"]
    # Op-level params come first (override winner), then non-overridden
    # path-level params. Order matters — assertion is exact.
    assert params == [
        {
            "name": "fields",
            "in": "query",
            "required": False,
            "description": "from op-level",
        },
        {
            "name": "include_deleted",
            "in": "query",
            "required": False,
            "description": "",
        },
        {
            "name": "id",
            "in": "path",
            "required": True,
            "description": "User id",
        },
    ]


def test_parse_preview_operations_op_security_overrides_doc_security():
    # Doc-level security applies to ops that don't define their own. An
    # empty `[]` on an op means "no auth required" (explicit override),
    # NOT "fall back to doc". Both shapes are exercised here.
    doc = {
        "security": [{"apiKey": []}],
        "paths": {
            "/inherits": {"get": {"summary": "Inherits doc-level auth"}},
            "/overrides": {
                "post": {
                    "summary": "Uses different scheme",
                    "security": [{"oauth2": ["write"]}],
                }
            },
            "/public": {
                "get": {
                    "summary": "Explicitly no auth",
                    "security": [],  # OAS shorthand for "this endpoint is public"
                }
            },
        },
    }
    by_path = {(o["method"], o["path"]): o["security"] for o in _parse_preview_operations(doc)}
    assert by_path[("GET", "/inherits")] == ["apiKey"]
    assert by_path[("POST", "/overrides")] == ["oauth2"]
    # Empty list is preserved — it's a meaningful signal (public endpoint)
    # and must NOT silently fall back to doc-level security.
    assert by_path[("GET", "/public")] == []


def test_parse_preview_operations_skips_non_http_keys():
    # OAS allows `parameters`, `summary`, `description`, `servers` etc. as
    # siblings of HTTP methods on a path item. They must not become ops.
    doc = {
        "paths": {
            "/items": {
                "summary": "Items resource",
                "description": "—",
                "parameters": [],
                "servers": [],
                "get": {"summary": "Real op"},
            }
        }
    }
    ops = _parse_preview_operations(doc)
    assert [(o["method"], o["path"]) for o in ops] == [("GET", "/items")]
