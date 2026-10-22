"""Offline exporter for the CLI profile manifest JSON Schema.

Serialises ``CliProfile.model_json_schema()`` (the Pydantic model every
shipped profile is validated against) to a deterministic, sorted JSON
document next to the manifests, so editors can validate a profile as it is
written (``# yaml-language-server: $schema=./cli-profile.schema.json``).

This is the single code path shared by ``make cli-profile-schema`` and the
drift test in ``tests/unit/shared/test_cli_profiles.py``.

Run directly::

    uv run python -m tools.cli_profile_schema_export            # write the schema
    uv run python -m tools.cli_profile_schema_export --stdout   # print it
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jentic_one.shared.cli_profiles.models import CliProfile

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_PROFILE_SCHEMA_PATH = (
    REPO_ROOT / "src" / "jentic_one" / "shared" / "cli_profiles" / "cli-profile.schema.json"
)


def build_cli_profile_schema() -> dict[str, Any]:
    """The CLI profile manifest JSON Schema."""
    schema: dict[str, Any] = CliProfile.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def dump_schema_json(schema: dict[str, Any]) -> str:
    """Serialise the schema to deterministic, sorted JSON (trailing newline)."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdout", action="store_true", help="Print instead of writing.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    text = dump_schema_json(build_cli_profile_schema())
    if args.stdout:
        sys.stdout.write(text)
        return 0
    CLI_PROFILE_SCHEMA_PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
