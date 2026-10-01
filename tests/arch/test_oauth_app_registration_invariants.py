"""Architecture invariant for ``credentials.oauth_app_registration_id`` writes.

Every writer that sets ``credentials.oauth_app_registration_id`` must go
through the registration service or the connect-session flow handlers. That
is where the ``flow_kind`` ↔ ``credentials.type`` invariant is enforced; a
direct write elsewhere would bypass it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch.conftest import SRC_ROOT, python_files_in

_COLUMN = "oauth_app_registration_id"

# Sanctioned modules for writing credentials.oauth_app_registration_id.
_REGISTRATION_WRITER_ALLOWLIST: frozenset[Path] = frozenset(
    {
        SRC_ROOT / "control" / "services" / "integrations" / "flow_handlers" / "auth_code.py",
        SRC_ROOT
        / "control"
        / "services"
        / "integrations"
        / "flow_handlers"
        / "device_authorization.py",
        SRC_ROOT / "control" / "services" / "integrations" / "connect_session_service.py",
        SRC_ROOT / "control" / "services" / "credentials" / "service.py",
        SRC_ROOT / "control" / "repos" / "credential_repo.py",
    }
)

# Calls whose keyword / dict-literal arguments write columns: the ORM
# constructor ``Credential(...)``, Core ``insert(...).values(...)`` /
# ``update(...).values(...)``, and ``Query.update({...})``.
_WRITING_CALLS = frozenset({"Credential", "values", "update"})


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _writes_column(node: ast.AST) -> bool:
    """Whether ``node`` assigns the column (reads and filters never match)."""
    if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        # Attribute targets only: the ORM declaration ``oauth_app_registration_id:
        # Mapped[...] = mapped_column(...)`` assigns a bare name.
        return any(isinstance(t, ast.Attribute) and t.attr == _COLUMN for t in targets)
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node.func)
    if name == "setattr":
        return (
            len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == _COLUMN
        )
    if name not in _WRITING_CALLS:
        return False
    if any(kw.arg == _COLUMN for kw in node.keywords):
        return True
    return any(
        isinstance(arg, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == _COLUMN for k in arg.keys)
        for arg in node.args
    )


def _offending_lines(source: str) -> list[int]:
    return [
        getattr(node, "lineno", 0) for node in ast.walk(ast.parse(source)) if _writes_column(node)
    ]


def test_oauth_app_registration_id_writers_confined_to_flow_handlers() -> None:
    """Only the connect-session flow handlers and the credential repo/service
    may assign ``credentials.oauth_app_registration_id``. Direct writes
    elsewhere would bypass the ``flow_kind`` ↔ ``credentials.type`` check
    those seams enforce.
    """
    offenders: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file in _REGISTRATION_WRITER_ALLOWLIST:
            continue
        # Migrations carry raw DDL that references the column; they aren't
        # runtime query sites.
        if "migrations" in py_file.parts:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        offenders.extend(
            f"{py_file.relative_to(SRC_ROOT)}:{line}" for line in _offending_lines(source)
        )

    assert not offenders, (
        "credentials.oauth_app_registration_id is assigned outside the "
        "sanctioned writer seams. Route the write through the credential "
        "service or flow handler. Offenders:\n" + "\n".join(f"  - {o}" for o in offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        "row.oauth_app_registration_id = rid",
        "row.oauth_app_registration_id: str | None = rid",
        "Credential(name=f(x), oauth_app_registration_id=rid)",
        "models.Credential(oauth_app_registration_id=rid)",
        "insert(Credential).values(name=g(h(x)), oauth_app_registration_id=rid)",
        "update(Credential).where(Credential.id == cid).values(oauth_app_registration_id=rid)",
        "update(Credential).values({'oauth_app_registration_id': rid})",
        "q.update({'oauth_app_registration_id': rid})",
        "setattr(row, 'oauth_app_registration_id', rid)",
    ],
)
def test_detector_flags_every_write_form(source: str) -> None:
    assert _offending_lines(source)


@pytest.mark.parametrize(
    "source",
    [
        "oauth_app_registration_id: Mapped[str | None] = mapped_column(String)",
        "select(Credential).where(Credential.oauth_app_registration_id == rid)",
        "rid = row.oauth_app_registration_id",
        "await svc.create_session(vendor_key=v, oauth_app_registration_id=rid)",
    ],
)
def test_detector_ignores_reads_and_declarations(source: str) -> None:
    assert not _offending_lines(source)
