"""Offline exporter for the combined control-plane OpenAPI spec.

Builds the FastAPI combined app for the control-plane surfaces
(``control``, ``admin``, ``auth``, ``registry``) *without* starting any
databases, then serialises ``app.openapi()`` to deterministic, sorted YAML.

This is the single code path shared by:

- ``make openapi`` (regenerate the checked-in spec), and
- the drift test in ``tests/arch/test_openapi_conformance.py``

so the generated artefact never diverges between local regeneration and CI.

Run directly::

    uv run python -m tools.openapi_export            # writes the control spec
    uv run python -m tools.openapi_export --stdout    # print to stdout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from jentic_one.shared.web.openapi_meta import PUBLISHED_SERVERS, SERVERS

# Surfaces that make up the control plane. Broker is intentionally excluded —
# it has its own hand-curated spec under openapi/broker/.
CONTROL_PLANE_SURFACES: list[str] = ["control", "admin", "auth", "registry"]

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_SPEC_PATH = REPO_ROOT / "openapi" / "control" / "control.openapi.yaml"
# The UI client schema is the same document serialised as JSON; `make openapi`
# writes both and the drift test keeps them in lock-step.
UI_SPEC_PATH = REPO_ROOT / "ui" / "openapi.json"
DEFAULT_CONFIG = REPO_ROOT / "config" / "local-sqlite.yaml"


class _SpecDumper(yaml.SafeDumper):
    """YAML dumper that renders multi-line strings as literal block scalars."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_SpecDumper.add_representer(str, _represent_str)


def build_control_plane_spec() -> dict[str, Any]:
    """Build the combined control-plane app and return its OpenAPI document.

    Database connections are never opened: the FastAPI app is constructed
    synchronously and ``app.openapi()`` only introspects routes and models.
    """
    # Ensure a config is resolvable even when invoked outside `make` (which
    # exports JENTIC_CONFIG_FILE). The SQLite config has no external deps.
    os.environ.setdefault("JENTIC_CONFIG_FILE", str(DEFAULT_CONFIG))

    from jentic_one.shared.config import load_config
    from jentic_one.shared.context import Context
    from jentic_one.shared.web.app_factory import create_combined_app

    config = load_config()
    ctx = Context(config, allowed_dbs={"registry", "admin", "control"})
    app = create_combined_app(ctx, list(CONTROL_PLANE_SURFACES))
    # Pin the same-origin server so a local ``server.public_base_url`` never
    # leaks into the committed artefacts. Copy so the app's cached schema is
    # left untouched.
    spec: dict[str, Any] = {**app.openapi(), "servers": SERVERS}
    return spec


def published_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The checked-in YAML artefact: ``spec`` with placeholder deployment hosts.

    ``ui/openapi.json`` keeps the same-origin server: it is the SPA client's
    codegen input, and the generator bakes ``servers[0]`` into ``OpenAPI.BASE``.
    """
    return {**spec, "servers": PUBLISHED_SERVERS}


def dump_spec_yaml(spec: dict[str, Any]) -> str:
    """Serialise an OpenAPI document to deterministic, sorted YAML."""
    return yaml.dump(
        spec,
        Dumper=_SpecDumper,
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
        width=100_000,
    )


def dump_spec_json(spec: dict[str, Any]) -> str:
    """Serialise an OpenAPI document to deterministic, sorted JSON (UI client schema)."""
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _serialise(spec: dict[str, Any], path: Path) -> str:
    if path.suffix == ".json":
        return dump_spec_json(spec)
    return dump_spec_yaml(published_spec(spec))


def write_control_plane_spec(path: Path | None = None) -> Path:
    """Generate the control-plane spec and write it to ``path``.

    Format is chosen by the target extension: ``.json`` for the UI client
    schema, sorted YAML otherwise (the canonical checked-in control spec, which
    carries the placeholder servers — see :func:`published_spec`).
    """
    target = path or CONTROL_SPEC_PATH
    spec = build_control_plane_spec()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_serialise(spec, target), encoding="utf-8")
    return target


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the generated spec to stdout instead of writing the file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the spec to this path (defaults to openapi/control/control.openapi.yaml).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.stdout:
        sys.stdout.write(dump_spec_yaml(published_spec(build_control_plane_spec())))
        return 0
    output = args.output.resolve() if args.output is not None else None
    written = write_control_plane_spec(output)
    try:
        display = written.relative_to(REPO_ROOT)
    except ValueError:
        display = written
    sys.stderr.write(f"Wrote {display}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
