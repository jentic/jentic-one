"""Offline generator for the human-readable configuration reference.

Renders ``docs/reference/config.md`` — every configuration key, its type,
default, and ``JENTIC__*`` environment variable — from the same
``build_config_schema()`` code path that produces ``config/config-schema.json``
(see ``tools/config_schema_export``). The Pydantic ``AppConfig`` model stays the
single source of truth: nested sections, field names, types, defaults, and
descriptions are all introspected, never hand-written.

This module is the single code path shared by:

- ``make config-reference`` (regenerate the checked-in document), and
- the drift test in ``tests/arch/test_config_reference_conformance.py``

so a config-model change that isn't accompanied by ``make config-reference``
is a red build — the documentation twin of the config-schema drift gate.

Run directly::

    uv run python -m tools.config_reference            # writes docs/reference/config.md
    uv run python -m tools.config_reference --stdout   # print to stdout
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from tools.config_schema_export import REPO_ROOT, build_config_schema

CONFIG_REFERENCE_PATH = REPO_ROOT / "docs" / "reference" / "config.md"

# Path segment placeholders for container children. ``<name>`` marks a
# user-chosen map key (``dict[str, …]`` fields); ``<n>`` marks a list index.
# Both survive into the env-var column (``JENTIC__…__<NAME>__…``), matching the
# loader's generic nesting: any env segment becomes a dict key, and digit
# segments are coerced back into list indices (see ``_env_overrides`` /
# ``_coerce_indexed_dicts_to_lists`` in ``jentic_one.shared.config``).
_MAP_KEY = "<name>"
_LIST_INDEX = "<n>"

_HEADER = """\
# Configuration reference

<!-- GENERATED FILE — do not hand-edit. Regenerate with `make config-reference`
     (tools/config_reference.py). Drift-gated by
     tests/arch/test_config_reference_conformance.py. -->

Every configuration key jentic-one reads, generated from the
[`AppConfig`](../../src/jentic_one/shared/config.py) Pydantic model — the same
source of truth behind [`config/config-schema.json`](../../config/config-schema.json)
and the `jenticctl install` wizard. How the config system works (loading,
`Context`, hot reload) is covered in
[Context and configuration](../development/context-and-config.md).

## How a value is resolved

Highest wins:

1. Environment variable (`JENTIC__SECTION__KEY`)
2. YAML config file (`jentic-one.yaml` in the working directory, or the file
   named by `JENTIC_CONFIG_FILE` — a loader-level variable, not a config key)
3. The field default listed below

Env-var naming: uppercase the YAML path and join nesting levels with `__`
(single underscores within a key are preserved). A `<name>` segment is a
map key you choose; a `<n>` segment is a zero-based list index
(`JENTIC__AUTH__ID_SIGNING__0__KID`). List- and model-valued fields are
usually easier to express in YAML.

A default of *required* means the app refuses to start without a value; `—`
means the default is empty or built at runtime (an empty list/map).
The open `extensions` section (enterprise overlay sub-configs registered at
import time) is machine state, not an operator knob, and is excluded here just
as it is from the JSON schema.
"""


def _clean_text(text: str) -> str:
    """Normalise a schema description for a markdown table cell."""
    text = text.replace("``", "`")
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "\\|")


def _resolve_ref(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Follow a ``$ref`` into ``$defs`` (returns ``node`` unchanged otherwise)."""
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    name = ref.rsplit("/", 1)[-1]
    resolved = defs.get(name)
    if not isinstance(resolved, dict):
        raise ValueError(f"Unresolvable $ref in config schema: {ref}")
    return resolved


def _constraints(node: dict[str, Any]) -> str:
    """Render numeric/length bounds as a compact suffix, e.g. ``(>= 0)``."""
    parts: list[str] = []
    for key, symbol in (
        ("minimum", ">="),
        ("exclusiveMinimum", ">"),
        ("maximum", "<="),
        ("exclusiveMaximum", "<"),
    ):
        if key in node:
            parts.append(f"{symbol} {node[key]}")
    if "minLength" in node:
        parts.append(f"min length {node['minLength']}")
    if "maxLength" in node:
        parts.append(f"max length {node['maxLength']}")
    return f" ({', '.join(parts)})" if parts else ""


def _render_type(node: dict[str, Any], defs: dict[str, Any]) -> str:
    """Human-readable type for a schema node (refs resolved, unions joined)."""
    node = _resolve_ref(node, defs)
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        return " \\| ".join(json.dumps(v) for v in node["enum"])
    if "anyOf" in node or "oneOf" in node:
        variants = node.get("anyOf") or node.get("oneOf") or []
        return " \\| ".join(_render_type(v, defs) for v in variants)
    schema_type = node.get("type")
    if schema_type == "array":
        items = node.get("items")
        inner = _render_type(items, defs) if isinstance(items, dict) else "any"
        return f"list of {inner}"
    if schema_type == "object":
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            return f"map of {_render_type(extra, defs)}"
        if isinstance(extra, list):  # union-valued map (e.g. credential providers)
            inner = " \\| ".join(_render_type(v, defs) for v in extra)
            return f"map of {inner}"
        if "properties" in node:
            return str(node.get("title", "object"))
        return "object"
    if schema_type == "string" and node.get("format") == "password":
        return "string (secret)" + _constraints(node)
    if schema_type is None:
        return "any"
    return str(schema_type) + _constraints(node)


def _render_default(node: dict[str, Any], required: bool) -> str:
    if "default" in node:
        rendered = json.dumps(node["default"], ensure_ascii=False)
        if len(rendered) > 60:
            rendered = rendered[:57] + "…"
        return f"`{rendered}`".replace("|", "\\|")
    if "const" in node:
        return f"`{json.dumps(node['const'])}`"
    return "*required*" if required else "—"


def _env_var(path: list[str]) -> str:
    """The ``JENTIC__*`` env var addressing this path."""
    segments = []
    for seg in path:
        if seg == _MAP_KEY:
            segments.append("<NAME>")
        elif seg == _LIST_INDEX:
            segments.append("<N>")
        else:
            segments.append(seg.upper())
    return "JENTIC__" + "__".join(segments)


class _Row:
    __slots__ = ("default", "description", "path", "types")

    def __init__(self, path: list[str], type_str: str, default: str, description: str) -> None:
        self.path = path
        self.types = [type_str]
        self.default = default
        self.description = description


def _model_variants(node: dict[str, Any], defs: dict[str, Any]) -> list[dict[str, Any]]:
    """Object-model schema(s) reachable from ``node`` without crossing a container.

    Returns one entry per union variant (discriminated unions such as the
    credential providers map yield several); an empty list means ``node`` is a
    leaf (scalar, enum, or a container handled by the caller).
    """
    node = _resolve_ref(node, defs)
    if "properties" in node:
        return [node]
    variants = node.get("anyOf") or node.get("oneOf") or []
    models = []
    for variant in variants:
        if isinstance(variant, dict):
            resolved = _resolve_ref(variant, defs)
            if "properties" in resolved:
                models.append(resolved)
    return models


def _walk(
    path: list[str],
    node: dict[str, Any],
    defs: dict[str, Any],
    required: bool,
    rows: dict[str, _Row],
) -> None:
    """Emit table rows for the field at ``path``, recursing into sub-models."""
    field_desc = node.get("description", "")
    resolved = _resolve_ref(node, defs)
    description = field_desc or resolved.get("description", "")

    models = _model_variants(node, defs)
    if models:
        for model in models:
            model_required = set(model.get("required", []))
            for name, child in model.get("properties", {}).items():
                _walk([*path, name], child, defs, name in model_required, rows)
        return

    def add_row(row_path: list[str], type_str: str, default: str, desc: str) -> None:
        key = ".".join(row_path)
        existing = rows.get(key)
        if existing is not None:
            if type_str not in existing.types:
                existing.types.append(type_str)
            return
        rows[key] = _Row(row_path, type_str, default, desc)

    schema_type = resolved.get("type")
    container_row = (
        path,
        _render_type(resolved, defs),
        _render_default(node, required),
        description,
    )

    if schema_type == "array":
        items = resolved.get("items")
        item_models = _model_variants(items, defs) if isinstance(items, dict) else []
        if item_models:
            add_row(*container_row)
            assert isinstance(items, dict)
            _walk([*path, _LIST_INDEX], items, defs, required=False, rows=rows)
            return
    elif schema_type == "object":
        extra = resolved.get("additionalProperties")
        value_schemas: list[dict[str, Any]] = []
        if isinstance(extra, dict):
            value_schemas = [extra]
        elif isinstance(extra, list):
            value_schemas = [v for v in extra if isinstance(v, dict)]
        if any(_model_variants(v, defs) for v in value_schemas):
            add_row(*container_row)
            for value_schema in value_schemas:
                _walk([*path, _MAP_KEY], value_schema, defs, required=False, rows=rows)
            return

    add_row(path, _render_type(node, defs), _render_default(node, required), description)


def _section_lines(
    name: str, node: dict[str, Any], defs: dict[str, Any], required: bool
) -> list[str]:
    resolved = _resolve_ref(node, defs)
    lines = [f"## `{name}`", ""]
    section_desc = resolved.get("description") or node.get("description")
    if section_desc:
        lines.extend([_clean_text(section_desc).replace("\\|", "|"), ""])

    rows: dict[str, _Row] = {}
    _walk([name], node, defs, required, rows)

    lines.append("| Key | Type | Default | Env var | Description |")
    lines.append("| --- | ---- | ------- | ------- | ----------- |")
    for key, row in rows.items():
        type_str = " \\| ".join(row.types)
        desc = _clean_text(row.description) if row.description else ""
        lines.append(f"| `{key}` | {type_str} | {row.default} | `{_env_var(row.path)}` | {desc} |")
    lines.append("")
    return lines


def build_markdown(schema: dict[str, Any] | None = None) -> str:
    """Render the full configuration reference document."""
    if schema is None:
        schema = build_config_schema()
    defs = schema.get("$defs", {})
    top_required = set(schema.get("required", []))

    lines = [_HEADER]
    for name, node in schema.get("properties", {}).items():
        lines.extend(_section_lines(name, node, defs, name in top_required))
    return "\n".join(lines).rstrip() + "\n"


def write_config_reference(path: Path | None = None) -> Path:
    """Generate the reference and write it to ``path`` (default canonical)."""
    target = path or CONFIG_REFERENCE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_markdown(), encoding="utf-8")
    return target


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the generated document to stdout instead of writing the file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the document to this path (defaults to docs/reference/config.md).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.stdout:
        sys.stdout.write(build_markdown())
        return 0
    output = args.output.resolve() if args.output is not None else None
    written = write_config_reference(output)
    try:
        display = written.relative_to(REPO_ROOT)
    except ValueError:
        display = written
    sys.stderr.write(f"Wrote {display}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
