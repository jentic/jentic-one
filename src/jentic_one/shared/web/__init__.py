"""Shared web utilities: dependencies, app factories, auth helpers."""

from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.auth import extract_bearer_token, extract_credential
from jentic_one.shared.web.deps import (
    effective_permissions,
    get_ctx,
    get_current_identity,
    resolve_identity,
)
from jentic_one.shared.web.errors import make_service_error_handler
from jentic_one.shared.web.health import make_health_router
from jentic_one.shared.web.links import build_link

__all__ = [
    "build_link",
    "create_combined_app",
    "effective_permissions",
    "extract_bearer_token",
    "extract_credential",
    "get_ctx",
    "get_current_identity",
    "make_health_router",
    "make_service_error_handler",
    "resolve_identity",
]
