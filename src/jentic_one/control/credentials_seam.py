"""Public credentials seam for in-process extensions.

An extension that runs in the same process as the control surface or the
broker uses this module to read stored credentials and to keep credentials of
its own ("managed" credentials). It is the only supported way for extension
code to touch credentials: everything an extension needs is exported here, so
it imports nothing internal from ``jentic_one.control``.

**``resolve_credential_material`` performs no authorization.** It returns the
decrypted secret of any active, connected credential to whoever calls it. The
extension must decide that the caller may use the credential *before* calling
it. It is a Python API only: no route exposes it.

Managed credentials
-------------------
``create_managed_credential`` stores a credential with a ``ManagedBy`` marker
(the kind and id of the extension object that owns it). The marker is set at
creation and never changes; no HTTP request can set it. A managed credential:

- cannot be bound to an agent (409 ``credential_managed``) and is never offered
  as an existing credential in a connect session;
- is never resolved by the broker for an agent call, even when named by
  ``Jentic-Credential-Id``;
- returns 409 ``credential_managed`` on ``PATCH``/``DELETE /credentials/{id}``,
  except that an ``org:admin`` may delete it;
- is changed or deleted only through ``update_managed_credential`` and
  ``delete_managed_credential`` with the same marker.

Create, update and delete run through the same service as the HTTP API, so
validation, owner scoping (``created_by`` is the identity passed in), audit
records (with ``*_rotated`` markers on a secret rotation) and broker cache
invalidation are identical. The seam never caches or logs secret material.

See ``docs/development/extending-jentic-one.md`` (Managed credentials).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.services.credentials.errors import (
    CredentialManagedError,
    CredentialNotFoundError,
    InvalidCredentialInputError,
)
from jentic_one.control.services.credentials.mapping import to_wire
from jentic_one.control.services.credentials.schemas.credentials import (
    CredentialCreate,
    CredentialUpdate,
    ManagedBy,
    managed_by_of,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.aws.sigv4 import SigV4Material, sign_request
from jentic_one.shared.context import Context
from jentic_one.shared.crypto.encryption import DecryptionError
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType

__all__ = [
    "APIReference",
    "ApiKeyMaterial",
    "Credential",
    "CredentialCreate",
    "CredentialHeader",
    "CredentialManagedError",
    "CredentialNotFoundError",
    "CredentialType",
    "CredentialUnavailableError",
    "CredentialUpdate",
    "InvalidCredentialInputError",
    "ManagedBy",
    "SigV4Material",
    "create_managed_credential",
    "delete_managed_credential",
    "get_credential_header",
    "resolve_credential_material",
    "sign_request",
    "update_managed_credential",
]

# The credential types ``resolve_credential_material`` can return material for.
_MATERIAL_TYPES = frozenset({CredentialType.API_KEY, CredentialType.SIGV4})


@dataclass(frozen=True, slots=True)
class CredentialHeader:
    """A credential's non-secret header. Never carries secret material.

    ``revision`` is the row's ``updated_at`` (``created_at`` when it was never
    updated): it moves on every persisted change, including a secret rotation,
    so it can key a cache of anything derived from the secret.
    """

    id: str
    type: str
    name: str
    api_vendor: str
    created_by: str | None
    active: bool
    state: str
    managed_by: ManagedBy | None
    revision: datetime


@dataclass(frozen=True, slots=True)
class ApiKeyMaterial:
    """A decrypted API key and where the broker would inject it.

    Carries **decrypted** secret material: never log or persist it.
    ``__repr__`` elides the key, like ``SigV4Material``.
    """

    key: str
    field_name: str
    location: str

    def __repr__(self) -> str:
        return (
            f"ApiKeyMaterial(key='***', field_name={self.field_name!r}, location={self.location!r})"
        )


class CredentialUnavailableError(Exception):
    """The credential cannot supply material: it is missing, inactive, not
    connected, of another type than expected, or its secret failed to decrypt.

    The message names the credential id and the reason, never secret material.
    """

    def __init__(self, credential_id: str, reason: str) -> None:
        super().__init__(f"Credential '{credential_id}' is unavailable: {reason}")
        self.credential_id = credential_id
        self.reason = reason


def _header(credential: Credential) -> CredentialHeader:
    return CredentialHeader(
        id=credential.id,
        type=to_wire(StoredCredentialType(credential.type)).value,
        name=credential.name,
        api_vendor=credential.api_vendor,
        created_by=credential.created_by,
        active=credential.active,
        state=credential.state,
        managed_by=managed_by_of(credential.managed_by_kind, credential.managed_by_ref),
        revision=credential.updated_at or credential.created_at,
    )


async def get_credential_header(ctx: Context, credential_id: str) -> CredentialHeader | None:
    """The credential's header, or ``None`` when no such credential exists.

    No identity check: the caller decides who may learn about the credential.
    """
    async with ctx.control_db.read_session() as session:
        credential = await CredentialRepository.get_header(session, credential_id)
        return _header(credential) if credential is not None else None


async def resolve_credential_material(
    ctx: Context, credential_id: str, *, expected_type: CredentialType
) -> ApiKeyMaterial | SigV4Material:
    """Decrypt the credential's secret for in-process use.

    **No authorization is performed: authorize the use before calling.**

    Applies the broker's own filter (the credential must be ``active`` and
    ``connected``) and checks its type against ``expected_type``
    (``CredentialType.API_KEY`` or ``CredentialType.SIGV4``). The plaintext is
    returned to the caller only: it is not cached or logged here.

    Raises ``CredentialUnavailableError`` when the credential is missing,
    inactive, not connected, of another type, has no stored secret, or fails to
    decrypt. Raises ``ValueError`` for an ``expected_type`` this function does
    not supply material for.
    """
    if expected_type not in _MATERIAL_TYPES:
        raise ValueError(f"No material is resolved for credential type '{expected_type}'")
    async with ctx.control_db.read_session() as session:
        credential = await CredentialRepository.get_header(session, credential_id)
        if credential is None:
            raise CredentialUnavailableError(credential_id, "not found")
        if not credential.active:
            raise CredentialUnavailableError(credential_id, "inactive")
        if credential.state != "connected":
            raise CredentialUnavailableError(credential_id, "not connected")
        if to_wire(StoredCredentialType(credential.type)) != expected_type:
            raise CredentialUnavailableError(credential_id, f"not a {expected_type} credential")
        await CredentialRepository.load_material(session, [credential])
        try:
            if expected_type == CredentialType.API_KEY:
                cak = credential.customer_api_key
                if cak is None:
                    raise CredentialUnavailableError(credential_id, "no stored key")
                return ApiKeyMaterial(
                    key=ctx.encryption.decrypt(cak.encrypted_key),
                    field_name=cak.field_name,
                    location=cak.location,
                )
            sig = credential.sigv4_credential
            if sig is None:
                raise CredentialUnavailableError(credential_id, "no stored keys")
            return SigV4Material(
                access_key_id=sig.access_key_id,
                secret_access_key=ctx.encryption.decrypt(sig.encrypted_secret_access_key),
                region=sig.region,
                service=sig.service,
                session_token=(
                    ctx.encryption.decrypt(sig.encrypted_session_token)
                    if sig.encrypted_session_token
                    else None
                ),
            )
        except DecryptionError:
            # ``from None``: the decryption error carries nothing the caller
            # needs, and the chain must not drag ciphertext context along.
            raise CredentialUnavailableError(credential_id, "decryption failed") from None


async def _require_header(ctx: Context, credential_id: str) -> CredentialHeader:
    header = await get_credential_header(ctx, credential_id)
    if header is None:
        raise CredentialNotFoundError(credential_id)
    return header


async def create_managed_credential(
    ctx: Context, payload: CredentialCreate, *, identity: Identity, managed_by: ManagedBy
) -> CredentialHeader:
    """Create a credential managed by ``managed_by``, owned by ``identity``.

    Runs the same validation, audit and event path as ``POST /credentials``.
    Raises ``InvalidCredentialInputError`` (and the service's other errors)
    for an invalid payload.
    """
    view = await CredentialService(ctx).create(payload, identity=identity, managed_by=managed_by)
    return await _require_header(ctx, view.credential_id)


async def update_managed_credential(
    ctx: Context,
    credential_id: str,
    payload: CredentialUpdate,
    *,
    identity: Identity,
    managed_by: ManagedBy,
) -> CredentialHeader:
    """Update or rotate a credential this extension manages.

    ``managed_by`` must be the credential's own marker, else
    ``CredentialManagedError``. ``identity`` must be able to write the
    credential (its owner, or ``org:admin``), else ``CredentialNotFoundError``.
    A secret rotation is audited with ``*_rotated`` markers and invalidates the
    broker's caches, as ``PATCH /credentials/{id}`` does.
    """
    await CredentialService(ctx).update(
        credential_id, payload, identity=identity, managed_by=managed_by
    )
    return await _require_header(ctx, credential_id)


async def delete_managed_credential(
    ctx: Context, credential_id: str, *, identity: Identity, managed_by: ManagedBy
) -> bool:
    """Delete a credential this extension manages.

    Returns ``False`` when no credential with this id is visible to
    ``identity`` for writing (already gone, or not theirs), ``True`` once it
    is deleted. Raises ``CredentialManagedError`` when the credential does not
    carry ``managed_by``.
    """
    try:
        await CredentialService(ctx).delete(credential_id, identity=identity, managed_by=managed_by)
    except CredentialNotFoundError:
        return False
    return True
