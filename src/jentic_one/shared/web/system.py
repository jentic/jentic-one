"""``GET /system/version`` — running vs. latest-known app release.

Lets the UI show the running version everywhere and an "update available"
banner when a newer release has been reported. Requires an authenticated
session (any valid caller) — both consumers (the update banner and the user
menu's version line) render only inside the signed-in app shell, and gating it
keeps the exact running build off unauthenticated fingerprinting (OWASP ASVS
14.3.3). No special permission is needed. It reads the running version from the
package metadata and the last-known-latest release from the admin DB (populated
best-effort by ``jenticctl update``). The backend never fetches releases
itself, so there is no outbound egress here.

On surfaces without an admin database (e.g. a standalone registry or auth
surface) ``latest`` degrades to ``null`` and ``update_available`` to ``false``
rather than erroring.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from jentic_one import __version__
from jentic_one.admin.services.latest_release_service import LatestReleaseService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.version_compare import is_update_available
from jentic_one.shared.web.deps import get_ctx, get_current_identity

SYSTEM_VERSION_PATH = "/system/version"

_log = structlog.get_logger(__name__)


class VersionResponse(BaseModel):
    """The running app version and the latest release known to this backend."""

    current: str = Field(
        description="The version of jentic-one currently running on this backend."
    )
    latest: str | None = Field(
        default=None,
        description=(
            "The latest release version reported to this backend (by `jenticctl "
            "update`), without a leading 'v'. Null when nothing has been reported "
            "yet or this surface has no admin database."
        ),
    )
    update_available: bool = Field(
        description=(
            "True when `latest` is a newer release than `current`. Matches the "
            "verdict `jenticctl update` would print."
        )
    )


async def _latest_known(ctx: Context) -> str | None:
    """Read the last-known-latest release from the admin DB, or None if unavailable.

    Best-effort: a surface without an admin database (standalone registry/auth) or
    a transient DB error degrades to ``None`` (UI simply shows the current version,
    no banner) rather than 500ing.
    """
    if not ctx.is_db_allowed("admin"):
        return None
    try:
        return await LatestReleaseService(ctx).read_latest()
    except Exception:
        # The version probe must never fail on a DB hiccup — degrade to "unknown".
        _log.warning("system.version.latest_read_failed", exc_info=True)
        return None


def get_system_router() -> APIRouter:
    """Router exposing the version endpoint (``GET /system/version``)."""
    router = APIRouter()

    @router.get(
        SYSTEM_VERSION_PATH,
        operation_id="getVersion",
        summary="Running and latest-known app version",
        response_model=VersionResponse,
    )
    async def version(
        ctx: Context = Depends(get_ctx),
        _identity: Identity = get_current_identity(),
    ) -> VersionResponse:
        """Return the running version and the latest release known to this backend.

        Requires an authenticated session (any valid caller; no special
        permission). The SPA reads it from inside the signed-in shell to show the
        current version and, when a newer release has been reported, an update
        banner. Gating it keeps the exact running build off unauthenticated
        fingerprinting while costing nothing (every consumer is already signed in).
        """
        latest = await _latest_known(ctx)
        return VersionResponse(
            current=__version__,
            latest=latest,
            update_available=is_update_available(__version__, latest),
        )

    return router
