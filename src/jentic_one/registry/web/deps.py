"""FastAPI dependencies for the registry web layer."""

from __future__ import annotations

from fastapi import Depends

from jentic_one.registry.services.api_service import ApiService
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.governed_hosts_service import GovernedHostsService
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_ctx


def get_api_service(ctx: Context = Depends(get_ctx)) -> ApiService:
    return ApiService(ctx)


def get_catalog_service(ctx: Context = Depends(get_ctx)) -> CatalogService:
    return CatalogService(ctx)


def get_governed_hosts_service(ctx: Context = Depends(get_ctx)) -> GovernedHostsService:
    return GovernedHostsService(ctx)
