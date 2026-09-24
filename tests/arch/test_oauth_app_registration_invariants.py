"""Architecture invariants for OAuth app registrations + credentials.owner_user_id.

Guards two failure modes that would silently break the shared-credential model:

1. ``credentials.owner_user_id`` must only be filtered inside sanctioned seams
   (the broker's binding resolver, the credentials service's scoping paths).
   A stray query that filters by ``owner_user_id`` alone — outside the
   resolver's binding boundary — would leak another user's rows, or hide the
   caller's own row behind an incorrect owner test.

2. Every writer that sets ``credentials.oauth_app_registration_id`` must go
   through the registration service or the connect-session flow handlers.
   That is where the ``flow_kind`` ↔ ``credentials.type`` invariant is
   enforced; a direct write elsewhere would bypass it.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.arch.conftest import SRC_ROOT, python_files_in

# Sanctioned modules for filtering credentials by owner_user_id.
_OWNER_FILTER_ALLOWLIST: frozenset[Path] = frozenset(
    {
        SRC_ROOT / "broker" / "repos" / "credential_binding_resolver.py",
        SRC_ROOT / "control" / "scoping" / "filters.py",
        SRC_ROOT / "control" / "repos" / "credential_repo.py",
    }
)

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

# WHERE-shaped uses of the column: ORM comparison (Credential.owner_user_id ==),
# is_() calls, and raw SQL fragments. Excludes function signatures + kwarg
# threading, which are legitimate ways to pass the value through.
_OWNER_FILTER_PATTERN = re.compile(
    r"(Credential\.owner_user_id\s*(==|\.is_))|owner_user_id\s+IS\s+"
)
# Column-attribute assignment on a `credential` (or similarly-named) row.
# Matches `<row>.oauth_app_registration_id = ...` and the SQLAlchemy ORM keyword
# assignment `Credential(oauth_app_registration_id=...)`. Filter reads
# (`Credential.oauth_app_registration_id == ...`) are intentionally NOT caught
# here — the invariant is about writes, not reads.
_REGISTRATION_WRITE_PATTERN = re.compile(r"\.oauth_app_registration_id\s*=(?!=)")


def test_owner_user_id_filter_confined_to_sanctioned_modules() -> None:
    """Only the resolver + scoping filter + credential repo may compare
    ``owner_user_id``. Any other file that references it in a WHERE-shaped
    expression is a candidate for a leak.
    """
    offenders: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file in _OWNER_FILTER_ALLOWLIST:
            continue
        # Migrations carry raw DDL that references the column; they aren't
        # runtime query sites.
        if "migrations" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _OWNER_FILTER_PATTERN.search(text):
            offenders.append(str(py_file.relative_to(SRC_ROOT)))

    assert not offenders, (
        "credentials.owner_user_id is filtered outside the sanctioned resolver "
        "seams. Compose the filter through the binding resolver or scoping "
        "filters instead. Offenders:\n" + "\n".join(f"  - {o}" for o in offenders)
    )


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
        # The ORM model declaration itself uses ``oauth_app_registration_id =``.
        if py_file.name == "credentials.py" and "core/schema" in str(py_file):
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _REGISTRATION_WRITE_PATTERN.search(text):
            offenders.append(str(py_file.relative_to(SRC_ROOT)))

    assert not offenders, (
        "credentials.oauth_app_registration_id is assigned outside the "
        "sanctioned writer seams. Route the write through the credential "
        "service or flow handler. Offenders:\n" + "\n".join(f"  - {o}" for o in offenders)
    )
