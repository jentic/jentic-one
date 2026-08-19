"""Webhook endpoint management: creation, inspection, rotation, deletion.

This build is **outbound-only**: endpoints are ``notification`` destinations we
POST signed events to.

**Creation, rotation and deletion are audited** with the *creating* actor
recorded, so the operator who configured a destination is attributable
afterwards.

Secrets are shown **once**, at creation or rotation. They are stored encrypted
(HMAC needs the key back) so we *could* show them again, but doing so would turn
every read endpoint into a secret-disclosure endpoint. Lost secret ⇒ rotate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from jentic_one.admin.core.schema.webhook_deliveries import WebhookDelivery
from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.repos import AuditRepository
from jentic_one.admin.repos.webhook_repo import (
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
    WebhookEventRepository,
)
from jentic_one.admin.services.errors import (
    InvalidInputError,
    WebhookDeliveryNotFoundError,
    WebhookEndpointNotFoundError,
)
from jentic_one.admin.services.webhooks.secrets import WebhookSecretService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

logger = structlog.get_logger(__name__)


class CreatedEndpoint:
    """A newly created endpoint plus its one-time plaintext secret."""

    def __init__(self, endpoint: WebhookEndpoint, secret: str) -> None:
        self.endpoint = endpoint
        self.secret = secret


class WebhookEndpointService:
    """Manages webhook endpoint configuration."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._secrets = WebhookSecretService(ctx)

    async def create(
        self,
        *,
        name: str,
        identity: Identity,
        target_url: str | None = None,
        event_types: list[str] | None = None,
    ) -> CreatedEndpoint:
        """Create an endpoint, returning the plaintext secret exactly once."""
        self._validate_shape(target_url=target_url)

        secret, encrypted, fingerprint = self._secrets.new_secret()

        async with self._ctx.admin_db.transaction() as session:
            endpoint = await WebhookEndpointRepository.create(
                session,
                name=name,
                secret_hash=fingerprint,
                secret_encrypted=encrypted,
                target_url=target_url,
                event_types=event_types or [],
                created_by=identity.sub,
            )

            await AuditRepository.record(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.WEBHOOK_ENDPOINT,
                target_id=endpoint.id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={
                    "name": name,
                    "target_url": target_url,
                    "event_types": event_types or [],
                },
            )

            logger.info(
                "webhook_endpoint_created",
                endpoint_id=endpoint.id,
                created_by=identity.sub,
            )
            return CreatedEndpoint(endpoint, secret)

    async def list_all(self) -> list[WebhookEndpoint]:
        async with self._ctx.admin_db.session() as session:
            return await WebhookEndpointRepository.list_all(session)

    async def get(self, endpoint_id: str) -> WebhookEndpoint:
        async with self._ctx.admin_db.session() as session:
            endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
        if endpoint is None:
            raise WebhookEndpointNotFoundError(endpoint_id)
        return endpoint

    async def rotate_secret(
        self,
        endpoint_id: str,
        *,
        identity: Identity,
        grace: timedelta | None = None,
    ) -> str:
        """Issue a new secret, returning the plaintext once.

        The previous secret stays valid for a grace window so the far side can be
        updated without dropping in-flight events. ``grace=timedelta(0)`` revokes
        immediately — correct for a leak, at the cost of those events.
        """
        async with self._ctx.admin_db.transaction() as session:
            endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
            if endpoint is None:
                raise WebhookEndpointNotFoundError(endpoint_id)

            secret = (
                self._secrets.rotate(endpoint, grace=grace)
                if grace is not None
                else self._secrets.rotate(endpoint)
            )

            await AuditRepository.record(
                session,
                action=AuditAction.ROTATE,
                target_type=AuditTargetType.WEBHOOK_ENDPOINT,
                target_id=endpoint_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"grace_seconds": int(grace.total_seconds()) if grace else None},
            )
            return secret

    async def delete(self, endpoint_id: str, *, identity: Identity) -> None:
        async with self._ctx.admin_db.transaction() as session:
            endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
            if endpoint is None:
                raise WebhookEndpointNotFoundError(endpoint_id)

            before = {
                "name": endpoint.name,
            }
            await WebhookEndpointRepository.delete(session, endpoint_id)
            await AuditRepository.record(
                session,
                action=AuditAction.DELETE,
                target_type=AuditTargetType.WEBHOOK_ENDPOINT,
                target_id=endpoint_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before=before,
            )

    async def list_deliveries(self, endpoint_id: str) -> list[WebhookDelivery]:
        """The delivery log: what was sent, what came back, how many attempts."""
        async with self._ctx.admin_db.session() as session:
            endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
            if endpoint is None:
                raise WebhookEndpointNotFoundError(endpoint_id)
            return await WebhookDeliveryRepository.list_for_endpoint(session, endpoint_id)

    async def resend(self, delivery_id: str, *, identity: Identity) -> None:
        """Requeue a delivery for another attempt.

        Deliberately re-queues rather than sending inline: the dispatcher owns
        sending, and doing network I/O in a request handler is what the "never hold
        a session across the POST" rule exists to prevent.
        """
        async with self._ctx.admin_db.transaction() as session:
            reset = await WebhookDeliveryRepository.reset_for_resend(session, delivery_id)
            if not reset:
                raise WebhookDeliveryNotFoundError(delivery_id)
            await AuditRepository.record(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.WEBHOOK_DELIVERY,
                target_id=delivery_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                reason="manual resend",
            )

    async def send_test_event(self, endpoint_id: str, *, identity: Identity) -> str:
        """Queue a synthetic event so an operator can confirm wiring.

        Returns the delivery id. The payload is marked ``webhook.test`` so a
        receiver can tell it apart from a real platform event and avoid acting on
        it — an unmarked test event is worse than no test event.
        """
        async with self._ctx.admin_db.transaction() as session:
            endpoint = await WebhookEndpointRepository.get_by_id(session, endpoint_id)
            if endpoint is None:
                raise WebhookEndpointNotFoundError(endpoint_id)

            # Unique per call so repeated tests are not silently deduplicated as
            # one event — an operator pressing "test" twice expects two sends.
            event = await WebhookEventRepository.record_event(
                session,
                endpoint_id=endpoint_id,
                source_event_id=f"test_{endpoint_id}_{_now_token()}",
                event_type="webhook.test",
                payload={
                    "event_type": "webhook.test",
                    "summary": "Test event from Jentic One",
                    "triggered_by": identity.sub,
                },
            )
            if event is None:  # pragma: no cover - the token makes this unreachable
                raise InvalidInputError("Could not queue a test event; please retry.")

            delivery = await WebhookDeliveryRepository.enqueue(
                session, event_id=event.id, endpoint_id=endpoint_id
            )
            return delivery.id

    def _validate_shape(self, *, target_url: str | None) -> None:
        """Reject configurations that cannot be safe or cannot work."""
        if not target_url:
            raise InvalidInputError("A notification endpoint requires a target_url.")
        # Only the scheme is checked here; the address itself is validated at
        # send time by the SSRF-guarding egress transport, which is the only
        # place that can do it correctly (DNS can change after this check).
        if not target_url.startswith(("http://", "https://")):
            raise InvalidInputError("target_url must be an http(s) URL.")


def _now_token() -> str:
    """A short unique token making each test event distinct."""
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
