"""Active shared OAuth-app registrations, read for broker denial guidance.

A missing-credential denial tells the agent which connect key serves the API
it was denied. Registrations live in the control DB's
``oauth_app_registrations``; the broker may not import ``control`` ORM, so the
read is raw SQL behind ``ConnectableRegistrationSourceProtocol``. Coverage is
decided in Python (``shared.access_guidance.connect_target``) on the same rule
the broker uses for credentials.
"""

from __future__ import annotations

from sqlalchemy import text

from jentic_one.shared.broker.protocols import ConnectableRegistration
from jentic_one.shared.db import DatabaseSession

# control DB — inactive registrations refuse new connects, so they are never
# suggested. Ordered so the caller's match is deterministic across backends.
_ACTIVE_REGISTRATIONS = text(
    "SELECT id, api_vendor, catalog_api_id FROM oauth_app_registrations "
    "WHERE is_active ORDER BY api_vendor, id"
)


class ConnectableRegistrationReader:
    """Lists active shared OAuth-app registrations from the control DB.

    Implements ``ConnectableRegistrationSourceProtocol``.
    """

    def __init__(self, control_db: DatabaseSession) -> None:
        self._control_db = control_db

    async def list_active(self) -> tuple[ConnectableRegistration, ...]:
        """Return every active registration's id, connect key and catalog API."""
        async with self._control_db.session() as session:
            rows = (await session.execute(_ACTIVE_REGISTRATIONS)).all()
        return tuple(
            ConnectableRegistration(
                id=row.id, api_vendor=row.api_vendor, catalog_api_id=row.catalog_api_id
            )
            for row in rows
        )
