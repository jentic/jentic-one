"""Config router — runtime, DB-backed platform configuration (provider configs)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from jentic_one.admin.services.provider_config_service import ProviderConfigService
from jentic_one.admin.services.schemas.provider_configs import ProviderConfigView
from jentic_one.admin.web.deps import get_provider_config_service
from jentic_one.admin.web.schemas.provider_configs import (
    ProviderConfigListResponse,
    ProviderConfigResponse,
    ProviderConfigSetRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.openapi_responses import not_found

router = APIRouter()


def _to_response(view: ProviderConfigView) -> ProviderConfigResponse:
    return ProviderConfigResponse(
        name=view.name,
        config=view.config,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.put("/admin/config/providers/{name}", summary="Set a credential provider config")
async def set_provider_config(
    name: Annotated[str, Path(openapi_examples=ex.example(ex.PROVIDER_NAME))],
    body: ProviderConfigSetRequest,
    identity: Identity = get_current_identity(required_permissions=["config:write"]),
    svc: ProviderConfigService = Depends(get_provider_config_service),
) -> ProviderConfigResponse:
    """Set (create or update) a credential provider config at runtime.

    The payload is validated by provider name (e.g. ``pipedream``). Secret fields
    are encrypted at rest. A successful write rebuilds the in-process provider
    registry so the change takes effect without a restart. The response redacts
    secret fields.
    """
    view = await svc.set(name, body.config, identity=identity)
    return _to_response(view)


@router.get(
    "/admin/config/providers/{name}",
    summary="Get a credential provider config",
    responses=not_found(),
)
async def get_provider_config(
    name: Annotated[str, Path(openapi_examples=ex.example(ex.PROVIDER_NAME))],
    identity: Identity = get_current_identity(required_permissions=["config:read"]),
    svc: ProviderConfigService = Depends(get_provider_config_service),
) -> ProviderConfigResponse:
    """Get a stored provider config by name, with secret fields redacted."""
    view = await svc.get(name)
    return _to_response(view)


@router.get("/admin/config/providers", summary="List credential provider configs")
async def list_provider_configs(
    identity: Identity = get_current_identity(required_permissions=["config:read"]),
    svc: ProviderConfigService = Depends(get_provider_config_service),
) -> ProviderConfigListResponse:
    """List all stored provider configs, with secret fields redacted."""
    views = await svc.list_all()
    return ProviderConfigListResponse(data=[_to_response(v) for v in views])
