"""System admin router — record operator/CLI-reported system metadata.

Currently exposes the write side of the app-release update check: the CLI
(``jenticctl update``) reports the latest release tag it discovered so the UI
can show an "update available" banner. The public read side lives in
:mod:`jentic_one.shared.web.system` (``GET /system/version``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from jentic.problem_details import BadRequest
from pydantic import BaseModel, Field

from jentic_one.admin.services.latest_release_service import LatestReleaseService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.version_compare import normalize_release
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import get_ctx

router = APIRouter()


class LatestReleaseSetRequest(BaseModel):
    """Body for reporting the latest available release."""

    version: str = Field(
        description="The latest release version, e.g. 'v0.26.0' or '0.26.0'.",
        min_length=1,
    )


class LatestReleaseResponse(BaseModel):
    """The stored latest-release version (normalized, no leading 'v')."""

    version: str


def _normalized_version(raw: str) -> str:
    """Validate a clean ``[v]X.Y.Z`` and return it without the leading ``v``.

    Rejects anything that is not a clean three-part release version (branches,
    pre-releases, SHAs) so the stored value is always comparable.
    """
    parsed = normalize_release(raw)
    if parsed is None:
        raise BadRequest(
            detail=f"version {raw!r} is not a clean release version (expected [v]X.Y.Z)"
        )
    return parsed


@router.post("/admin/system/latest-release", summary="Report the latest available release")
async def set_latest_release(
    body: LatestReleaseSetRequest,
    identity: Identity = get_current_identity(required_permissions=["instance:write"]),
    ctx: Context = Depends(get_ctx),
) -> LatestReleaseResponse:
    """Record the latest available app release (operator/CLI action).

    The value is normalized to a bare ``X.Y.Z`` and upserted into the singleton
    ``latest_releases`` row; the public ``GET /system/version`` reads it to decide
    whether to advertise an update.
    """
    version = _normalized_version(body.version)
    stored = await LatestReleaseService(ctx).report(version, identity=identity)
    return LatestReleaseResponse(version=stored)
