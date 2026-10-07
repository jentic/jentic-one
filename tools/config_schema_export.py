"""Offline exporter for the backend configuration JSON Schema.

Serialises ``AppConfig.model_json_schema()`` — the single Pydantic source of
truth for jentic-one's configuration (nested sections, field names, types,
defaults, descriptions) — to a deterministic, sorted JSON document.

This is the single code path shared by:

- ``make config-schema`` (regenerate the checked-in schema), and
- the drift test in ``tests/arch/test_config_schema_conformance.py``

so the artefact never diverges between local regeneration and CI. The schema
is the source the Go CLI's ``jenticctl install`` flags/wizard are generated from
(``cli/`` ``make generate-config`` → ``go-jsonschema``), mirroring the
FastAPI-app → OpenAPI-spec → generated-SDK chain: one drift gate per link so a
config change that skips regeneration is a red build on both sides.

The ``extensions`` field is deliberately excluded: it is an open ``dict`` the
OSS schema cannot describe (enterprise overlays register their own sections at
import time), and it is machine state, not an installer knob.

Run directly::

    uv run python -m tools.config_schema_export            # writes config/config-schema.json
    uv run python -m tools.config_schema_export --stdout   # print to stdout
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_SCHEMA_PATH = REPO_ROOT / "config" / "config-schema.json"


def build_config_schema() -> dict[str, Any]:
    """Return the AppConfig JSON Schema with the open ``extensions`` map dropped.

    ``model_json_schema()`` only introspects the model — no config file, no
    database, nothing to load — so this is safe to run in any environment.
    """
    from jentic_one.shared.config import AppConfig

    schema: dict[str, Any] = AppConfig.model_json_schema()
    # `extensions` is an open dict[str, BaseModel] the OSS schema can't describe
    # and is not an installer knob; drop it from properties (and required, if the
    # model ever marks it so) so the generated Go struct stays closed.
    props = schema.get("properties")
    if isinstance(props, dict):
        props.pop("extensions", None)
    required = schema.get("required")
    if isinstance(required, list) and "extensions" in required:
        required.remove("extensions")
    return schema


def dump_schema_json(schema: dict[str, Any]) -> str:
    """Serialise the schema to deterministic, sorted JSON (trailing newline)."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_config_schema(path: Path | None = None) -> Path:
    """Generate the config schema and write it to ``path`` (default canonical)."""
    target = path or CONFIG_SCHEMA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_schema_json(build_config_schema()), encoding="utf-8")
    return target


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the generated schema to stdout instead of writing the file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the schema to this path (defaults to config/config-schema.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.stdout:
        sys.stdout.write(dump_schema_json(build_config_schema()))
        return 0
    output = args.output.resolve() if args.output is not None else None
    written = write_config_schema(output)
    try:
        display = written.relative_to(REPO_ROOT)
    except ValueError:
        display = written
    sys.stderr.write(f"Wrote {display}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
