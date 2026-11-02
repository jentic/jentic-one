"""Hoist repeated response and parameter objects into reusable components.

FastAPI inlines the full body of every response and parameter into each
operation, so the shared RFC 9457 error envelope is written out once per
operation rather than once per document. The passes here run last, over the
finished document, and replace each byte-identical repeat with a ``$ref`` into
``components.responses`` / ``components.parameters``.

The transform is information-preserving: only objects that serialise identically
are collapsed, and the hoisted component is the object itself. OpenAPI 3.1
follows JSON Schema 2020-12, where ``$ref`` may carry sibling keywords, so an
operation that needs its own wording keeps a local ``description`` beside the
``$ref``.

:func:`strip_redundant_titles` drops the ``title`` Pydantic derives from a field
name, which restates the property key it sits on.
"""

from __future__ import annotations

import json
from typing import Any

#: Collapse an object only once it appears at least this many times. Below it the
#: ``$ref`` indirection costs a reader more than the repetition does.
_MIN_OCCURRENCES = 4

#: Status codes whose shared response component gets a readable name.
_STATUS_NAMES = {
    "400": "BadRequest",
    "401": "Unauthorized",
    "403": "Forbidden",
    "404": "NotFound",
    "409": "Conflict",
    "410": "Gone",
    "412": "PreconditionFailed",
    "422": "UnprocessableEntity",
    "429": "TooManyRequests",
    "500": "InternalServerError",
    "503": "ServiceUnavailable",
    "default": "UnexpectedError",
}

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def _fingerprint(obj: Any) -> str:
    """A stable key identifying an object by content, independent of key order."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _operations(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every operation object in the document."""
    out: list[dict[str, Any]] = []
    for path_item in (schema.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() in _HTTP_METHODS and isinstance(operation, dict):
                out.append(operation)
    return out


def _unique_name(base: str, taken: set[str]) -> str:
    """``base``, suffixed to the first spelling not already in ``taken``."""
    if base not in taken:
        taken.add(base)
        return base
    for n in range(2, 1000):
        candidate = f"{base}{n}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise RuntimeError(f"cannot find a free component name for {base!r}")


def extract_shared_responses(schema: dict[str, Any]) -> int:
    """Hoist repeated response objects into ``components.responses``.

    Returns the number of inline response objects replaced by a ``$ref``.
    """
    counts: dict[tuple[str, str], int] = {}
    for operation in _operations(schema):
        for code, response in (operation.get("responses") or {}).items():
            if isinstance(response, dict) and "$ref" not in response:
                counts[(code, _fingerprint(response))] = (
                    counts.get((code, _fingerprint(response)), 0) + 1
                )

    # Deterministic: most frequent first, then by content, so names are stable
    # across runs and the diff of a regenerated spec stays reviewable.
    shared = sorted(
        (key for key, n in counts.items() if n >= _MIN_OCCURRENCES),
        key=lambda key: (-counts[key], key[0], key[1]),
    )
    if not shared:
        return 0

    components = schema.setdefault("components", {})
    responses_component: dict[str, Any] = components.setdefault("responses", {})
    taken = set(responses_component)
    names: dict[tuple[str, str], str] = {}
    for code, fingerprint in shared:
        base = _STATUS_NAMES.get(code, f"Status{code}")
        names[(code, fingerprint)] = _unique_name(base, taken)

    replaced = 0
    for operation in _operations(schema):
        responses = operation.get("responses") or {}
        for code, response in list(responses.items()):
            if not isinstance(response, dict) or "$ref" in response:
                continue
            name = names.get((code, _fingerprint(response)))
            if name is None:
                continue
            responses_component.setdefault(name, response)
            responses[code] = {"$ref": f"#/components/responses/{name}"}
            replaced += 1
    return replaced


def extract_shared_parameters(schema: dict[str, Any]) -> int:
    """Hoist repeated parameter objects into ``components.parameters``.

    Returns the number of inline parameter objects replaced by a ``$ref``.
    """
    counts: dict[str, int] = {}
    meta: dict[str, dict[str, Any]] = {}
    for operation in _operations(schema):
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict) or "$ref" in parameter:
                continue
            fingerprint = _fingerprint(parameter)
            counts[fingerprint] = counts.get(fingerprint, 0) + 1
            meta[fingerprint] = parameter

    shared = sorted(
        (f for f, n in counts.items() if n >= _MIN_OCCURRENCES),
        key=lambda f: (-counts[f], f),
    )
    if not shared:
        return 0

    def component_name(fingerprint: str) -> str:
        parameter = meta[fingerprint]
        location = str(parameter.get("in") or "param").capitalize()
        raw = str(parameter.get("name") or "value")
        label = "".join(part.capitalize() for part in raw.replace("-", "_").split("_") if part)
        return f"{location}{label}"

    # Two parameters can share a name and location while documenting different
    # things (``id`` on an OAuth client and on an app registration). Naming those
    # apart means a meaningless numeric suffix, which client generators surface as
    # a type name, so they stay inline and only unambiguous parameters are hoisted.
    name_counts: dict[str, int] = {}
    for fingerprint in shared:
        base = component_name(fingerprint)
        name_counts[base] = name_counts.get(base, 0) + 1

    components = schema.setdefault("components", {})
    parameters_component: dict[str, Any] = components.setdefault("parameters", {})
    taken = set(parameters_component)
    names: dict[str, str] = {}
    for fingerprint in shared:
        base = component_name(fingerprint)
        if name_counts[base] > 1 or base in taken:
            continue
        names[fingerprint] = _unique_name(base, taken)

    replaced = 0
    for operation in _operations(schema):
        parameters = operation.get("parameters")
        if not isinstance(parameters, list):
            continue
        for index, parameter in enumerate(parameters):
            if not isinstance(parameter, dict) or "$ref" in parameter:
                continue
            name = names.get(_fingerprint(parameter))
            if name is None:
                continue
            parameters_component.setdefault(name, parameter)
            parameters[index] = {"$ref": f"#/components/parameters/{name}"}
            replaced += 1
    return replaced


def _derived_title(key: str) -> str:
    """The ``title`` Pydantic derives from a field name: ``agent_id`` -> ``Agent Id``."""
    return key.replace("_", " ").title()


def strip_redundant_titles(schema: dict[str, Any]) -> int:
    """Drop every ``title`` that only restates the key or schema name it sits on.

    Pydantic titles each field from its name, so a reader learns nothing from
    ``title: Name`` on a ``name`` property. A title that says more than the key
    is kept. Returns the number of titles removed.

    Client generators name their types from the component key rather than from
    ``title``, so the generated Go and TypeScript clients are unaffected.
    """
    removed = 0

    def walk(node: Any, key_name: str | None) -> None:
        nonlocal removed
        if isinstance(node, list):
            for item in node:
                walk(item, None)
            return
        if not isinstance(node, dict):
            return
        title = node.get("title")
        if isinstance(title, str) and key_name is not None and title == _derived_title(key_name):
            del node["title"]
            removed += 1
        for child_key, child in node.items():
            if child_key in ("properties", "$defs", "definitions") and isinstance(child, dict):
                for property_name, property_schema in child.items():
                    walk(property_schema, property_name)
            else:
                walk(child, None)

    for name, component in (schema.get("components", {}).get("schemas") or {}).items():
        if isinstance(component, dict) and component.get("title") == name:
            del component["title"]
            removed += 1
        walk(component, None)
    walk(schema.get("paths"), None)
    return removed


def collapse_duplicate_components(schema: dict[str, Any]) -> dict[str, int]:
    """Run every dedupe pass over a finished document, reporting what each removed."""
    return {
        "responses": extract_shared_responses(schema),
        "parameters": extract_shared_parameters(schema),
        "titles": strip_redundant_titles(schema),
    }
