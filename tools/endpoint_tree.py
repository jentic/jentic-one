"""Generate the endpoint → typical-caller → scope reference tree.

Joins every control-plane operation with the standalone broker surface and
renders three artifacts:

- ``docs/reference/endpoints.md``   — human-readable Markdown (GitHub-rendered)
- ``docs/reference/endpoints.json`` — portable, stack-agnostic machine-readable join
- a colored ANSI tree (for ``--format ansi``)

The join itself lives in :mod:`jentic_one.shared.web.endpoint_reference` (shipped
in the wheel) so the committed ``endpoints.json`` and the live
``GET /reference/endpoints.json`` endpoint share one code path and cannot drift.
This module is the *offline renderer* on top of that builder.

The generated files carry a ``DO NOT EDIT`` header; the source of truth is code
plus the curated map in ``jentic_one.shared.web.endpoint_scopes``. Regenerate via
``make endpoints``.

Run directly::

    uv run python -m tools.endpoint_tree                 # write docs/reference/*
    uv run python -m tools.endpoint_tree --format ansi    # print colored tree
    uv run python -m tools.endpoint_tree --format json --stdout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from jentic_one.shared.web.endpoint_reference import (
    GROUP_ORDER,
    Endpoint,
    build_reference_payload,
    collect_endpoints,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = REPO_ROOT / "docs" / "reference"
ENDPOINTS_MD = REFERENCE_DIR / "endpoints.md"
ENDPOINTS_JSON = REFERENCE_DIR / "endpoints.json"
DEFAULT_CONFIG = REPO_ROOT / "config" / "local-sqlite.yaml"

_GENERATED_HEADER_MD = """<!--
GENERATED FILE — DO NOT EDIT.

This endpoint + scope reference is generated from code by `make endpoints`
(tools/endpoint_tree.py). Editing it by hand will be overwritten and will fail
the drift-guard test.

How to update (humans & agents)
-------------------------------
- The scope of a route is read from its `get_current_identity(required_permissions=[...])`
  dependency. To make a route's scope appear here, add that argument upstream.
- For routes whose scope is enforced in the service layer, edit the curated map
  `PATH_SCOPE_OVERRIDES` / `ACTOR_TYPE_OVERRIDES` in
  `src/jentic_one/shared/web/endpoint_scopes.py`.
- Then run `make endpoints` (regenerates this file + endpoints.json) and
  `make openapi` (regenerates the specs), and commit code + artifacts together.

Agents: treat `src/jentic_one/shared/web/endpoint_scopes.py` as the editable
source of truth, never this file.
-->
"""

#: A short, GitHub-visible banner (HTML comments are hidden in rendered Markdown,
#: so a reader landing on the .md needs an on-page do-not-edit notice).
_GENERATED_BANNER_MD = (
    "> **Generated file — do not edit by hand.** Produced by `make endpoints` from "
    "code. To correct an entry, edit `src/jentic_one/shared/web/endpoint_scopes.py` "
    "and regenerate (see [docs/reference/README.md](README.md))."
)


def _build_control_app() -> FastAPI:
    """Build the combined control-plane app DB-less."""
    os.environ.setdefault("JENTIC_CONFIG_FILE", str(DEFAULT_CONFIG))
    from jentic_one.shared.config import load_config
    from jentic_one.shared.context import Context
    from jentic_one.shared.web.app_factory import create_combined_app

    config = load_config()
    ctx = Context(config, allowed_dbs={"registry", "admin", "control"})
    return create_combined_app(ctx, ["control", "admin", "auth", "registry"])


def _build_broker_app() -> FastAPI:
    """Build the standalone broker app DB-less (best-effort)."""
    os.environ.setdefault("JENTIC_CONFIG_FILE", str(DEFAULT_CONFIG))
    from jentic_one.__main__ import _expand_allowed_dbs
    from jentic_one.broker.web.app import create_app
    from jentic_one.shared.config import load_config
    from jentic_one.shared.context import Context

    config = load_config()
    ctx = Context(config, allowed_dbs=_expand_allowed_dbs(["broker"], config))
    return create_app(ctx)


def collect() -> list[Endpoint]:
    """Collect control-plane + broker endpoints from freshly-built apps."""
    control_app = _build_control_app()
    try:
        broker_app = _build_broker_app()
    except Exception as exc:  # pragma: no cover - broker build is best-effort
        sys.stderr.write(f"warning: could not introspect broker surface: {exc}\n")
        broker_app = None
    return collect_endpoints(control_app, broker_app)


# --- rendering --------------------------------------------------------------


def _grouped(endpoints: list[Endpoint]) -> dict[str, dict[str, list[Endpoint]]]:
    """group -> surface -> [endpoints]."""
    out: dict[str, dict[str, list[Endpoint]]] = {g: {} for g in GROUP_ORDER}
    for ep in endpoints:
        out.setdefault(ep.group, {}).setdefault(ep.surface, []).append(ep)
    return out


def render_markdown(endpoints: list[Endpoint]) -> str:
    grouped = _grouped(endpoints)
    lines = [_GENERATED_HEADER_MD, "# Endpoint & scope reference\n", _GENERATED_BANNER_MD + "\n"]
    lines.append(
        "Every API endpoint grouped by its **typical caller**, then by surface, "
        "annotated with the **scope(s)** it requires.\n"
    )
    lines.append(
        "> The grouping and the _Typical caller_ column are an **advisory hint** at "
        "who usually calls a route, inferred from the scope family. They are **not** "
        "an enforced restriction: access is gated by the **scope**, not the actor "
        "kind, so any actor holding the required scope can call the endpoint.\n"
    )
    total = len(endpoints)
    lines.append(f"_Total endpoints: **{total}**._\n")
    for group in GROUP_ORDER:
        surfaces = grouped.get(group)
        if not surfaces:
            continue
        count = sum(len(v) for v in surfaces.values())
        lines.append(f"\n## {group} ({count})\n")
        for surface in sorted(surfaces):
            lines.append(f"\n### `{surface}`\n")
            lines.append("| Method | Path | Scope(s) | Typical caller | Summary |")
            lines.append("|---|---|---|---|---|")
            for ep in surfaces[surface]:
                if ep.public or not ep.authenticated:
                    scopes = "_public — no auth_"
                    typical = "—"
                else:
                    scopes = (
                        ", ".join(f"`{s}`" for s in ep.required_scopes) or "_any authenticated_"
                    )
                    typical = ep.typical_caller or "any"
                summary = ep.summary.replace("|", "\\|") if ep.summary else ""
                if ep.auth_note:
                    note = ep.auth_note.replace("|", "\\|")
                    summary = f"{summary} _({note})_" if summary else f"_{note}_"
                lines.append(f"| {ep.method} | `{ep.path}` | {scopes} | {typical} | {summary} |")
    lines.append("")
    return "\n".join(lines)


def render_json(endpoints: list[Endpoint]) -> str:
    """Portable, stack-agnostic JSON — identical shape to GET /reference/endpoints.json."""
    payload = build_reference_payload(endpoints)
    payload = {
        "_generated": (
            "Generated by `make endpoints` from code — do not edit by hand. "
            "Source of truth: src/jentic_one/shared/web/endpoint_scopes.py. "
            "See docs/reference/README.md."
        ),
        **payload,
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "magenta": "\033[35m",
}


def render_ansi(endpoints: list[Endpoint], color: bool = True) -> str:
    def c(text: str, *styles: str) -> str:
        if not color:
            return text
        return "".join(_ANSI[s] for s in styles) + text + _ANSI["reset"]

    grouped = _grouped(endpoints)
    out: list[str] = []
    out.append(c("Endpoint & scope reference", "bold"))
    for group in GROUP_ORDER:
        surfaces = grouped.get(group)
        if not surfaces:
            continue
        count = sum(len(v) for v in surfaces.values())
        out.append(c(f"\n{group} ({count})", "bold", "cyan"))
        for surface in sorted(surfaces):
            out.append(c(f"  {surface}/", "magenta"))
            for ep in surfaces[surface]:
                if ep.public or not ep.authenticated:
                    scopes = "public — no auth"
                else:
                    scopes = ", ".join(ep.required_scopes) or "any authenticated"
                out.append(
                    f"    {c(ep.method.ljust(6), 'green')} {ep.path}  {c('→ ' + scopes, 'yellow')}"
                )
    return "\n".join(out) + "\n"


def write_reference(endpoints: list[Endpoint] | None = None) -> list[Path]:
    eps = endpoints if endpoints is not None else collect()
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    ENDPOINTS_MD.write_text(render_markdown(eps), encoding="utf-8")
    ENDPOINTS_JSON.write_text(render_json(eps), encoding="utf-8")
    return [ENDPOINTS_MD, ENDPOINTS_JSON]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("files", "markdown", "json", "ansi"),
        default="files",
        help="files: write docs/reference/*; markdown/json/ansi: render to stdout.",
    )
    parser.add_argument("--stdout", action="store_true", help="Print instead of writing files.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    endpoints = collect()
    if args.format == "files" and not args.stdout:
        written = write_reference(endpoints)
        for path in written:
            try:
                display = path.relative_to(REPO_ROOT)
            except ValueError:
                display = path
            sys.stderr.write(f"Wrote {display}\n")
        return 0
    if args.format in ("files", "markdown"):
        sys.stdout.write(render_markdown(endpoints))
    elif args.format == "json":
        sys.stdout.write(render_json(endpoints))
    elif args.format == "ansi":
        sys.stdout.write(render_ansi(endpoints, color=not args.no_color))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
