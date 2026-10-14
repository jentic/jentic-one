"""Provider config request/response schemas for the admin web layer.

Generic over provider name: the request carries provider-specific fields as a
free-form object that the service validates by name (e.g. ``pipedream`` maps to
``PipedreamProviderConfig``). Responses redact secret fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REDACTED = "***"


class ProviderConfigSetRequest(BaseModel):
    """Request body for setting a provider config.

    Fields are provider-specific and validated server-side by provider name.
    For ``pipedream`` the recognised fields are ``project_id``, ``client_id``,
    ``client_secret`` (write-only), and optional ``environment``,
    ``connect_base_url``, ``expiry_skew_seconds``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "config": {
                        "project_id": "proj_abc123",
                        "client_id": "client_abc123",
                        "client_secret": "<redacted>",  # pragma: allowlist secret
                        "environment": "production",
                    }
                }
            ]
        }
    )

    config: dict[str, Any] = Field(
        description="Provider-specific configuration fields, validated by provider name.",
    )


class ProviderConfigResponse(BaseModel):
    """A stored provider config with secret fields redacted."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "pipedream",
                    "config": {
                        "kind": "pipedream",
                        "project_id": "proj_abc123",
                        "client_id": "client_abc123",
                        "client_secret": "***",
                        "environment": "production",
                    },
                    "created_at": "2026-06-29T12:00:00Z",
                    "updated_at": "2026-06-29T12:00:00Z",
                }
            ]
        }
    )

    name: str = Field(description="Provider name (e.g. 'pipedream').")
    config: dict[str, Any] = Field(description="Stored config with secret fields redacted.")
    created_at: datetime = Field(description="When the provider config was created (RFC 3339).")
    updated_at: datetime | None = Field(
        default=None,
        description="When the provider config was last updated (RFC 3339); null if never updated.",
    )


class ProviderConfigListResponse(BaseModel):
    """A list of stored provider configs (secrets redacted)."""

    data: list[ProviderConfigResponse] = Field(description="The stored provider configs.")
