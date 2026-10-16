"""Web request/response models for the OAuth app registration admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.control.services.oauth_app_registrations.schemas import (
    OAuthAppRegistrationFlowKind,
)
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.sensitive import SENSITIVE

# ``<domain>/<sub>`` — the same shape ``VendorAuthConfig.vendor`` enforces, so
# a registration can never project into a vendor config that fails to load.
_CATALOG_API_ID_PATTERN = r"^[^/].*/.*[^/]$"

# Generous for any vendor's client secret, but bounded so a request can't
# make us seal and store an arbitrarily large blob.
_CLIENT_SECRET_MAX_LENGTH = 4096

# RFC 6749 §3.3 scope-token: non-empty, no whitespace.
ScopeToken = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^\S+$")]


class OAuthAppRegistrationResponse(BaseModel):
    """Registration as returned to admins.

    Never includes the client secret — only ``has_client_secret`` +
    ``secret_last_rotated_at`` so an admin can see rotation history without
    being able to read the material.
    """

    id: str = Field(
        description="Unique ID of the OAuth app registration.",
        examples=[ex.OAUTH_APP_REGISTRATION_ID],
    )
    name: str = Field(
        description="Admin-chosen name of the registration.", examples=["MyOrg GitHub"]
    )
    api_vendor: str = Field(
        description="Vendor registry key the registration connects.", examples=[ex.VENDOR_KEY]
    )
    catalog_api_id: str = Field(
        examples=["github.com/api.github.com"],
        description=(
            "Catalog API slug this OAuth app targets (e.g. 'googleapis-com/gmail'). "
            "Feeds credential.catalog_api_id at connect time so the operations "
            "preview resolves against a real registered API."
        ),
    )
    display_name: str = Field(
        examples=["GitHub"],
        description=(
            "Vendor family label ('Gmail'), shown alongside the admin's per-registration "
            "'name' on the picker card."
        ),
    )
    flow_kind: OAuthAppRegistrationFlowKind = Field(examples=["authorization_code"])
    client_id: str = Field(description="OAuth client ID of the app.", examples=["Iv1.a1b2c3d4"])
    is_active: bool = Field(description="Whether the registration is active.", examples=[True])
    has_client_secret: bool = Field(
        description="Whether a client secret is stored.", examples=[True]
    )
    secret_last_rotated_at: datetime | None = Field(
        description="When the client secret was last rotated (RFC 3339); null if never rotated.",
        examples=["2026-01-20T14:05:00Z"],
    )
    authorize_url: str | None = Field(
        default=None,
        description="Authorization endpoint URL; null for device-authorization registrations.",
        examples=["https://github.com/login/oauth/authorize"],
    )
    token_url: str | None = Field(
        default=None,
        description="Token endpoint URL; null for device-authorization registrations.",
        examples=["https://github.com/login/oauth/access_token"],
    )
    authorization_endpoint: str | None = Field(
        default=None,
        description="Device authorization endpoint URL; null for authorization-code registrations.",
        examples=["https://github.com/login/device/code"],
    )
    token_endpoint: str | None = Field(
        default=None,
        description="Token endpoint URL; null for authorization-code registrations.",
        examples=["https://github.com/login/oauth/access_token"],
    )
    default_scopes: list[str] | None = Field(
        default=None,
        description="Scopes requested by default; null if none are set.",
        examples=[["repo", "read:user"]],
    )
    created_at: datetime = Field(
        description="When the registration was created (RFC 3339).",
        examples=["2026-01-15T09:30:00Z"],
    )
    updated_at: datetime = Field(
        description="When the registration was last updated (RFC 3339).",
        examples=["2026-01-20T14:05:00Z"],
    )
    created_by: str | None = Field(
        description="ID of the actor that created the registration.", examples=[ex.USER_ID]
    )
    dependent_credential_count: int = Field(
        description="Number of credentials minted through this registration.", examples=[4]
    )


class OAuthAppRegistrationListResponse(BaseModel):
    """Paginated list of OAuth app registrations."""

    data: list[OAuthAppRegistrationResponse] = Field(
        description="The page of OAuth app registrations."
    )
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )


class AuthorizationCodeRegistrationCreateRequest(BaseModel):
    """Create request for an authorization-code registration."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "MyOrg GitHub",
                    "api_vendor": "github",
                    "catalog_api_id": "github.com/api.github.com",
                    "display_name": "GitHub",
                    "flow_kind": "authorization_code",
                    "client_id": "Iv1.a1b2c3d4",
                    "client_secret": "<paste-client-secret-from-vendor-console>",
                    "authorize_url": "https://github.com/login/oauth/authorize",
                    "token_url": "https://github.com/login/oauth/access_token",
                    "default_scopes": ["repo", "read:user"],
                }
            ]
        },
    )

    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="Admin-chosen name of the registration.",
            examples=["MyOrg GitHub"],
        ),
    ]
    api_vendor: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Vendor registry key the registration connects.",
            examples=[ex.VENDOR_KEY],
        ),
    ]
    catalog_api_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            pattern=_CATALOG_API_ID_PATTERN,
            examples=["github.com/api.github.com"],
            description=(
                "Catalog API slug this OAuth app targets (e.g. 'github.com/api.github.com'). "
                "The credential minted through this registration carries the same slug "
                "so its operations preview resolves against a real registered API."
            ),
        ),
    ]
    display_name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="Vendor family label ('GitHub'), shown on picker cards.",
            examples=["GitHub"],
        ),
    ]
    flow_kind: Literal[OAuthAppRegistrationFlowKind.AUTHORIZATION_CODE] = Field(
        description="OAuth flow the registration uses; always `authorization_code`.",
        examples=["authorization_code"],
    )
    client_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="OAuth client ID of the app.",
            examples=["Iv1.a1b2c3d4"],
        ),
    ]
    client_secret: Annotated[
        str,
        Field(
            min_length=1,
            max_length=_CLIENT_SECRET_MAX_LENGTH,
            json_schema_extra=SENSITIVE,
            description="OAuth client secret of the app; stored encrypted and never returned.",
            examples=["example-secret"],  # pragma: allowlist secret
        ),
    ]
    authorize_url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2048,
            description="Authorization endpoint URL.",
            examples=["https://github.com/login/oauth/authorize"],
        ),
    ]
    token_url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2048,
            description="Token endpoint URL.",
            examples=["https://github.com/login/oauth/access_token"],
        ),
    ]
    default_scopes: list[ScopeToken] | None = Field(
        default=None, description="Scopes requested by default.", examples=[["repo", "read:user"]]
    )


class DeviceAuthorizationRegistrationCreateRequest(BaseModel):
    """Create request for a device-authorization registration."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "MyOrg GitHub (device flow)",
                    "api_vendor": "github",
                    "catalog_api_id": "github.com/api.github.com",
                    "display_name": "GitHub",
                    "flow_kind": "device_authorization",
                    "client_id": "Iv1.a1b2c3d4",
                    "authorization_endpoint": "https://github.com/login/device/code",
                    "token_endpoint": "https://github.com/login/oauth/access_token",
                    "default_scopes": ["repo"],
                }
            ]
        },
    )

    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="Admin-chosen name of the registration.",
            examples=["MyOrg GitHub"],
        ),
    ]
    api_vendor: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Vendor registry key the registration connects.",
            examples=[ex.VENDOR_KEY],
        ),
    ]
    catalog_api_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            pattern=_CATALOG_API_ID_PATTERN,
            examples=["github.com/api.github.com"],
            description=(
                "Catalog API slug this OAuth app targets (e.g. 'github.com/api.github.com')."
            ),
        ),
    ]
    display_name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="Vendor family label ('GitHub'), shown on picker cards.",
            examples=["GitHub"],
        ),
    ]
    flow_kind: Literal[OAuthAppRegistrationFlowKind.DEVICE_AUTHORIZATION] = Field(
        description="OAuth flow the registration uses; always `device_authorization`.",
        examples=["device_authorization"],
    )
    client_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="OAuth client ID of the app.",
            examples=["Iv1.a1b2c3d4"],
        ),
    ]
    authorization_endpoint: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2048,
            description="Device authorization endpoint URL.",
            examples=["https://github.com/login/device/code"],
        ),
    ]
    token_endpoint: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2048,
            description="Token endpoint URL.",
            examples=["https://github.com/login/oauth/access_token"],
        ),
    ]
    default_scopes: list[ScopeToken] | None = Field(
        default=None, description="Scopes requested by default.", examples=[["repo"]]
    )


# Discriminated on ``flow_kind``: the spec gets a ``oneOf`` with a
# discriminator, and a bad body fails with one error for its own flow instead
# of one per union member.
OAuthAppRegistrationCreateRequest = Annotated[
    AuthorizationCodeRegistrationCreateRequest | DeviceAuthorizationRegistrationCreateRequest,
    Field(discriminator="flow_kind"),
]


class OAuthAppRegistrationUpdateRequest(BaseModel):
    """Partial update — fields not present are left untouched."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)] | None = Field(
        default=None,
        description="New registration name; omit to leave unchanged.",
        examples=["MyOrg GitHub"],
    )
    display_name: Annotated[str, Field(min_length=1, max_length=255)] | None = Field(
        default=None,
        description="New vendor family label; omit to leave unchanged.",
        examples=["GitHub"],
    )
    is_active: bool | None = Field(
        default=None,
        description="Activate or deactivate the registration; omit to leave unchanged.",
        examples=[True],
    )
    default_scopes: list[ScopeToken] | None = Field(
        default=None,
        description="Replacement default scopes; omit to leave unchanged.",
        examples=[["repo", "read:user"]],
    )
    authorize_url: Annotated[str, Field(min_length=1, max_length=2048)] | None = Field(
        default=None,
        description="New authorization endpoint URL; authorization-code registrations only.",
        examples=["https://github.com/login/oauth/authorize"],
    )
    token_url: Annotated[str, Field(min_length=1, max_length=2048)] | None = Field(
        default=None,
        description="New token endpoint URL; authorization-code registrations only.",
        examples=["https://github.com/login/oauth/access_token"],
    )
    authorization_endpoint: Annotated[str, Field(min_length=1, max_length=2048)] | None = Field(
        default=None,
        description=(
            "New device authorization endpoint URL; device-authorization registrations only."
        ),
        examples=["https://github.com/login/device/code"],
    )
    token_endpoint: Annotated[str, Field(min_length=1, max_length=2048)] | None = Field(
        default=None,
        description="New token endpoint URL; device-authorization registrations only.",
        examples=["https://github.com/login/oauth/access_token"],
    )


class OAuthAppRegistrationRotateSecretRequest(BaseModel):
    """Rotate the client secret on an auth-code registration."""

    model_config = ConfigDict(extra="forbid")

    client_secret: Annotated[
        str,
        Field(
            min_length=1,
            max_length=_CLIENT_SECRET_MAX_LENGTH,
            json_schema_extra=SENSITIVE,
            description="New OAuth client secret; stored encrypted and never returned.",
            examples=["example-secret"],  # pragma: allowlist secret
        ),
    ]
