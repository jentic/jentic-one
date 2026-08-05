"""Latest-release service — persist and read the last-known-latest app release.

Wraps :class:`LatestReleaseRepository` so the web layer never touches the repo
directly (arch rule). The stored value is the newest release the CLI has
reported; the public version endpoint reads it to decide whether to advertise
an update. The backend never fetches releases itself.
"""

from __future__ import annotations

from jentic_one.admin.repos.latest_release_repo import LatestReleaseRepository
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context


class LatestReleaseService:
    """Records and reads the last-known-latest app release version."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def report(self, version: str, *, identity: Identity) -> str:
        """Upsert the singleton latest-release row; return the stored version.

        ``version`` must already be normalized (bare ``X.Y.Z``) by the caller.
        """
        async with self._ctx.admin_db.transaction() as session:
            record = await LatestReleaseRepository.upsert(
                session, version=version, reported_by=identity.sub
            )
            return record.version

    async def read_latest(self) -> str | None:
        """Return the last-known-latest version, or ``None`` if none reported."""
        async with self._ctx.admin_db.session() as session:
            record = await LatestReleaseRepository.get(session)
        return record.version if record is not None else None
