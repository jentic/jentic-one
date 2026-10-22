"""CLI profiles router: the shipped vendor CLI manifests, and provisioning their APIs.

Served by the registry surface, which owns the imports provisioning runs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from jentic_one.registry.services.cli_profile_service import (
    ApiAvailability,
    CliProfileService,
)
from jentic_one.registry.web.deps import get_cli_profile_service
from jentic_one.registry.web.schemas.cli_profiles import (
    CliProfileApiResponse,
    CliProfileHardDenyResponse,
    CliProfileListResponse,
    CliProfilePresetParamResponse,
    CliProfilePresetResponse,
    CliProfileProvisionedApiResponse,
    CliProfileProvisionResponse,
    CliProfileResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.cli_profiles import CliProfile
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.openapi_responses import not_found, with_responses

router = APIRouter()


def _profile_response(
    profile: CliProfile, enabled: bool, availability: list[ApiAvailability]
) -> CliProfileResponse:
    return CliProfileResponse(
        profile=profile.profile,
        display_name=profile.display_name,
        vendor=profile.vendor.id,
        vendor_enabled=enabled,
        cli=profile.cli.binary,
        supported_range=profile.cli.supported_range,
        credential_mode=profile.credential.mode,
        apis=[
            CliProfileApiResponse(
                role=a.api.role.value,
                source=a.api.source,
                api_id=a.api.catalog_api_id,
                vendor=a.vendor,
                name=a.name,
                version=a.version,
                connect=a.api.connect,
                live=a.live,
            )
            for a in availability
        ],
        presets=[
            CliProfilePresetResponse(
                name=name,
                description=preset.description,
                roles=list(dict.fromkeys(rule.api.value for rule in preset.rules)),
                params=[
                    CliProfilePresetParamResponse(
                        name=param_name,
                        kind=param.kind,
                        description=param.description,
                        default=param.default,
                    )
                    for param_name, param in preset.params.items()
                ],
            )
            for name, preset in profile.presets.items()
        ],
        hard_deny=[
            CliProfileHardDenyResponse(role=h.api.value, kind=h.kind, description=h.description)
            for h in profile.hard_deny
        ],
    )


@router.get("/cli-profiles", summary="List CLI profiles")
async def list_cli_profiles(
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: CliProfileService = Depends(get_cli_profile_service),
) -> CliProfileListResponse:
    """The vendor CLI profiles this Jentic ships.

    A profile declares a vendor CLI's APIs (with the `jentic connect` key of
    each), its rule presets (for `jentic connect --preset`) and the hard-deny
    lists no rule can override. Each API says whether the registry serves it
    now; `POST /cli-profiles/{profile}:provision` makes them all live.
    """
    data: list[CliProfileResponse] = []
    for profile, enabled in svc.list_profiles():
        data.append(_profile_response(profile, enabled, await svc.availability(profile)))
    return CliProfileListResponse(data=data)


@router.post(
    "/cli-profiles/{profile}:provision",
    summary="Provision a CLI profile's APIs",
    responses=with_responses(not_found("No shipped CLI profile has this name")),
)
async def provision_cli_profile(
    profile: Annotated[
        str,
        Path(description="Profile name, e.g. `github`.", openapi_examples=ex.example("github")),
    ],
    identity: Identity = get_current_identity(required_permissions=["catalog:import"]),
    svc: CliProfileService = Depends(get_cli_profile_service),
) -> CliProfileProvisionResponse:
    """Make every API the profile declares exist and be live. Idempotent.

    A `catalog` API is imported from the public catalog (enqueued; nothing
    when it is already registered), a `shipped` API is imported from the spec
    shipped with Jentic and goes live at once, and a `builtin` API needs
    nothing. A connect for one of the profile's APIs runs the same
    provisioning on demand, so this is for an operator who wants the APIs
    in place before any agent connects. Runs whether or not the vendor's
    facade is enabled (`vendor_enabled`).
    """
    result = await svc.provision(profile, identity)
    return CliProfileProvisionResponse(
        profile=result.profile,
        vendor=result.vendor,
        vendor_enabled=result.vendor_enabled,
        apis=[
            CliProfileProvisionedApiResponse(
                role=a.role,  # type: ignore[arg-type]  # an ApiRole value
                source=a.source,
                api_id=a.api_id,
                vendor=a.vendor,
                name=a.name,
                version=a.version,
                status=a.status,
                detail=a.detail,
                job_id=a.job_id,
            )
            for a in result.apis
        ],
    )
