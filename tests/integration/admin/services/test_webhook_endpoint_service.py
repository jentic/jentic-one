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

    A DNS-name address is still validated at send time by the SSRF-guarding
    transport — the only place that can do it correctly, since DNS can change
    afterwards — but the clear-cut cases (bad scheme, disallowed IP literal) are
    rejected here for a friendly, immediate error.
    """
    service = WebhookEndpointService(integration_context)
    with pytest.raises(InvalidInputError, match="http"):
        await service.create(
            name="bad-scheme",
            identity=OPERATOR,
            target_url="file:///etc/passwd",
        )


@pytest.mark.parametrize(
    "target_url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata IMDS
        "http://10.0.0.5/",  # private range
        "http://127.0.0.1:6379/",  # loopback
        "http://[::1]/",  # IPv6 loopback literal
    ],
)
async def test_create_rejects_disallowed_ip_literal(
    integration_context: Context, clean_webhooks: None, target_url: str
) -> None:
    """An IP-literal SSRF target is refused at create time (defence-in-depth)."""
    service = WebhookEndpointService(integration_context)
    with pytest.raises(InvalidInputError, match="not allowed"):
        await service.create(name="ssrf", identity=OPERATOR, target_url=target_url)


async def test_create_accepts_a_valid_public_url(
    integration_context: Context, clean_webhooks: None
) -> None:
    """A well-formed public https URL is accepted at create time."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="valid",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )
    assert created.endpoint.target_url == "https://receiver.test/hook"


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


# --- update ------------------------------------------------------------------


async def test_update_changes_fields_and_is_audited(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    """A happy-path edit: name, target_url, event_types and active all change."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="before-edit",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
        event_types=["credential.expired"],
    )

    updated = await service.update(
        created.endpoint.id,
        identity=OPERATOR,
        name="after-edit",
        target_url="https://receiver.test/new-hook",
        event_types=["execution.failed", "access_request.filed"],
        active=False,
    )

    assert updated.name == "after-edit"
    assert updated.target_url == "https://receiver.test/new-hook"
    assert updated.event_types == ["execution.failed", "access_request.filed"]
    assert updated.active is False

    # Re-read to prove it persisted, not just mutated in memory.
    refetched = await service.get(created.endpoint.id)
    assert refetched.name == "after-edit"
    assert refetched.active is False

    async with admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_id == created.endpoint.id,
                AuditEntry.action == AuditAction.UPDATE,
            )
        )
        entry = result.scalars().first()
    assert entry is not None, "an edit must be audited"
    assert "secret" not in str(entry.after).lower(), "no secret material in the audit log"


async def test_partial_update_leaves_omitted_fields_untouched(
    integration_context: Context, clean_webhooks: None
) -> None:
    """PATCH semantics: an omitted field keeps its current value."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="partial",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
        event_types=["credential.expired"],
    )

    # Change only the name.
    await service.update(created.endpoint.id, identity=OPERATOR, name="renamed")

    refetched = await service.get(created.endpoint.id)
    assert refetched.name == "renamed"
    assert refetched.target_url == "https://receiver.test/hook", "target_url untouched"
    assert refetched.event_types == ["credential.expired"], "subscription untouched"
    assert refetched.active is True, "active untouched"


async def test_update_to_empty_event_types_subscribes_to_all(
    integration_context: Context, clean_webhooks: None
) -> None:
    """An explicit empty list means "subscribe to everything", not "leave as-is"."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="widen",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
        event_types=["credential.expired"],
    )

    await service.update(created.endpoint.id, identity=OPERATOR, event_types=[])

    refetched = await service.get(created.endpoint.id)
    assert refetched.event_types == [], "empty means all relayable types"


async def test_update_rejects_a_bad_target_url(
    integration_context: Context, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="guarded",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    with pytest.raises(InvalidInputError, match="http"):
        await service.update(
            created.endpoint.id,
            identity=OPERATOR,
            target_url="file:///etc/passwd",
        )

    # The bad edit must not have partially applied.
    refetched = await service.get(created.endpoint.id)
    assert refetched.target_url == "https://receiver.test/hook"


@pytest.mark.parametrize(
    "target_url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata IMDS
        "http://10.0.0.5/",  # private range
        "http://127.0.0.1:6379/",  # loopback
        "http://[::1]/",  # IPv6 loopback literal
    ],
)
async def test_update_rejects_disallowed_ip_literal(
    integration_context: Context, clean_webhooks: None, target_url: str
) -> None:
    """An IP-literal SSRF target is refused at update time, and does not apply."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="guarded-update",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    with pytest.raises(InvalidInputError, match="not allowed"):
        await service.update(created.endpoint.id, identity=OPERATOR, target_url=target_url)

    refetched = await service.get(created.endpoint.id)
    assert refetched.target_url == "https://receiver.test/hook", "the bad edit did not apply"


async def test_update_accepts_a_valid_public_url(
    integration_context: Context, clean_webhooks: None
) -> None:
    """A well-formed public https URL is accepted at update time."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="update-valid",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    updated = await service.update(
        created.endpoint.id,
        identity=OPERATOR,
        target_url="https://receiver.test/new-hook",
    )
    assert updated.target_url == "https://receiver.test/new-hook"


# --- per-endpoint allowed_cidrs validation (Phase 3) -------------------------


async def test_create_normalises_and_dedupes_allowed_cidrs(
    integration_context: Context, clean_webhooks: None
) -> None:
    """Valid CIDRs are canonicalised (host->/32|/128), deduped, order preserved."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="cidr-ok",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
        allowed_cidrs=["10.0.0.0/8", "10.0.0.5", "10.0.0.0/8", "fc00::/7"],
    )
    # "10.0.0.5" canonicalises to its /32; the duplicate /8 is dropped.
    assert created.endpoint.allowed_cidrs == ["10.0.0.0/8", "10.0.0.5/32", "fc00::/7"]


async def test_create_rejects_a_malformed_cidr(
    integration_context: Context, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    with pytest.raises(InvalidInputError, match="not a valid CIDR"):
        await service.create(
            name="cidr-bad",
            identity=OPERATOR,
            target_url="https://receiver.test/hook",
            allowed_cidrs=["not-a-cidr"],
        )


async def test_update_replaces_allowed_cidrs(
    integration_context: Context, clean_webhooks: None
) -> None:
    """Sending a new list replaces the stored allowlist; omitting it leaves it."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="cidr-edit",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
        allowed_cidrs=["10.0.0.0/8"],
    )

    # Omitting allowed_cidrs must leave the current value untouched.
    await service.update(created.endpoint.id, identity=OPERATOR, name="cidr-edit-2")
    assert (await service.get(created.endpoint.id)).allowed_cidrs == ["10.0.0.0/8"]

    # Sending a new list replaces it.
    updated = await service.update(
        created.endpoint.id, identity=OPERATOR, allowed_cidrs=["192.168.0.0/16"]
    )
    assert updated.allowed_cidrs == ["192.168.0.0/16"]

    # Sending an empty list clears it.
    cleared = await service.update(created.endpoint.id, identity=OPERATOR, allowed_cidrs=[])
    assert cleared.allowed_cidrs == []


async def test_update_rejects_a_malformed_cidr(
    integration_context: Context, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="cidr-edit-bad",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
        allowed_cidrs=["10.0.0.0/8"],
    )
    with pytest.raises(InvalidInputError, match="not a valid CIDR"):
        await service.update(
            created.endpoint.id, identity=OPERATOR, allowed_cidrs=["999.999.0.0/8"]
        )
    # The bad edit did not apply.
    assert (await service.get(created.endpoint.id)).allowed_cidrs == ["10.0.0.0/8"]


async def test_metadata_cidr_stored_but_never_widens_deny(
    integration_context: Context, clean_webhooks: None
) -> None:
    """Storing the metadata range is allowed at config time (it's a valid CIDR)…

    …but this only proves the CIDR validator accepts a well-formed network; the
    ``assert_ip_allowed`` hard-deny (covered in the egress-allowlist unit tests)
    still refuses the metadata IPs at send regardless of this stored list. Config
    validation and send-time enforcement are separate layers.
    """
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="cidr-metadata",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
        allowed_cidrs=["169.254.0.0/16"],
    )
    assert created.endpoint.allowed_cidrs == ["169.254.0.0/16"]


async def test_update_unknown_endpoint_raises_not_found(
    integration_context: Context, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    with pytest.raises(WebhookEndpointNotFoundError):
        await service.update("whep_nope", identity=OPERATOR, name="ghost")


async def test_update_never_affects_the_signing_secret(
    integration_context: Context, clean_webhooks: None
) -> None:
    """Editing configuration must leave signing authority exactly as it was."""
    service = WebhookEndpointService(integration_context)
    secrets = WebhookSecretService(integration_context)
    created = await service.create(
        name="secret-stable",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    before = await service.get(created.endpoint.id)
    before_encrypted = before.secret_encrypted
    assert secrets.resolve_secrets(before) == [created.secret]

    await service.update(
        created.endpoint.id,
        identity=OPERATOR,
        name="secret-stable-renamed",
        target_url="https://receiver.test/elsewhere",
    )

    after = await service.get(created.endpoint.id)
    assert after.secret_encrypted == before_encrypted, "the stored key is untouched"
    assert secrets.resolve_secrets(after) == [created.secret], "the same secret still verifies"


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


# --- aggregate stats + per-attempt history via the service (Phase 3) ---------


async def test_get_stats_summarises_pending_deliveries(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    """The Overview aggregate exposes the shape the drawer's KpiStrip renders."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="stats",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    async with admin_db.transaction() as session:
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=created.endpoint.id,
            source_event_id="evt_stats",
            event_type="credential.expired",
            payload={},
        )
        assert event is not None
        await WebhookDeliveryRepository.enqueue(
            session, event_id=event.id, endpoint_id=created.endpoint.id
        )

    stats = await service.get_stats(created.endpoint.id)
    assert stats["total"] == 1
    assert stats["counts_by_status"].get(STATUS_PENDING) == 1
    # A never-attempted delivery has no last attempt / recent activity yet.
    assert stats["last_status_code"] is None
    assert stats["recent_total"] == 0


async def test_get_stats_unknown_endpoint_raises_not_found(
    integration_context: Context, clean_webhooks: None
) -> None:
    service = WebhookEndpointService(integration_context)
    with pytest.raises(WebhookEndpointNotFoundError):
        await service.get_stats("whep_nope")


async def test_list_delivery_attempts_is_empty_before_any_send(
    integration_context: Context, admin_db: DatabaseSession, clean_webhooks: None
) -> None:
    """A freshly-queued delivery has no attempt-history rows yet."""
    service = WebhookEndpointService(integration_context)
    created = await service.create(
        name="attempts",
        identity=OPERATOR,
        target_url="https://receiver.test/hook",
    )

    async with admin_db.transaction() as session:
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=created.endpoint.id,
            source_event_id="evt_attempts",
            event_type="credential.expired",
            payload={},
        )
        assert event is not None
        delivery = await WebhookDeliveryRepository.enqueue(
            session, event_id=event.id, endpoint_id=created.endpoint.id
        )
        delivery_id = delivery.id

    assert await service.list_delivery_attempts(delivery_id) == []
