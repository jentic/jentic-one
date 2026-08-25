"""``GET /system/version`` — running vs. latest-available app release.

Lets the UI show the running version everywhere and an "update available"
banner when a newer release has been published. Requires an authenticated
session (any valid caller) — both consumers (the update banner and the user
menu's version line) render only inside the signed-in app shell, and gating it
keeps the exact running build off unauthenticated fingerprinting (OWASP ASVS
14.3.3), matching how GitLab and Nextcloud gate their update-check data. No
special permission is needed.

The running version comes from the package metadata; the latest-available
release is resolved server-side from GitHub (cached in-process, best-effort) by
``ReleaseChecker`` — see ``shared/release_check.py``. On surfaces where the
check is disabled, air-gapped, or a remote backend, ``latest`` degrades to
``null`` and ``update_available`` to ``false`` rather than erroring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from jentic_one import __version__
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.version_compare import is_update_available
from jentic_one.shared.web.deps import get_ctx, get_current_identity

SYSTEM_VERSION_PATH = "/system/version"


class VersionResponse(BaseModel):
    """The running app version and the latest release known to this backend."""

    current: str = Field(description="The version of jentic-one currently running on this backend.")
    latest: str | None = Field(
        default=None,
        description=(
            "The latest published release of jentic-one, without a leading 'v'. "
            "Null when the backend can't determine it (update check disabled, "
            "air-gapped, a remote backend, or GitHub was unreachable)."
        ),
    )
    update_available: bool = Field(
        description=(
            "True when `latest` is a newer release than `current`. Matches the "
            "verdict `jenticctl update` would print."
        )
    )


def get_system_router() -> APIRouter:
    """Router exposing the version endpoint (``GET /system/version``)."""
    router = APIRouter()

    @router.get(
        SYSTEM_VERSION_PATH,
        operation_id="getVersion",
        summary="Running and latest-available app version",
        response_model=VersionResponse,
    )
    async def version(
        ctx: Context = Depends(get_ctx),
        _identity: Identity = get_current_identity(),
    ) -> VersionResponse:
        """Return the running version and the latest available release.

        Requires an authenticated session (any valid caller; no special
        permission). The SPA reads it from inside the signed-in shell to show the
        current version and, when a newer release is available, an update banner.
        The latest release is resolved best-effort (cached); on any failure it is
        ``null`` and no banner shows.
        """
        latest = await ctx.release_checker.latest_version()
        return VersionResponse(
            current=__version__,
            latest=latest,
            update_available=is_update_available(__version__, latest),
        )

    return router
