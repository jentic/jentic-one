"""Provider config service — validate, encrypt, persist, and activate at runtime."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from jentic_one.admin.core.schema.provider_configs import ProviderConfigRecord
from jentic_one.admin.repos import ProviderConfigRepository
from jentic_one.admin.services.errors import InvalidInputError, NotFoundError
from jentic_one.admin.services.schemas.provider_configs import ProviderConfigView
from jentic_one.shared.audit import record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import PipedreamProviderConfig
from jentic_one.shared.context import Context
from jentic_one.shared.crypto import DecryptionError
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

# Secret fields per provider that must be encrypted at rest and redacted on read.
_SECRET_FIELDS = ("client_secret",)
_REDACTED = "***"


class ProviderConfigNotFoundError(NotFoundError):
    """Raised when a provider config does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Provider config '{name}' not found")
        self.name = name


def _validate_by_name(name: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Validate provider-specific fields by name into a normalized config dict.

    The returned dict includes the discriminator ``kind`` and the plaintext
    secret (encryption happens in the caller). Unknown provider names are a
    client error.
    """
    if name == "pipedream":
        try:
            model = PipedreamProviderConfig.model_validate({**fields, "kind": "pipedream"})
        except ValidationError as exc:
            raise InvalidInputError(f"invalid pipedream config: {exc}") from exc
        return {
            "kind": "pipedream",
            "project_id": model.project_id,
            "client_id": model.client_id,
            "client_secret": model.client_secret.get_secret_value(),
            "environment": model.environment,
            "connect_base_url": model.connect_base_url,
            "expiry_skew_seconds": model.expiry_skew_seconds,
        }
    raise InvalidInputError(f"unknown provider '{name}'")


def _redact(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a stored config with secret fields masked."""
    result = dict(config)
    for field in _SECRET_FIELDS:
        if result.get(field):
            result[field] = _REDACTED
    return result


def _to_view(record: ProviderConfigRecord) -> ProviderConfigView:
    return ProviderConfigView(
        name=record.name,
        config=_redact(record.config_json),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class ProviderConfigService:
    """Manages runtime credential provider configuration."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def set(
        self, name: str, fields: dict[str, Any], *, identity: Identity
    ) -> ProviderConfigView:
        """Validate, encrypt, persist, and activate a provider config.

        Read-modify-merge: caller-supplied fields are overlaid on the existing
        stored config (decrypted first), so a partial update that omits a secret
        (e.g. ``client_secret``) preserves the previously stored value rather
        than clearing it. Empty/None values are treated as "not supplied" and
        never clobber an existing field.
        """
        async with self._ctx.admin_db.session() as session:
            existing = await ProviderConfigRepository.get(session, name)

        base: dict[str, Any] = {}
        if existing is not None:
            try:
                base = self._ctx.decrypt_provider_config(existing.config_json)
            except DecryptionError:
                # The stored secret was encrypted under a lost/rotated key —
                # keep the plaintext (non-secret) fields so the merge still
                # works, drop the undecryptable secret, and let validation
                # demand a fresh one. A re-apply heals; a 500 dead-ends.
                base = {
                    k: v
                    for k, v in existing.config_json.items()
                    if k not in _SECRET_FIELDS
                }
            base.pop("kind", None)

        merged = dict(base)
        for key, value in fields.items():
            if value is None or value == "":
                continue
            merged[key] = value

        try:
            validated = _validate_by_name(name, merged)
        except InvalidInputError:
            # Sharpen the classic case: reconfigure with a blank secret after
            # the stored one became undecryptable.
            if existing is not None and not merged.get("client_secret"):
                raise InvalidInputError(
                    f"the stored {name} secret can no longer be decrypted "
                    "(encryption key changed) — re-enter the client secret"
                ) from None
            raise

        # Encrypt secret fields before persisting; plaintext never lands in the DB.
        stored = dict(validated)
        for field in _SECRET_FIELDS:
            value = stored.get(field)
            if isinstance(value, str) and value:
                stored[field] = self._ctx.encryption.encrypt(value)

        async with self._ctx.admin_db.transaction() as session:
            record = await ProviderConfigRepository.upsert(
                session,
                name=name,
                config_json=stored,
                created_by=identity.sub,
            )
            await record_audit(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.PROVIDER_CONFIG,
                target_id=name,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"name": name, "config": _redact(stored)},
                origin=identity.origin.value,
            )
            view = _to_view(record)

        # Rebuild the in-process provider registry so the change takes effect
        # without a restart. NOTE: single-process only — see refresh_providers().
        await self._ctx.refresh_providers()
        return view

    async def get(self, name: str) -> ProviderConfigView:
        async with self._ctx.admin_db.session() as session:
            record = await ProviderConfigRepository.get(session, name)
        if record is None:
            raise ProviderConfigNotFoundError(name)
        return _to_view(record)

    async def list_all(self) -> list[ProviderConfigView]:
        async with self._ctx.admin_db.session() as session:
            records = await ProviderConfigRepository.list_all(session)
        return [_to_view(r) for r in records]
