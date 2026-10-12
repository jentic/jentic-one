"""Handlers for connect sessions a human completes by entering a credential.

``manual_api_key``, ``manual_bearer`` and ``manual_basic`` serve connect
sessions that target a registry API whose spec declares an API-key, bearer or
basic scheme. There is no vendor conversation: at ``:confirm`` the approver
enters the secret and the handler writes it into the session's pending
credential through the shared typed-secret writers, inside the caller's
transaction. One kind per stored type, because ``stored_type`` fixes the
credential's type when the session is created.

These handlers are deliberately not in ``handler_for``: they have no
``begin`` / ``on_finalise`` and are never driven by the OAuth paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import SecretStr

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.services.credentials.typed_secrets import (
    write_api_key_secret,
    write_basic_secret,
    write_bearer_token_secret,
)
from jentic_one.shared.crypto.encryption import EncryptionService
from jentic_one.shared.models.credentials import StoredCredentialType

#: ``credential.provider`` for a human-entered static secret.
_PROVIDER_ID = "static"


@dataclass(frozen=True, slots=True)
class ApiKeySecret:
    """The API key an approver entered."""

    key: SecretStr


@dataclass(frozen=True, slots=True)
class BearerSecret:
    """The bearer token an approver entered."""

    token: SecretStr


@dataclass(frozen=True, slots=True)
class BasicSecret:
    """The basic-auth username and password an approver entered."""

    username: str
    password: SecretStr


ManualSecret = ApiKeySecret | BearerSecret | BasicSecret


class ManualSecretMismatchError(Exception):
    """The entered secret is not the shape the session's flow stores."""


class ManualFlowHandler:
    """Shared shape: no OAuth app, a typed secret written at confirm."""

    #: ``connect_sessions.resolved_flow`` value.
    kind: ClassVar[str]
    #: Declared-scheme kind this flow serves (``api_key`` / ``bearer`` / ``basic``).
    scheme_kind: ClassVar[str]
    stored_type: ClassVar[StoredCredentialType]
    provider_id: ClassVar[str] = _PROVIDER_ID

    async def write_secret(
        self,
        db_session: Any,
        encryption: EncryptionService,
        *,
        row: ConnectSession,
        secret: ManualSecret,
        created_by: str,
    ) -> None:
        raise NotImplementedError


class ManualApiKeyHandler(ManualFlowHandler):
    kind: ClassVar[str] = "manual_api_key"
    scheme_kind: ClassVar[str] = "api_key"
    stored_type: ClassVar[StoredCredentialType] = StoredCredentialType.API_KEY

    async def write_secret(
        self,
        db_session: Any,
        encryption: EncryptionService,
        *,
        row: ConnectSession,
        secret: ManualSecret,
        created_by: str,
    ) -> None:
        if not isinstance(secret, ApiKeySecret):
            raise ManualSecretMismatchError(self.kind)
        # Injection point comes from the scheme snapshot taken at ``:connect``
        # (the spec), never from the approver.
        assert row.scheme_location is not None and row.scheme_field_name is not None
        await write_api_key_secret(
            db_session,
            encryption,
            credential_id=row.credential_id,
            key=secret.key,
            location=row.scheme_location,
            field_name=row.scheme_field_name,
            created_by=created_by,
        )


class ManualBearerHandler(ManualFlowHandler):
    kind: ClassVar[str] = "manual_bearer"
    scheme_kind: ClassVar[str] = "bearer"
    stored_type: ClassVar[StoredCredentialType] = StoredCredentialType.STATIC_BEARER_TOKEN

    async def write_secret(
        self,
        db_session: Any,
        encryption: EncryptionService,
        *,
        row: ConnectSession,
        secret: ManualSecret,
        created_by: str,
    ) -> None:
        if not isinstance(secret, BearerSecret):
            raise ManualSecretMismatchError(self.kind)
        await write_bearer_token_secret(
            db_session,
            encryption,
            credential_id=row.credential_id,
            token=secret.token,
            created_by=created_by,
        )


class ManualBasicHandler(ManualFlowHandler):
    kind: ClassVar[str] = "manual_basic"
    scheme_kind: ClassVar[str] = "basic"
    stored_type: ClassVar[StoredCredentialType] = StoredCredentialType.BASIC_AUTH

    async def write_secret(
        self,
        db_session: Any,
        encryption: EncryptionService,
        *,
        row: ConnectSession,
        secret: ManualSecret,
        created_by: str,
    ) -> None:
        if not isinstance(secret, BasicSecret):
            raise ManualSecretMismatchError(self.kind)
        await write_basic_secret(
            db_session,
            encryption,
            credential_id=row.credential_id,
            username=secret.username,
            password=secret.password,
            created_by=created_by,
        )


_MANUAL_HANDLERS: dict[str, ManualFlowHandler] = {
    h.kind: h for h in (ManualApiKeyHandler(), ManualBearerHandler(), ManualBasicHandler())
}

#: Every ``manual_*`` flow kind.
MANUAL_FLOW_KINDS: frozenset[str] = frozenset(_MANUAL_HANDLERS)


def manual_handler_for(kind: str) -> ManualFlowHandler | None:
    """The manual handler for a ``resolved_flow``, or ``None`` when it is not a manual flow."""
    return _MANUAL_HANDLERS.get(kind)


def manual_handler_for_scheme(scheme_kind: str) -> ManualFlowHandler | None:
    """The manual handler serving a declared-scheme kind (``api_key`` / ``bearer`` / ``basic``)."""
    for handler in _MANUAL_HANDLERS.values():
        if handler.scheme_kind == scheme_kind:
            return handler
    return None
