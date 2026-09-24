"""Service-layer views for OAuth app registrations.

Kept distinct from the web-layer request/response models: this is the shape
the router projects to the wire, but no HTTP-only concern (aliases, examples)
lives here.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel


class OAuthAppRegistrationFlowKind(StrEnum):
    """Which OAuth flow a registration supports."""

    AUTHORIZATION_CODE = "authorization_code"
    DEVICE_AUTHORIZATION = "device_authorization"


class OAuthAppRegistrationView(BaseModel):
    """Registration view exposed to admins.

    Never carries the client secret — only a ``has_client_secret`` flag and
    the last-rotated timestamp. The write API keeps the secret write-only.
    """

    id: str
    name: str
    api_vendor: str
    flow_kind: OAuthAppRegistrationFlowKind
    client_id: str
    is_active: bool
    has_client_secret: bool
    secret_last_rotated_at: dt.datetime | None
    authorize_url: str | None
    token_url: str | None
    authorization_endpoint: str | None
    token_endpoint: str | None
    default_scopes: list[str] | None
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: str | None
    dependent_credential_count: int
