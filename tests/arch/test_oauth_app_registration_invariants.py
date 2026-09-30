"""Architecture invariant for ``credentials.oauth_app_registration_id`` writes.

Every writer that sets ``credentials.oauth_app_registration_id`` must go
through the registration service or the connect-session flow handlers. That
is where the ``flow_kind`` ↔ ``credentials.type`` invariant is enforced; a
direct write elsewhere would bypass it.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.arch.conftest import SRC_ROOT, python_files_in

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

# Column-attribute assignment on a `credential` (or similarly-named) row.
# Matches `<row>.oauth_app_registration_id = ...` and the SQLAlchemy ORM keyword
# assignment `Credential(oauth_app_registration_id=...)`. Filter reads
# (`Credential.oauth_app_registration_id == ...`) are intentionally NOT caught
# here — the invariant is about writes, not reads.
_REGISTRATION_WRITE_PATTERN = re.compile(r"\.oauth_app_registration_id\s*=(?!=)")


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
