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

from typing import Any

import jsonschema
import pytest

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
            f"_TAG_RULES entry in openapi_meta.py. {_GUIDE_HINT}"
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
            f"X_TAG_GROUPS group in openapi_meta.py. {_GUIDE_HINT}"
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
        for code, response in (op.get("responses") or {}).items():
            if not _is_error_code(code):
                continue
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
            + "\n\nFix: _normalise_error_responses in openapi_meta.py should fold these "
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
