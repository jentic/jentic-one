"""Enforce that no production code calls session.commit() or session.rollback() directly.

All transaction management must go through DatabaseSession.transaction().
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

ALLOWED_FILES = frozenset(
    {
        SRC_ROOT / "shared" / "db" / "session.py",
    }
)

MIGRATIONS_DIR = SRC_ROOT / "migrations"

#: Receiver names that denote a DB session/connection — the objects whose bare
#: ``commit()``/``rollback()`` this rule governs. Matched against the last attribute
#: segment (``self.session`` → ``session``) or a plain variable name. In addition to
#: these exact names, any receiver whose name ends in ``session``/``_session``/``_conn``
#: is treated as a session (covers multi-DB handlers using ``admin_session``,
#: ``control_session``, ``read_session``, ``audit_session``, etc.) so the invariant
#: cannot be dodged by a differently-named session binding.
_SESSION_RECEIVER_NAMES = frozenset({"session", "conn", "connection", "db_session", "sess"})


def _is_session_receiver(node: ast.expr) -> bool:
    """True if *node* names a DB session/connection (vs an arbitrary service object)."""
    if isinstance(node, ast.Name):
        name: str = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    else:
        return False
    return (
        name in _SESSION_RECEIVER_NAMES
        or name.endswith("session")
        or name.endswith("_conn")
        or name == "conn"
    )


def _check_file(filepath: Path) -> list[str]:
    """Return violation messages if the file calls session.commit() or session.rollback().

    A specific call may be exempted with a trailing ``# arch-allow: manual-commit``
    comment on the same line. This is reserved for the rare commit-before-rollback
    case that ``DatabaseSession.transaction()`` cannot express (it rolls back on
    exception by design); every use must carry a comment explaining why.
    """
    source = filepath.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        value = node.value
        if not isinstance(value, ast.Await):
            continue
        awaited = value.value
        if not isinstance(awaited, ast.Call):
            continue
        func = awaited.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr in ("commit", "rollback"):
            # Only flag calls on a *session/connection* receiver — the transaction
            # primitives this rule governs. Domain services may legitimately expose a
            # method named ``rollback`` (e.g. OverlayService.rollback un-confirms an
            # overlay); that is not a raw session.rollback() and must not trip the rule.
            if not _is_session_receiver(func.value):
                continue
            line = source_lines[node.lineno - 1] if node.lineno <= len(source_lines) else ""
            if "# arch-allow: manual-commit" in line:
                continue
            violations.append(
                f"{filepath}:{node.lineno} — calls session.{func.attr}() directly "
                f"(use DatabaseSession.transaction() instead)"
            )

    return violations


@pytest.mark.arch
def test_no_manual_commit() -> None:
    """No production code outside session.py should call session.commit()/rollback()."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file in ALLOWED_FILES:
            continue
        if py_file.is_relative_to(MIGRATIONS_DIR):
            continue
        violations.extend(_check_file(py_file))
    assert not violations, (
        "Production code must not call session.commit()/rollback() directly — "
        "use DatabaseSession.transaction():\n" + "\n".join(violations)
    )
