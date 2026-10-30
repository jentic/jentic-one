"""Enforce that every generated operation carries the spec metadata catalogue.

The control-plane spec is generated from the FastAPI app (see
``tools/openapi_export``). For that generated document to stay
reference-quality, every operation a new route adds must come with the metadata
the shared meta modules provide:

- ``operationId`` + ``summary`` + a non-empty ``tags`` (see
  ``jentic_one.shared.web.openapi_meta``), and
- the standard RFC 9457 error envelope (``400``/``500``/``503``) supplied by
  ``include_router(..., responses=COMMON_ERROR_RESPONSES)`` from
  ``jentic_one.shared.web.openapi_responses``.

These tests fail when a new or changed route ships without that metadata,
pointing the author at the meta modules. They
complement the drift test in ``test_openapi_conformance.py`` (which catches a
spec that wasn't regenerated). The spec is built once per session via the
``generated_control_spec`` fixture.
"""

from __future__ import annotations

import re
from typing import Any

import jsonschema
import pytest

from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, OIDC_PASSTHROUGH_SCOPES
from jentic_one.shared.web.openapi_meta import OPENAPI_TAGS

_HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}

# Health/liveness probes are dependency-free and intentionally bare; they do not
# carry the business error envelope.
_SYSTEM_TAG = "System"

# The error envelope every authenticated business operation must document.
_REQUIRED_ERROR_CODES = {"400", "500", "503"}

_GUIDE_HINT = (
    "See jentic_one.shared.web.openapi_meta / "
    "openapi_responses for how to attach this metadata, then run `make openapi`."
)


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (method, path, operation) for every operation in the spec."""
    ops: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() in _HTTP_METHODS and isinstance(operation, dict):
                ops.append((method.upper(), path, operation))
    return ops


def _resolve(spec: dict[str, Any], node: Any) -> Any:
    """Follow a local ``$ref`` to the object it names, keeping any sibling keys.

    The generator hoists repeated response and parameter objects into
    ``components`` (:mod:`jentic_one.shared.web.openapi_meta.dedupe`), so these
    checks resolve a reference before inspecting the object, exactly as a
    document consumer does. OpenAPI 3.1 lets ``$ref`` carry siblings, so a local
    override wins over the component it references.
    """
    for _ in range(10):
        if not (isinstance(node, dict) and isinstance(node.get("$ref"), str)):
            return node
        ref: str = node["$ref"]
        if not ref.startswith("#/"):
            return node
        target: Any = spec
        for part in ref[2:].split("/"):
            if not isinstance(target, dict):
                return node
            target = target.get(part.replace("~1", "/").replace("~0", "~"))
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        node = {**(target if isinstance(target, dict) else {}), **siblings}
    return node


def _parameters(spec: dict[str, Any], operation: dict[str, Any]) -> list[dict[str, Any]]:
    """The operation's parameters, each resolved through any ``$ref``."""
    return [_resolve(spec, param) for param in operation.get("parameters") or []]


@pytest.mark.arch
def test_every_operation_has_core_metadata(generated_control_spec: dict[str, Any]) -> None:
    """Every operation must have operationId, summary, and a non-empty tag."""
    violations: list[str] = []
    for method, path, op in _operations(generated_control_spec):
        gaps = [field for field in ("operationId", "summary", "tags") if not op.get(field)]
        if gaps:
            violations.append(f"{method} {path} -> missing: {', '.join(gaps)}")
    if violations:
        pytest.fail(
            "Generated operations are missing core metadata:\n"
            + "\n".join(violations)
            + f"\n\nFix: add summary= (or a docstring) and, for an unmapped path, a "
            f"_TAG_RULES entry in openapi_meta/operation_ids.py. {_GUIDE_HINT}"
        )


@pytest.mark.arch
def test_every_operation_tag_is_declared(generated_control_spec: dict[str, Any]) -> None:
    """Every tag used by an operation must be declared in OPENAPI_TAGS.

    Declaring the tag (with a description) is what gives it a Redoc description
    and a place in an ``x-tagGroups`` group — an undeclared tag means a new
    surface was wired up without documenting it.
    """
    declared = {tag["name"] for tag in OPENAPI_TAGS}
    violations: list[str] = []
    for method, path, op in _operations(generated_control_spec):
        for tag in op.get("tags") or []:
            if tag not in declared:
                violations.append(f"{method} {path} -> undeclared tag: {tag!r}")
    if violations:
        pytest.fail(
            "Operations reference tags not declared in OPENAPI_TAGS:\n"
            + "\n".join(violations)
            + f"\n\nFix: add the tag (with a description) to OPENAPI_TAGS and place it in an "
            f"X_TAG_GROUPS group in openapi_meta/tags.py. {_GUIDE_HINT}"
        )


@pytest.mark.arch
def test_business_operations_document_error_envelope(
    generated_control_spec: dict[str, Any],
) -> None:
    """Non-System operations must document the standard 400/500/503 error envelope.

    Routers inherit it via ``include_router(..., responses=COMMON_ERROR_RESPONSES)``.
    A business operation missing these codes signals a router included without
    the shared error catalogue.
    """
    violations: list[str] = []
    for method, path, op in _operations(generated_control_spec):
        if op.get("tags") == [_SYSTEM_TAG]:
            continue
        codes = set((op.get("responses") or {}).keys())
        missing = sorted(_REQUIRED_ERROR_CODES - codes)
        if missing:
            violations.append(f"{method} {path} -> missing responses: {', '.join(missing)}")
    if violations:
        pytest.fail(
            "Operations missing the standard error envelope:\n"
            + "\n".join(violations)
            + "\n\nFix: include the router with "
            "`responses=COMMON_ERROR_RESPONSES` (or merge via with_responses(...)) "
            f"from openapi_responses.py. {_GUIDE_HINT}"
        )


def _is_error_code(code: str) -> bool:
    return code.startswith(("4", "5"))


@pytest.mark.arch
def test_every_operation_has_description(generated_control_spec: dict[str, Any]) -> None:
    """Every operation must carry a non-empty ``description`` (the handler docstring)."""
    violations = [
        f"{method} {path}"
        for method, path, op in _operations(generated_control_spec)
        if not (op.get("description") or "").strip()
    ]
    if violations:
        pytest.fail(
            "Operations missing a description:\n"
            + "\n".join(violations)
            + f"\n\nFix: give the handler a docstring explaining what it does and returns. "
            f"{_GUIDE_HINT}"
        )


@pytest.mark.arch
def test_error_responses_document_a_body(generated_control_spec: dict[str, Any]) -> None:
    """Every 4xx/5xx response must declare content with a schema.

    A description-only error response tells a client nothing about the body it
    will parse. Problem Details routes get this from ``model=ProblemDetail``;
    other dialects use ``oauth_error()`` / ``route_not_found()`` from
    ``openapi_responses.py``.
    """
    violations: list[str] = []
    for method, path, op in _operations(generated_control_spec):
        for code, raw_response in (op.get("responses") or {}).items():
            if not _is_error_code(code):
                continue
            response = _resolve(generated_control_spec, raw_response)
            content = response.get("content") or {}
            if not content:
                violations.append(f"{method} {path} {code} -> no content")
                continue
            for media_type, media in content.items():
                if not (media or {}).get("schema"):
                    violations.append(f"{method} {path} {code} {media_type} -> no schema")
    if violations:
        pytest.fail(
            "Error responses without a documented body:\n"
            + "\n".join(violations)
            + "\n\nFix: declare model=ProblemDetail (or use problem()/oauth_error()/"
            f"route_not_found() from openapi_responses.py). {_GUIDE_HINT}"
        )


@pytest.mark.arch
def test_success_responses_declare_a_schema(generated_control_spec: dict[str, Any]) -> None:
    """Every 2xx body declares a schema, so a caller knows the shape it will parse.

    An empty ``schema: {}`` is what FastAPI emits when a route suppresses its
    response model (``response_model=None``) or returns a bare ``Response``, and
    it tells a caller nothing at all. Declare ``response_model=`` on the route,
    or — when the body genuinely has no fixed shape, such as a pass-through of an
    upstream payload — declare an explicit permissive schema with a description
    saying so. A response with no ``content`` at all (204, or a redirect) is
    fine: it promises no body.
    """
    violations: list[str] = []
    for method, path, operation in _operations(generated_control_spec):
        for code, raw_response in (operation.get("responses") or {}).items():
            if not code.startswith("2"):
                continue
            response = _resolve(generated_control_spec, raw_response)
            for media_type, raw_media in (response.get("content") or {}).items():
                media = _resolve(generated_control_spec, raw_media)
                if not media.get("schema"):
                    violations.append(f"{method} {path} {code} {media_type}")
    if violations:
        pytest.fail(
            "Success responses without a declared schema:\n"
            + "\n".join(violations)
            + "\n\nFix: set response_model= on the route, or declare an explicit schema "
            f"via responses={{...}} when the body has no fixed shape. {_GUIDE_HINT}"
        )


@pytest.mark.arch
def test_error_responses_are_not_html(generated_control_spec: dict[str, Any]) -> None:
    """Error bodies are JSON, even on HTML-page routes (``response_class=HTMLResponse``)."""
    violations = [
        f"{method} {path} {code}"
        for method, path, op in _operations(generated_control_spec)
        for code, response in (op.get("responses") or {}).items()
        if _is_error_code(code) and "text/html" in (response.get("content") or {})
    ]
    if violations:
        pytest.fail(
            "Error responses documented as text/html:\n"
            + "\n".join(violations)
            + "\n\nFix: normalise_error_responses in openapi_meta/responses.py should fold these "
            f"into a JSON media type. {_GUIDE_HINT}"
        )


@pytest.mark.arch
def test_component_schema_examples_validate(generated_control_spec: dict[str, Any]) -> None:
    """Every ``components.schemas.*.examples`` entry must validate against its schema.

    A common trap: FastAPI's OpenAPI encoder drops ``None`` values from
    ``json_schema_extra`` examples, so a required-but-nullable field set to
    ``None`` silently disappears and the example no longer validates. Use a
    non-null example value for those fields.
    """
    components = generated_control_spec.get("components") or {}
    schemas: dict[str, Any] = components.get("schemas") or {}
    violations: list[str] = []
    for name, schema in schemas.items():
        examples = list(schema.get("examples") or [])
        if "example" in schema:
            examples.append(schema["example"])
        if not examples:
            continue
        # Resolve ``#/components/schemas/...`` refs against the document itself.
        subject = {
            key: value for key, value in schema.items() if key not in ("examples", "example")
        }
        subject["components"] = {"schemas": schemas}
        validator = jsonschema.Draft202012Validator(subject)
        for index, example in enumerate(examples):
            for error in validator.iter_errors(example):
                location = "/".join(str(p) for p in error.absolute_path) or "<root>"
                violations.append(f"{name}.examples[{index}] at {location}: {error.message}")
    if violations:
        pytest.fail(
            "Component schema examples that fail their own schema:\n"
            + "\n".join(violations)
            + "\n\nFix: correct the model's json_schema_extra examples (and avoid None for "
            f"required-but-nullable fields). {_GUIDE_HINT}"
        )


@pytest.mark.arch
def test_every_parameter_has_a_valid_example(generated_control_spec: dict[str, Any]) -> None:
    """Every path/query/header parameter must carry an example that fits its schema.

    Declare it inline with ``openapi_examples=ex.example(...)`` on the
    ``Path``/``Query``/``Header`` marker, using the shared constants in
    ``jentic_one.shared.web.openapi_examples``. (``examples=[...]`` is not
    enough: FastAPI renders it inside the parameter's ``schema``, where
    parameter-example lint rules and doc renderers do not look.)
    """
    schemas = (generated_control_spec.get("components") or {}).get("schemas") or {}
    violations: list[str] = []
    for method, path, operation in _operations(generated_control_spec):
        for param in _parameters(generated_control_spec, operation):
            where = f"{method} {path} [{param.get('in')}] {param.get('name')}"
            values = [entry.get("value") for entry in (param.get("examples") or {}).values()]
            if "example" in param:
                values.append(param["example"])
            if not values:
                violations.append(f"{where}: no example")
                continue
            subject = dict(param.get("schema") or {})
            subject["components"] = {"schemas": schemas}
            validator = jsonschema.Draft202012Validator(subject)
            for value in values:
                for error in validator.iter_errors(value):
                    violations.append(f"{where}: example {value!r} invalid: {error.message}")
    if violations:
        pytest.fail(
            "Parameters without a valid example:\n"
            + "\n".join(violations)
            + "\n\nFix: add openapi_examples=ex.example(...) to the parameter, using "
            "jentic_one.shared.web.openapi_examples. A new canonical value is defined "
            "in jentic_one/shared/web/openapi_meta/examples.py (with a _VALUE_SUMMARIES "
            "label) and re-exported through openapi_examples. " + _GUIDE_HINT
        )


@pytest.mark.arch
def test_every_parameter_example_has_a_summary(generated_control_spec: dict[str, Any]) -> None:
    """Every parameter example carries a ``summary`` (scored by API linters).

    ``openapi_examples.example()`` labels the canonical constants and the
    generator labels the rest from the parameter, so this only fails if that
    post-processing is bypassed.
    """
    missing = [
        f"{method} {path} [{param.get('in')}] {param.get('name')}: {name}"
        for method, path, operation in _operations(generated_control_spec)
        for param in operation.get("parameters") or []
        for name, entry in (param.get("examples") or {}).items()
        if not entry.get("summary")
    ]
    assert not missing, "Parameter examples without a summary:\n" + "\n".join(missing)


@pytest.mark.arch
def test_every_path_item_has_a_summary(generated_control_spec: dict[str, Any]) -> None:
    """Every path item carries the path-level ``summary`` built from its operations."""
    missing = [
        path
        for path, item in (generated_control_spec.get("paths") or {}).items()
        if not item.get("summary")
    ]
    assert not missing, "Path items without a summary:\n" + "\n".join(missing)


def test_every_parameter_has_a_description(generated_control_spec: dict[str, Any]) -> None:
    """Every path/query/header parameter says what it is — set ``description=`` on the marker."""
    missing = [
        f"{method.upper()} {path} {param.get('in')}:{param.get('name')}"
        for path, method, operation in _operations(generated_control_spec)
        for param in _parameters(generated_control_spec, operation)
        if not param.get("description")
    ]
    assert not missing, "Parameters without a description:\n" + "\n".join(missing)


def test_every_path_item_and_request_body_has_a_description(
    generated_control_spec: dict[str, Any],
) -> None:
    """Path items and request bodies carry the descriptions the post-processor fills in."""
    missing = [
        path
        for path, item in (generated_control_spec.get("paths") or {}).items()
        if not item.get("description")
    ]
    missing += [
        f"{method.upper()} {path} requestBody"
        for path, method, operation in _operations(generated_control_spec)
        if "requestBody" in operation and not operation["requestBody"].get("description")
    ]
    assert not missing, "Missing descriptions:\n" + "\n".join(missing)


def test_every_operation_documents_a_default_problem_response(
    generated_control_spec: dict[str, Any],
) -> None:
    """Every operation's ``default`` response is the RFC 9457 catch-all."""
    bad = [
        f"{method.upper()} {path}"
        for path, method, operation in _operations(generated_control_spec)
        if _resolve(generated_control_spec, (operation.get("responses") or {}).get("default", {}))
        .get("content", {})
        .get("application/problem+json", {})
        .get("schema", {})
        .get("$ref")
        != "#/components/schemas/ProblemDetail"
    ]
    assert not bad, "Operations without a Problem Details default response:\n" + "\n".join(bad)


def test_oauth2_scheme_matches_the_authorization_server(
    generated_control_spec: dict[str, Any],
) -> None:
    """The ``OAuth2`` scheme's scopes are exactly the permission catalogue plus OIDC scopes.

    Scopes are permission names (``scopes_to_permissions`` is the identity), so a
    permission added to the catalogue must appear here — which it does by
    construction; this pins that nobody hard-codes a divergent list.
    """
    scheme = generated_control_spec["components"]["securitySchemes"]["OAuth2"]
    flow = scheme["flows"]["authorizationCode"]
    assert flow["authorizationUrl"] == "/authorize"
    assert flow["tokenUrl"] == "/oauth/token"
    assert set(flow["scopes"]) == set(ALL_PERMISSIONS) | OIDC_PASSTHROUGH_SCOPES
    assert all(desc for desc in flow["scopes"].values())


def test_authenticated_operations_accept_bearer_or_oauth2(
    generated_control_spec: dict[str, Any],
) -> None:
    """Every authenticated operation offers both schemes, with no per-operation scopes."""
    bad = [
        f"{method.upper()} {path}: {operation.get('security')}"
        for path, method, operation in _operations(generated_control_spec)
        if operation.get("security") not in ([], [{"BearerAuth": []}, {"OAuth2": []}])
    ]
    assert not bad, "Unexpected security requirements:\n" + "\n".join(bad)


def test_every_component_property_example_validates(
    generated_control_spec: dict[str, Any],
) -> None:
    """Each property's ``examples`` (hand-written or composed) validates against that property."""
    schemas: dict[str, Any] = (generated_control_spec.get("components") or {}).get("schemas") or {}
    violations: list[str] = []
    missing: list[str] = []
    for name, schema in schemas.items():
        if "properties" in schema and not (schema.get("examples") or "example" in schema):
            missing.append(name)
        for prop, prop_schema in (schema.get("properties") or {}).items():
            subject = {k: v for k, v in prop_schema.items() if k not in ("examples", "example")}
            subject["components"] = {"schemas": schemas}
            validator = jsonschema.Draft202012Validator(subject)
            for example in prop_schema.get("examples") or []:
                for error in validator.iter_errors(example):
                    violations.append(f"{name}.{prop}: {error.message}")
    assert not violations, "Property examples that fail their schema:\n" + "\n".join(violations)
    assert not missing, (
        "Object components without an example (give their leaf fields `examples=`):\n"
        + "\n".join(missing)
    )


def test_media_examples_validate_against_their_schema(
    generated_control_spec: dict[str, Any],
) -> None:
    """Every request/response media ``example`` (mirrored from its component) fits its schema."""
    schemas: dict[str, Any] = (generated_control_spec.get("components") or {}).get("schemas") or {}
    violations: list[str] = []
    for method, path, operation in _operations(generated_control_spec):
        bodies = {"requestBody": operation.get("requestBody") or {}}
        bodies.update(operation.get("responses") or {})
        for where, body in bodies.items():
            for media_type, media in (body.get("content") or {}).items():
                if "example" not in media:
                    continue
                subject = dict(media.get("schema") or {})
                subject["components"] = {"schemas": schemas}
                validator = jsonschema.Draft202012Validator(subject)
                for error in validator.iter_errors(media["example"]):
                    violations.append(f"{method} {path} {where} {media_type}: {error.message}")
    assert not violations, "Media examples that fail their schema:\n" + "\n".join(violations)


def test_parameter_schema_examples_match_the_parameter(
    generated_control_spec: dict[str, Any],
) -> None:
    """A parameter's ``schema.examples`` mirrors (never contradicts) its documented example."""
    mismatched: list[str] = []
    for method, path, operation in _operations(generated_control_spec):
        for param in operation.get("parameters") or []:
            documented = [entry.get("value") for entry in (param.get("examples") or {}).values()]
            schema_examples = (param.get("schema") or {}).get("examples")
            if documented and schema_examples and schema_examples[0] not in documented:
                mismatched.append(f"{method} {path} {param.get('name')}")
    assert not mismatched, "Parameter schema examples diverge from the parameter:\n" + "\n".join(
        mismatched
    )


def test_oneof_discriminator_mappings_match_variant_constants(
    generated_control_spec: dict[str, Any],
) -> None:
    """Each discriminator mapping entry points at a variant that pins that tag value."""
    schemas: dict[str, Any] = (generated_control_spec.get("components") or {}).get("schemas") or {}
    problems: list[str] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{where}/{index}")
            return
        if not isinstance(node, dict):
            return
        discriminator = node.get("discriminator")
        if isinstance(discriminator, dict) and "oneOf" in node:
            prop = discriminator["propertyName"]
            for tag, ref in (discriminator.get("mapping") or {}).items():
                variant = schemas.get(ref.rsplit("/", 1)[-1]) or {}
                spec = (variant.get("properties") or {}).get(prop) or {}
                pinned = spec.get("const", (spec.get("enum") or [None])[0])
                if pinned != tag:
                    problems.append(f"{where}: {tag!r} -> {ref} pins {pinned!r}")
        for key, value in node.items():
            walk(value, f"{where}/{key}")

    walk(generated_control_spec, "")
    assert not problems, "Discriminator mappings inconsistent with variants:\n" + "\n".join(
        problems
    )


#: Status codes an operation's prose names without the operation returning them.
#: Keyed by ``(operationId, code)`` with the reason, because
#: :func:`test_prose_status_codes_are_declared` cannot tell a real promise from a
#: number that happens to look like one. Add an entry only after checking the
#: handler: the default assumption is that prose naming a code is a promise, and
#: an undeclared promise is the defect the test exists to catch.
_PROSE_CODE_EXEMPTIONS: dict[tuple[str, str], str] = {
    # "at most 512 actions" — a size limit, not a status.
    ("submitOverlay", "512"): "overlay action-count limit, not a status code",
    # Describes the 429 its sibling `:connect` answers, not this operation.
    ("rejectConnectSession", "429"): "documents the rejection cooldown seen on :connect",
    # The 403/409 here are the Problem Details body's own `status` member for a
    # call that never ran; the HTTP status of this response stays 200.
    ("getJobResult", "409"): "problem document's own status member, HTTP stays 200",
    ("getJobResult", "403"): "problem document's own status member, HTTP stays 200",
}


@pytest.mark.arch
def test_prose_status_codes_are_declared(generated_control_spec: dict[str, Any]) -> None:
    """A status code an operation's prose promises is also declared in ``responses``.

    Telling a caller in English that an operation 404s, while declaring no 404,
    leaves it unable to generate error handling from the document — the fact is
    written down but not machine-readable. Declare the code with
    ``not_found()``/``conflict()``/``problem()`` from ``openapi_responses.py``,
    reusing the condition the prose already states and naming the problem `type`
    slug a caller branches on.

    A number that only looks like a status (a byte or item limit), or a code
    described as belonging to another operation or to a Problem Details body's
    own ``status`` member, belongs in :data:`_PROSE_CODE_EXEMPTIONS` with a
    reason — after checking the handler, not to silence the test.
    """
    violations: list[str] = []
    for method, path, operation in _operations(generated_control_spec):
        op_id = str(operation.get("operationId") or "")
        declared = set((operation.get("responses") or {}).keys())
        prose = " ".join(
            text
            for text in (
                operation.get("summary"),
                operation.get("description"),
                *(
                    _resolve(generated_control_spec, response).get("description")
                    for response in (operation.get("responses") or {}).values()
                ),
            )
            if isinstance(text, str)
        )
        for code in sorted(set(re.findall(r"\b([45]\d\d)\b", prose))):
            if code in declared or (op_id, code) in _PROSE_CODE_EXEMPTIONS:
                continue
            violations.append(
                f"{method} {path} ({op_id}) describes {code} but declares {sorted(declared)}"
            )
    if violations:
        pytest.fail(
            "Status codes promised in prose but never declared:\n"
            + "\n".join(violations)
            + "\n\nFix: declare the code on the route with the openapi_responses.py helpers, "
            "or exempt it in _PROSE_CODE_EXEMPTIONS with a reason if the handler never "
            f"returns it. {_GUIDE_HINT}"
        )


@pytest.mark.arch
def test_tag_catalogue_and_groups_agree(generated_control_spec: dict[str, Any]) -> None:
    """Every declared tag carries operations and sits in exactly one ``x-tagGroups`` group.

    Three ways this drifts, each of which misleads a reader navigating the
    document rather than breaking it:

    - a tag declared with a description but carrying no operations promises a
      surface that is not there;
    - a tag missing from ``x-tagGroups`` is invisible to a grouped renderer, so
      its operations cannot be found by browsing;
    - a group naming a tag that was never declared renders an empty heading.

    Fix all three in ``openapi_meta/tags.py`` (``OPENAPI_TAGS`` and
    ``X_TAG_GROUPS``), and the path to tag rules in ``openapi_meta/operation_ids.py``.
    """
    declared = {tag["name"] for tag in generated_control_spec.get("tags") or []}
    grouped = [
        name
        for group in generated_control_spec.get("x-tagGroups") or []
        for name in group.get("tags") or []
    ]
    used = {
        (operation.get("tags") or [None])[0]
        for _, _, operation in _operations(generated_control_spec)
    }

    problems: list[str] = []
    for name in sorted(declared - used):
        problems.append(f"tag {name!r} is declared but no operation carries it")
    for name in sorted(declared - set(grouped)):
        problems.append(f"tag {name!r} is declared but in no x-tagGroups group")
    for name in sorted(set(grouped) - declared):
        problems.append(f"x-tagGroups names {name!r}, which is not a declared tag")
    for name in sorted({n for n in grouped if grouped.count(n) > 1}):
        problems.append(f"tag {name!r} appears in more than one x-tagGroups group")

    assert not problems, (
        "Tag catalogue and groups disagree:\n"
        + "\n".join(f"  - {problem}" for problem in problems)
        + f"\n\n{_GUIDE_HINT}"
    )


@pytest.mark.arch
def test_tag_promised_status_codes_exist_somewhere_in_the_tag(
    generated_control_spec: dict[str, Any],
) -> None:
    """A status code a tag description promises is declared by some operation it covers.

    A tag description speaks for a whole group, so when the ``Executions`` tag
    says an id outside the caller's visibility answers 404, a caller expects to
    find a 404 somewhere under that tag. If no operation declares one, the tag is
    describing behaviour the document does not model anywhere.

    This deliberately does NOT require every operation in the tag to declare the
    code — a 410 for a deleted user belongs on the single-user reads, not on
    ``GET /users`` — so it asks only that the promise is realised somewhere.
    Per-operation accuracy is :func:`test_prose_status_codes_are_declared`'s job.
    """
    declared_by_tag: dict[str, set[str]] = {}
    for _, _, operation in _operations(generated_control_spec):
        codes = set((operation.get("responses") or {}).keys())
        for name in operation.get("tags") or []:
            declared_by_tag.setdefault(name, set()).update(codes)

    violations: list[str] = []
    for tag in generated_control_spec.get("tags") or []:
        name, description = tag.get("name"), tag.get("description") or ""
        if not name or name not in declared_by_tag:
            continue
        for code in sorted(set(re.findall(r"\b([45]\d\d)\b", description))):
            if code not in declared_by_tag[name]:
                violations.append(
                    f"tag {name!r} promises {code}, but no operation it covers declares it"
                )
    assert not violations, (
        "Tag descriptions promising status codes nothing declares:\n"
        + "\n".join(f"  - {violation}" for violation in violations)
        + "\n\nFix: declare the code on the operations that really return it, or stop "
        f"claiming it in the tag description. {_GUIDE_HINT}"
    )
