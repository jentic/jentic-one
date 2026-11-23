"""Integration tests for ProviderConfigService against a real database.

Covers the security-sensitive round-trip the issue-638 plan called for:
encrypt-at-rest, redaction on read, read-modify-merge that preserves an
omitted secret, audit emission, and that a write refreshes the in-process
provider registry so ``providers.get(...)`` resolves the new config. Runs on
both backends (Postgres object / SQLite text), exercising the
``provider_config_store._decode`` dialect branch end to end.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
from sqlalchemy import text

from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.provider_config_service import (
    ProviderConfigNotFoundError,
    ProviderConfigService,
)
from jentic_one.control.services.credentials.providers import PipedreamProvider
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditTargetType

pytestmark = pytest.mark.integration

_PLAINTEXT_SECRET = "pd-client-secret-value"  # pragma: allowlist secret
_ROTATED_SECRET = "pd-rotated-secret-value"  # pragma: allowlist secret


def _as_dict(raw: Any) -> dict[str, Any]:
    """Normalize a JSON column (PG object vs SQLite text) to a dict."""
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    return cast("dict[str, Any]", decoded)


def _identity() -> Identity:
    return Identity(sub="usr_test_operator", email="operator@test.local")


def _pipedream_fields(secret: str | None = _PLAINTEXT_SECRET) -> dict[str, object]:
    fields: dict[str, object] = {
        "project_id": "proj_test",
        "client_id": "client_test",
        "environment": "production",
    }
    if secret is not None:
        fields["client_secret"] = secret
    return fields


async def _stored_config(ctx: Context, name: str) -> dict[str, Any]:
    async with ctx.admin_db.session() as session:
        row = (
            await session.execute(
                text("SELECT config_json FROM provider_configs WHERE name = :n"),
                {"n": name},
            )
        ).first()
    assert row is not None
    return _as_dict(row.config_json)


@pytest.fixture()
async def _clean_provider_configs(
    integration_context: Context,
) -> AsyncGenerator[None, None]:
    """Ensure the provider_configs table is empty around each test."""

    async def _wipe() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(text("DELETE FROM provider_configs"))

    await _wipe()
    yield
    await _wipe()


async def test_set_persists_ciphertext_and_returns_redacted(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    """set() encrypts the secret at rest and never returns plaintext."""
    service = ProviderConfigService(integration_context)

    view = await service.set("pipedream", _pipedream_fields(), identity=_identity())

    # Response is redacted, never the plaintext.
    assert view.config["client_secret"] == "***"
    assert view.config["client_id"] == "client_test"

    # At rest: ciphertext, not the plaintext we supplied.
    stored = await _stored_config(integration_context, "pipedream")
    assert stored["client_secret"] != _PLAINTEXT_SECRET
    assert stored["client_secret"]  # non-empty ciphertext
    # And it decrypts back to the original plaintext.
    assert integration_context.encryption.decrypt(stored["client_secret"]) == _PLAINTEXT_SECRET


async def test_get_returns_redacted_and_missing_raises(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    service = ProviderConfigService(integration_context)
    with pytest.raises(ProviderConfigNotFoundError):
        await service.get("pipedream")

    await service.set("pipedream", _pipedream_fields(), identity=_identity())
    got = await service.get("pipedream")
    assert got.config["client_secret"] == "***"


async def test_set_emits_audit_with_redacted_secret(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    service = ProviderConfigService(integration_context)
    await service.set("pipedream", _pipedream_fields(), identity=_identity())

    async with integration_context.admin_db.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT target_type, target_id, after FROM audit_entries "
                    "WHERE target_id = :n ORDER BY created_at DESC"
                ),
                {"n": "pipedream"},
            )
        ).first()
    assert row is not None
    assert row.target_type == AuditTargetType.PROVIDER_CONFIG.value
    after = _as_dict(row.after)
    assert after["config"]["client_secret"] == "***"


async def test_partial_update_preserves_existing_secret(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    """An update that omits client_secret keeps the previously stored value."""
    service = ProviderConfigService(integration_context)
    await service.set("pipedream", _pipedream_fields(), identity=_identity())

    # Update only client_id, omitting the secret entirely.
    await service.set(
        "pipedream",
        {"project_id": "proj_test", "client_id": "client_rotated"},
        identity=_identity(),
    )

    stored = await _stored_config(integration_context, "pipedream")
    assert stored["client_id"] == "client_rotated"
    # The original secret survives the partial update.
    assert integration_context.encryption.decrypt(stored["client_secret"]) == _PLAINTEXT_SECRET


async def test_update_can_rotate_the_secret(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    service = ProviderConfigService(integration_context)
    await service.set("pipedream", _pipedream_fields(), identity=_identity())
    await service.set("pipedream", _pipedream_fields(secret=_ROTATED_SECRET), identity=_identity())

    stored = await _stored_config(integration_context, "pipedream")
    assert integration_context.encryption.decrypt(stored["client_secret"]) == _ROTATED_SECRET


async def test_set_refreshes_registry_and_resolves(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    """After a write the in-process registry resolves the new provider."""
    service = ProviderConfigService(integration_context)
    await service.set("pipedream", _pipedream_fields(), identity=_identity())

    provider = integration_context.providers.get("pipedream")
    assert provider is not None
    assert isinstance(provider, PipedreamProvider)
    # The registry carries the decrypted client_id from the DB config.
    assert provider._client_id == "client_test"


async def test_unknown_provider_rejected(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    service = ProviderConfigService(integration_context)
    with pytest.raises(InvalidInputError):
        await service.set("not-a-provider", {"foo": "bar"}, identity=_identity())


async def test_first_set_without_secret_rejected(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    """With no existing config to merge, the required secret must be supplied."""
    service = ProviderConfigService(integration_context)
    with pytest.raises(InvalidInputError):
        await service.set("pipedream", _pipedream_fields(secret=None), identity=_identity())


async def test_cross_origin_connect_base_url_with_preserved_secret_rejected(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    """Changing connect_base_url origin without re-entering client_secret is refused."""
    service = ProviderConfigService(integration_context)
    await service.set("pipedream", _pipedream_fields(), identity=_identity())

    with pytest.raises(InvalidInputError, match="client_secret"):
        await service.set(
            "pipedream",
            {"connect_base_url": "https://attacker.example.com/v1"},
            identity=_identity(),
        )

    stored = await _stored_config(integration_context, "pipedream")
    assert stored["connect_base_url"] == "https://api.pipedream.com/v1"
    provider = integration_context.providers.get("pipedream")
    assert isinstance(provider, PipedreamProvider)
    assert provider._base_url == "https://api.pipedream.com/v1"


async def test_same_origin_connect_base_url_path_update_preserves_secret(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    """A same-origin path update on connect_base_url keeps the stored secret."""
    service = ProviderConfigService(integration_context)
    await service.set("pipedream", _pipedream_fields(), identity=_identity())

    view = await service.set(
        "pipedream",
        {"connect_base_url": "https://api.pipedream.com/v2"},
        identity=_identity(),
    )
    assert view.config["connect_base_url"] == "https://api.pipedream.com/v2"
    stored = await _stored_config(integration_context, "pipedream")
    assert stored["connect_base_url"] == "https://api.pipedream.com/v2"
    assert integration_context.encryption.decrypt(stored["client_secret"]) == _PLAINTEXT_SECRET


async def test_cross_origin_connect_base_url_with_new_secret_allowed(
    integration_context: Context, _clean_provider_configs: None
) -> None:
    """Changing connect_base_url origin succeeds when a new client_secret is supplied."""
    service = ProviderConfigService(integration_context)
    await service.set("pipedream", _pipedream_fields(), identity=_identity())

    view = await service.set(
        "pipedream",
        {
            "connect_base_url": "https://pipedream.corp.example.com/v1",
            "client_secret": _ROTATED_SECRET,
        },
        identity=_identity(),
    )
    assert view.config["connect_base_url"] == "https://pipedream.corp.example.com/v1"
    stored = await _stored_config(integration_context, "pipedream")
    assert integration_context.encryption.decrypt(stored["client_secret"]) == _ROTATED_SECRET


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://api.pipedream.com/v1",
        "https://169.254.169.254/v1",
        "https://10.0.0.1/v1",
    ],
)
async def test_unsafe_or_cleartext_connect_base_url_rejected(
    integration_context: Context,
    _clean_provider_configs: None,
    unsafe_url: str,
) -> None:
    service = ProviderConfigService(integration_context)
    with pytest.raises(InvalidInputError):
        await service.set(
            "pipedream",
            {**_pipedream_fields(), "connect_base_url": unsafe_url},
            identity=_identity(),
        )
