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
import copy
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


# Keywords whose presence makes a union branch something other than a plain
# type: a branch that composes or references another schema cannot be folded
# into a type array.
_COMPOSITION_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "$ref"})


def _nullable_union_branch(branches: Any) -> dict[str, Any] | None:
    """The sole plain-typed branch of a ``<type> | null`` union, else ``None``.

    A union qualifies only when, after dropping its ``{type: 'null'}`` members,
    exactly one branch remains and that branch is a plain typed schema. A
    ``$ref`` branch, a composed branch, or a union of two real types stays as
    it is — a type array cannot carry their constraints.
    """
    if not isinstance(branches, list):
        return None
    if not any(isinstance(b, dict) and b.get("type") == "null" for b in branches):
        return None
    typed = [b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")]
    if len(typed) != 1:
        return None
    branch = typed[0]
    if not isinstance(branch, dict) or not isinstance(branch.get("type"), str):
        return None
    if _COMPOSITION_KEYWORDS & branch.keys():
        return None
    return branch


def collapse_nullable_unions(spec: dict[str, Any]) -> int:
    """Fold every ``<type> | null`` union into an OpenAPI 3.1 type array, in place.

    Pydantic renders ``Optional[X]`` as ``anyOf: [{type: X}, {type: 'null'}]``.
    The document is OpenAPI 3.1 (JSON Schema 2020-12), where ``type`` accepts a
    list, so ``type: [X, 'null']`` says the same thing in one node — one fewer
    indirection for a reader, and one fewer schema for a client generator.

    The surviving branch's own keywords (``format``, ``maxLength``, ``items``,
    ``properties``, ``examples``, …) move up beside the new ``type``, so no
    constraint is lost. A keyword already present on the parent wins: the parent
    describes the whole union and is the more specific statement.

    Returns the number of unions collapsed.
    """
    collapsed = 0

    def walk(node: Any) -> None:
        nonlocal collapsed
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for keyword in ("anyOf", "oneOf"):
            branch = _nullable_union_branch(node.get(keyword))
            if branch is None:
                continue
            del node[keyword]
            node["type"] = [branch.pop("type"), "null"]
            for key, value in branch.items():
                node.setdefault(key, value)
            collapsed += 1
            break
        for value in node.values():
            walk(value)

    walk(spec)
    return collapsed


def published_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The checked-in YAML artefact: ``spec`` with placeholder deployment hosts.

    ``ui/openapi.json`` keeps the same-origin server: it is the SPA client's
    codegen input, and the generator bakes ``servers[0]`` into ``OpenAPI.BASE``.

    Nullable unions are collapsed here only. ``ui/openapi.json`` feeds
    ``npm run codegen``, and a type array changes the TypeScript the generator
    emits; keeping the transform on this artefact leaves that diff as a separate
    decision. ``spec`` is deep-copied, so callers that derive both artefacts
    from one document are unaffected.
    """
    published = copy.deepcopy(spec)
    collapse_nullable_unions(published)
    return {**published, "servers": PUBLISHED_SERVERS}


def ui_client_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The SPA codegen input: ``spec`` without the operations' ``default`` responses.

    ``openapi-typescript-codegen`` folds a ``default`` response into the success
    union, so every service method would be typed ``Promise<X | ProblemDetail>``
    although the client throws ``ApiError`` on any non-2xx. The default
    (RFC 9457 catch-all) is documentation for spec readers, not a result the SPA
    can receive, so it is dropped from this artefact only.
    """
    paths: dict[str, Any] = {}
    for path, item in spec.get("paths", {}).items():
        paths[path] = {
            key: (
                {**op, "responses": {c: r for c, r in op["responses"].items() if c != "default"}}
                if isinstance(op, dict) and "responses" in op
                else op
            )
            for key, op in item.items()
        }
    return {**spec, "paths": paths}


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
        return dump_spec_json(ui_client_spec(spec))
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
