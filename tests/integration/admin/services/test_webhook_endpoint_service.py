"""Integration tests for webhook endpoint management.

This build is outbound-only: endpoints are ``notification`` destinations we POST
signed events to. Creation, rotation and deletion are audited.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.webhook_deliveries import WebhookDelivery
from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.core.schema.webhook_events import WebhookEvent
from jentic_one.admin.repos.webhook_repo import (
    STATUS_DEAD,
    STATUS_PENDING,
    WebhookDeliveryRepository,
    WebhookEventRepository,
)
from jentic_one.admin.services.errors import (
    InvalidInputError,
    WebhookDeliveryNotFoundError,
    WebhookEndpointNotFoundError,
)
from jentic_one.admin.services.webhooks.endpoints import WebhookEndpointService
from jentic_one.admin.services.webhooks.secrets import WebhookSecretService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

pytestmark = pytest.mark.integration

OPERATOR = Identity(sub="usr_operator", email="ops@test.local", actor_type=ActorType.USER)


@pytest.fixture()
async def clean_webhooks(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(WebhookDelivery))
            await session.execute(delete(WebhookEvent))
            await session.execute(delete(WebhookEndpoint))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def test_endpoint_creation_is_audited(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    """Creation is recorded with the operator who configured the destination."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="audited-notification",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    async with admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_type == AuditTargetType.WEBHOOK_ENDPOINT,
                AuditEntry.target_id == created.endpoint.id,
            )
        )
        entries = list(result.scalars())

    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == AuditAction.CREATE
    assert entry.actor_id == OPERATOR.sub, "the granting operator"
    assert "secret" not in str(entry.after).lower(), "no secret material in the audit log"


# --- validation --------------------------------------------------------------


async def test_notification_endpoint_requires_a_target_url(
    integration_context: Context, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    with pytest.raises(InvalidInputError, match="requires a target_url"):
        await service.create(name="no-url", identity=OPERATOR)


async def test_target_url_scheme_is_validated(
    integration_context: Context, clean_webhooks: None
) -> None:
    """A non-http scheme can never work, so it is refused up front.

    The address itself is validated at send time by the SSRF-guarding transport —
    the only place that can do it correctly, since DNS can change afterwards.
    """
    service = WebhookEndpointService(integration_context)
    with pytest.raises(InvalidInputError, match="http"):
        await service.create(
            name="bad-scheme",
            identity=OPERATOR,
            target_url="file:///etc/passwd",
        )


# --- read / delete -----------------------------------------------------------


async def test_get_and_list_endpoints(integration_context: Context, clean_webhooks: None) -> None:
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="listed",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    fetched = await service.get(created.endpoint.id)
    assert fetched.name == "listed"
    assert [e.id for e in await service.list_all()] == [created.endpoint.id]


async def test_get_unknown_endpoint_raises_not_found(
    integration_context: Context, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    with pytest.raises(WebhookEndpointNotFoundError):
        await service.get("whep_nope")


async def test_delete_is_audited(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="doomed",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )
    await service.delete(created.endpoint.id, identity=OPERATOR)

    with pytest.raises(WebhookEndpointNotFoundError):
        await service.get(created.endpoint.id)

    async with admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_id == created.endpoint.id,
                AuditEntry.action == AuditAction.DELETE,
            )
        )
        assert result.scalars().first() is not None


# --- rotation via the service ------------------------------------------------


async def test_rotate_secret_returns_a_new_secret_and_audits(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="rotatable",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    new_secret = await service.rotate_secret(created.endpoint.id, identity=OPERATOR)
    assert new_secret != created.secret

    secrets = WebhookSecretService(integration_context)
    endpoint = await service.get(created.endpoint.id)
    assert secrets.resolve_secrets(endpoint) == [new_secret, created.secret], (
        "both keys valid during the grace window, newest first"
    )

    async with admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_id == created.endpoint.id,
                AuditEntry.action == AuditAction.ROTATE,
            )
        )
        assert result.scalars().first() is not None


async def test_zero_grace_rotation_revokes_immediately(
    integration_context: Context, clean_webhooks: None
) -> None:
    """For a leaked secret: revoke now, accept the dropped in-flight events."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="leaked",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    new_secret = await service.rotate_secret(
        created.endpoint.id, identity=OPERATOR, grace=timedelta(0)
    )
    endpoint = await service.get(created.endpoint.id)
    secrets = WebhookSecretService(integration_context)
    assert secrets.resolve_secrets(endpoint) == [new_secret]
    assert created.secret not in secrets.resolve_secrets(endpoint)


# --- delivery log, resend, test event ----------------------------------------


async def test_delivery_log_lists_attempts(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="logged",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    async with admin_db.transaction() as session:
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=created.endpoint.id,
            source_event_id="evt_logged",
            event_type="credential.expired",
            payload={},
        )
        assert event is not None
        await WebhookDeliveryRepository.enqueue(
            session, event_id=event.id, endpoint_id=created.endpoint.id
        )

    log = await service.list_deliveries(created.endpoint.id)
    assert len(log) == 1
    assert log[0].status == STATUS_PENDING


async def test_resend_requeues_a_dead_lettered_delivery(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    """The point of resend: retry after the receiver has been fixed."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="resendable",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    async with admin_db.transaction() as session:
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=created.endpoint.id,
            source_event_id="evt_dead",
            event_type="credential.expired",
            payload={},
        )
        assert event is not None
        delivery = await WebhookDeliveryRepository.enqueue(
            session, event_id=event.id, endpoint_id=created.endpoint.id
        )
        delivery_id = delivery.id
        delivery.status = STATUS_DEAD

    await service.resend(delivery_id, identity=OPERATOR)

    log = await service.list_deliveries(created.endpoint.id)
    assert log[0].status == STATUS_PENDING, "a dead delivery must become sendable again"


async def test_resend_unknown_delivery_raises_not_found(
    integration_context: Context, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    with pytest.raises(WebhookDeliveryNotFoundError):
        await service.resend("whdl_nope", identity=OPERATOR)


async def test_test_event_is_queued_and_marked_as_a_test(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    """A test event must be distinguishable, or a receiver may act on it."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="testable",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    delivery_id = await service.send_test_event(created.endpoint.id, identity=OPERATOR)

    log = await service.list_deliveries(created.endpoint.id)
    assert [d.id for d in log] == [delivery_id]

    async with admin_db.session() as session:
        event = await WebhookEventRepository.get_by_id(session, log[0].event_id)
    assert event is not None
    assert event.event_type == "webhook.test"
    assert event.payload["triggered_by"] == OPERATOR.sub


async def test_repeated_test_events_are_not_deduplicated(
    integration_context: Context, clean_webhooks: None
) -> None:
    """Pressing "test" twice should send twice, not silently collapse to one."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="twice-tested",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    first = await service.send_test_event(created.endpoint.id, identity=OPERATOR)
    second = await service.send_test_event(created.endpoint.id, identity=OPERATOR)

    assert first != second
    assert len(await service.list_deliveries(created.endpoint.id)) == 2
