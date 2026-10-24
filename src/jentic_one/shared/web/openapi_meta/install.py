"""Assemble the generated OpenAPI document from the package's parts.

:func:`fastapi_metadata_kwargs` feeds the ``FastAPI(...)`` constructor, and
:func:`install_openapi_metadata` wraps ``app.openapi()`` to layer the static
metadata, security, response normalisation and examples onto it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from jentic_one.shared.web.endpoint_permissions import build_operation_auth_map
from jentic_one.shared.web.openapi_meta.content import (
    API_DESCRIPTION,
    API_SUMMARY,
    API_TITLE,
    API_VERSION,
    BEARER_SECURITY_SCHEME,
    CONTACT,
    HTTP_METHODS,
    LICENSE_INFO,
    OAUTH2_SECURITY_SCHEME,
    SERVERS,
    authenticated_security,
)
from jentic_one.shared.web.openapi_meta.examples import (
    add_oneof_discriminators,
    compose_component_examples,
    describe_path_item,
    describe_request_body,
    propagate_examples,
    summarise_parameter_examples,
    summarise_path_item,
)
from jentic_one.shared.web.openapi_meta.operation_ids import (
    NON_BEARER_AUTH_OPERATION_IDS,
    PUBLIC_OPERATION_IDS,
    ROUTER_RESHAPED_422_OPERATION_IDS,
    generate_operation_id,
    resolve_tag,
)
from jentic_one.shared.web.openapi_meta.responses import (
    add_default_problem_response,
    normalise_error_responses,
    stamp_permission_metadata,
)
from jentic_one.shared.web.openapi_meta.tags import OPENAPI_TAGS, X_TAG_GROUPS


def deployment_servers(public_base_url: str = "") -> list[dict[str, str]]:
    """The live document's ``servers``: the deployment's public origin, else ``SERVERS``.

    ``server.public_base_url`` names the origin clients reach the control
    surfaces on, so a spec downloaded from a configured deployment stays
    callable outside the browser. Unset, the same-origin relative server is the
    only honest value.
    """
    if public_base_url:
        return [{"url": public_base_url, "description": "This deployment"}]
    return SERVERS


def fastapi_metadata_kwargs(public_base_url: str = "") -> dict[str, Any]:
    """Keyword arguments to spread into ``FastAPI(...)`` for document metadata."""
    return {
        "title": API_TITLE,
        "summary": API_SUMMARY,
        "description": API_DESCRIPTION,
        "version": API_VERSION,
        "servers": deployment_servers(public_base_url),
        "contact": CONTACT,
        "license_info": LICENSE_INFO,
        "openapi_tags": OPENAPI_TAGS,
        "generate_unique_id_function": generate_operation_id,
    }


def install_openapi_metadata(app: FastAPI) -> None:
    """Install a custom ``openapi()`` that augments FastAPI's generated document.

    FastAPI populates paths, operations, and component schemas from the routes
    and models; this wrapper layers on the static document metadata that cannot
    be inferred: the ``BearerAuth`` security scheme, a global security
    requirement (relaxed for public endpoints), and the Redoc ``x-tagGroups``
    extension. Title/summary/description/tags/servers are passed to the
    ``FastAPI(...)`` constructor and surface through ``get_openapi`` here.
    """

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
            contact=CONTACT,
            license_info=LICENSE_INFO,
        )

        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes.update(BEARER_SECURITY_SCHEME)
        security_schemes.update(OAUTH2_SECURITY_SCHEME)

        schema["security"] = authenticated_security()
        schema["x-tagGroups"] = X_TAG_GROUPS

        operation_auth = build_operation_auth_map(app)

        for path, path_item in schema.get("paths", {}).items():
            resolved = resolve_tag(path)
            for method, operation in path_item.items():
                if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                if resolved is not None:
                    operation["tags"] = [resolved]
                op_id = operation.get("operationId")
                if op_id in PUBLIC_OPERATION_IDS:
                    operation["security"] = []
                    # Public endpoints don't authenticate, so 401/403 don't apply.
                    responses = operation.get("responses", {})
                    for code in ("401", "403"):
                        responses.pop(code, None)
                elif op_id in NON_BEARER_AUTH_OPERATION_IDS:
                    # Authenticates by a non-bearer credential (e.g. an RFC 7592
                    # Registration-Access-Token): drop the platform BearerAuth
                    # requirement but keep the 401 it genuinely returns on a bad
                    # credential. It never reaches the permission/authz layer, so the
                    # 403 (which only the permission gate raises) is dropped.
                    operation["security"] = []
                    operation.get("responses", {}).pop("403", None)
                else:
                    stamp_permission_metadata(method, path, operation, operation_auth)
                if op_id in ROUTER_RESHAPED_422_OPERATION_IDS:
                    # Validation failures are reshaped to the governing spec's
                    # dialect at the router; the framework 422 can never be
                    # returned.
                    operation.get("responses", {}).pop("422", None)
                normalise_error_responses(operation.get("responses", {}))
                add_default_problem_response(operation.get("responses", {}))
                summarise_parameter_examples(operation)
                describe_request_body(operation, components.get("schemas", {}))
            summarise_path_item(path_item)
            describe_path_item(path_item)

        compose_component_examples(components.get("schemas", {}))
        propagate_examples(schema.get("paths", {}), components.get("schemas", {}))
        add_oneof_discriminators(schema, components.get("schemas", {}))

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
