"""Resolve and load machine-readable rules facts published by the rules repo.

The ``jentic-one-rules`` repo is the source of truth for the parameterizable
enforcement facts (e.g. the ORM conventions in ``orm.facts.yaml``). This module
resolves that file with a robust lookup order and validates its
``schema_version`` so the arch tests can read the facts instead of hard-coding
them. A vendored copy is committed here so a standalone clone (with no external
rules repo mounted) still self-enforces.

Lookup order (first hit wins):

1. ``$JENTIC_RULES_DIR/rules/backend/<name>`` — explicit override (CI).
2. ``<repo>/../jentic-one-rules/rules/backend/<name>`` — sibling checkout.
3. ``tests/arch/vendored/<name>`` — committed fallback so a standalone clone
   (no rules repo mounted) still self-enforces.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_ARCH_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _ARCH_DIR.parent.parent
VENDORED_DIR = _ARCH_DIR / "vendored"


def candidate_paths(name: str) -> list[Path]:
    """Return the ordered lookup paths for a rules facts file named *name*."""
    rel = Path("rules") / "backend" / name
    paths: list[Path] = []
    env = os.environ.get("JENTIC_RULES_DIR")
    if env:
        paths.append(Path(env) / rel)
    paths.append(_REPO_ROOT.parent / "jentic-one-rules" / rel)
    paths.append(VENDORED_DIR / name)
    return paths


def mounted_facts_path(name: str) -> Path | None:
    """First existing non-vendored candidate, or None if only the vendored copy exists."""
    for p in candidate_paths(name):
        if p.is_file() and p.parent != VENDORED_DIR:
            return p
    return None


def load_facts(name: str, *, expected_schema: str) -> dict[str, Any]:
    """Load and schema-validate the first available facts file named *name*."""
    for p in candidate_paths(name):
        if p.is_file():
            data: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8"))
            got = data.get("schema_version")
            if got != expected_schema:
                raise ValueError(f"{p}: schema_version {got!r} != expected {expected_schema!r}")
            return data
    raise FileNotFoundError(
        f"Could not locate rules facts {name!r}. Tried: "
        + ", ".join(str(p) for p in candidate_paths(name))
    )


def orm_facts() -> dict[str, Any]:
    """Load the ORM enforcement facts (``orm.facts.yaml``)."""
    return load_facts("orm.facts.yaml", expected_schema="jentic.orm-facts/v1")
