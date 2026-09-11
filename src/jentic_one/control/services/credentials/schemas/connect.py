"""Connect-flow value objects for the credential provider protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    """Inbound connect intent from the caller."""

    scopes: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)


class AuthCodeChallenge(BaseModel):
    """Authorization-code redirect challenge.

    The caller opens ``authorize_url`` in a popup; ``state`` is the signed
    JWT the OAuth callback route verifies.
    """

    kind: Literal["authorization_code"] = "authorization_code"
    authorize_url: str
    state: str


class DeviceAuthorizationChallenge(BaseModel):
    """RFC 8628 device-code challenge.

    The caller shows ``user_code`` and asks the human to enter it at
    ``verification_uri``; the SPA polls ``GET /credentials/{id}`` while
    the ``ConnectPollScanner`` drives completion server-side.
    """

    kind: Literal["device_authorization"] = "device_authorization"
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    poll_interval_seconds: int | None = None


ConnectChallenge = Annotated[
    AuthCodeChallenge | DeviceAuthorizationChallenge,
    Field(discriminator="kind"),
]


class ConnectState(BaseModel):
    """Verified, decoded state payload from the connect flow.

    ``session_id`` is optional and only set when the state was signed by the
    connect-session flow (agent-driven integrations). At callback time its
    presence routes completion to ``ConnectSessionService`` rather than the
    standalone ``ConnectService`` path — one callback URL, two consumers.
    """

    credential_id: str
    provider: str
    actor_id: str | None = None
    actor_type: str | None = None
    issued_at: datetime
    nonce: str
    session_id: str | None = None


class ConnectCallback(BaseModel):
    """Provider-agnostic callback inputs received after user authorization."""

    code: str | None = None
    account_id: str | None = None
    error: str | None = None
    raw: dict[str, str] = Field(default_factory=dict)
