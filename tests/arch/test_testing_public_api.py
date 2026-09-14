"""Drift guard: the extending guide's ``jentic_one.testing`` references resolve.

``jentic_one.testing`` is public API — downstream packages import its
compliance bases from the worked examples in
``docs/development/extending-jentic-one.md``. The doc and the module live in
different files that merge independently, so a conflict resolution (or a
rename) can leave the doc advertising a symbol with no definition and nothing
failing. This gate pins both directions:

- every symbol the doc pulls from ``jentic_one.testing`` exists in the module
  and is exported via ``__all__``;
- every ``__all__`` symbol actually resolves on import.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import jentic_one.testing as testing_module

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

EXTENDING_DOC = REPO_ROOT / "docs" / "development" / "extending-jentic-one.md"

#: Names pulled from the module in the doc's code fences — both the
#: parenthesised and single-line import forms.
_IMPORT_RE = re.compile(r"from jentic_one\.testing import (?:\(([^)]*)\)|([\w, ]+))", re.MULTILINE)

#: Compliance-base mentions anywhere in the prose/tables (e.g.
#: ``BaseUnregisteredUrlHandlerComplianceTest``).
_BASE_MENTION_RE = re.compile(r"\bBase\w+ComplianceTest\b")


def _doc_referenced_names() -> set[str]:
    text = EXTENDING_DOC.read_text(encoding="utf-8")
    names: set[str] = set(_BASE_MENTION_RE.findall(text))
    for grouped, single in _IMPORT_RE.findall(text):
        names.update(n.strip() for n in (grouped or single).split(",") if n.strip())
    return names


@pytest.mark.arch
def test_all_exports_resolve() -> None:
    missing = [name for name in testing_module.__all__ if not hasattr(testing_module, name)]
    assert not missing, (
        f"jentic_one.testing.__all__ exports that do not resolve: {sorted(missing)} — "
        "__all__ and the module contents have drifted apart."
    )


@pytest.mark.arch
def test_extending_doc_references_are_exported() -> None:
    referenced = _doc_referenced_names()
    assert referenced, (
        f"{EXTENDING_DOC} no longer references any jentic_one.testing symbol — "
        "if the compliance examples moved, update this gate's doc path."
    )
    missing = sorted(referenced - set(testing_module.__all__))
    assert not missing, (
        f"{EXTENDING_DOC} references jentic_one.testing symbols that are not exported: "
        f"{missing} — either restore the export (a doc-advertised symbol is public API) "
        "or update the doc."
    )
