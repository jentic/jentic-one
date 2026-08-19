"""Integration tests for encrypted webhook secret storage and rotation.

Integration rather than unit because the point is that a secret survives a real
round-trip through Postgres and the real ``EncryptionService`` — the bug worth
catching is "stored in a way that can't be recovered", which a mocked cipher
would hide.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.repos.webhook_repo import WebhookEndpointRepository
from jentic_one.admin.services._support.webhook_secrets import (
    WEBHOOK_SECRET_PREFIX,
    generate_webhook_secret,
)
from jentic_one.admin.services.webhooks.secrets import WebhookSecretService
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.webhooks.signing import (
    HEADER_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    SCHEME,
    compute_signature,
    hash_secret,
    sign_payload,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_endpoints(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(WebhookEndpoint))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def _create(
    integration_context: Context,
    admin_db: DatabaseSession,
    name: str = "notification-endpoint",
) -> tuple[str, str]:
    """Create an endpoint the way the product does. Returns (id, plaintext)."""
    service = WebhookSecretService(integration_context)
    plaintext, encrypted, fingerprint = service.new_secret()
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name=name,
            secret_hash=fingerprint,
            secret_encrypted=encrypted,
            target_url="https://receiver.test/hook",
            created_by="test",
        )
        return endpoint.id, plaintext


# --- generation ---------------------------------------------------------------


def test_generated_secrets_are_prefixed_and_unique() -> None:
    secrets = {generate_webhook_secret() for _ in range(100)}
    assert len(secrets) == 100, "generated secrets must not collide"
    assert all(s.startswith(WEBHOOK_SECRET_PREFIX) for s in secrets)
    # 32 bytes of entropy is ~43 urlsafe-base64 chars, plus the prefix.
    assert all(len(s) > 40 for s in secrets)


# --- storage ------------------------------------------------------------------


async def test_secret_is_recoverable_but_not_stored_in_plaintext(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    """The whole point: encrypted at rest, yet usable for HMAC afterwards.

    This is what a hash could not do: HMAC signing needs the key itself.
    """
    endpoint_id, plaintext = await _create(integration_context, admin_db)
    service = WebhookSecretService(integration_context)

    async with admin_db.session() as session:
        stored = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
    assert stored is not None

    assert plaintext not in stored.secret_encrypted, "ciphertext must not contain the plaintext"
    assert plaintext != stored.secret_hash
    assert service.resolve_secrets(stored) == [plaintext], "must round-trip through Postgres"


async def test_stored_secret_can_verify_a_real_signature(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    """End-to-end meaning: the recovered key actually reproduces the signature."""
    endpoint_id, plaintext = await _create(integration_context, admin_db)
    service = WebhookSecretService(integration_context)

    body = b'{"event":"charge.refunded"}'
    headers = sign_payload(plaintext, message_id="msg_1", body=body)

    async with admin_db.session() as session:
        stored = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
    assert stored is not None
    recovered = service.resolve_secrets(stored)[0]

    sent = headers.as_dict()
    expected = compute_signature(recovered, sent[HEADER_ID], sent[HEADER_TIMESTAMP], body)
    assert sent[HEADER_SIGNATURE] == f"{SCHEME},{expected}"


async def test_fingerprint_is_a_hash_not_the_secret(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    """``secret_hash`` stays a non-reversible fingerprint, for comparison only."""
    _, plaintext = await _create(integration_context, admin_db)
    service = WebhookSecretService(integration_context)
    _, _, fingerprint = service.new_secret()

    assert fingerprint != plaintext
    assert hash_secret(plaintext) != fingerprint, "different secrets, different fingerprints"


async def test_two_endpoints_get_different_secrets(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    """A leaked secret must compromise one endpoint, not all of them.

    (The dev-only shared env-var secret this replaces had exactly that flaw.)
    """
    first_id, first_secret = await _create(integration_context, admin_db, name="one")
    second_id, second_secret = await _create(integration_context, admin_db, name="two")

    assert first_secret != second_secret
    service = WebhookSecretService(integration_context)
    async with admin_db.session() as session:
        first = await WebhookEndpointRepository.get_by_id(session, first_id)
        second = await WebhookEndpointRepository.get_by_id(session, second_id)
    assert first is not None
    assert second is not None
    assert service.resolve_secrets(first) == [first_secret]
    assert service.resolve_secrets(second) == [second_secret]


async def test_undecryptable_secret_yields_no_candidates(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    """A corrupt envelope must fail closed, not raise into the request path.

    An empty candidate list means callers refuse the request; it must never be
    mistaken for "no signature needed".
    """
    endpoint_id, _ = await _create(integration_context, admin_db)
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
        assert endpoint is not None
        endpoint.secret_encrypted = "v1:not-valid-base64-ciphertext"  # pragma: allowlist secret

    service = WebhookSecretService(integration_context)
    async with admin_db.session() as session:
        stored = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
    assert stored is not None
    assert service.resolve_secrets(stored) == []
    assert service.resolve_signing_secret(stored) is None


# --- rotation -----------------------------------------------------------------


async def test_rotation_keeps_the_old_secret_valid_during_grace(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    """Rotating must not drop events already signed with the old secret."""
    endpoint_id, old_secret = await _create(integration_context, admin_db)
    service = WebhookSecretService(integration_context)

    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
        assert endpoint is not None
        new_secret = service.rotate(endpoint, grace=timedelta(hours=1))

    assert new_secret != old_secret
    async with admin_db.session() as session:
        stored = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
    assert stored is not None

    candidates = service.resolve_secrets(stored)
    assert candidates == [new_secret, old_secret], "both keys valid, newest first"
    assert service.resolve_signing_secret(stored) == new_secret, "sign with the new one only"


async def test_expired_previous_secret_stops_being_accepted(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    endpoint_id, old_secret = await _create(integration_context, admin_db)
    service = WebhookSecretService(integration_context)

    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
        assert endpoint is not None
        new_secret = service.rotate(endpoint, grace=timedelta(hours=1))
        # Wind the grace window into the past.
        endpoint.previous_secret_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    async with admin_db.session() as session:
        stored = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
    assert stored is not None
    candidates = service.resolve_secrets(stored)
    assert candidates == [new_secret]
    assert old_secret not in candidates, "expired secret must be refused"


async def test_zero_grace_revokes_the_old_secret_immediately(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    """The leaked-secret path: revoke now, accept the dropped events."""
    endpoint_id, old_secret = await _create(integration_context, admin_db)
    service = WebhookSecretService(integration_context)

    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
        assert endpoint is not None
        new_secret = service.rotate(endpoint, grace=timedelta(0))
        assert endpoint.previous_secret_encrypted is None

    async with admin_db.session() as session:
        stored = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
    assert stored is not None
    assert service.resolve_secrets(stored) == [new_secret]
    assert old_secret not in service.resolve_secrets(stored)


async def test_missing_expiry_treated_as_expired(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    """Ambiguous data must fail closed for a credential."""
    endpoint_id, old_secret = await _create(integration_context, admin_db)
    service = WebhookSecretService(integration_context)

    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
        assert endpoint is not None
        service.rotate(endpoint, grace=timedelta(hours=1))
        endpoint.previous_secret_expires_at = None

    async with admin_db.session() as session:
        stored = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
    assert stored is not None
    assert old_secret not in service.resolve_secrets(stored)


async def test_rotation_updates_the_fingerprint(
    integration_context: Context, admin_db: DatabaseSession, clean_endpoints: None
) -> None:
    endpoint_id, _ = await _create(integration_context, admin_db)
    service = WebhookSecretService(integration_context)

    async with admin_db.session() as session:
        before = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
        assert before is not None
        old_fingerprint = before.secret_hash

    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
        assert endpoint is not None
        new_secret = service.rotate(endpoint)

    async with admin_db.session() as session:
        after = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
    assert after is not None
    assert after.secret_hash != old_fingerprint
    assert after.secret_hash == hash_secret(new_secret)
