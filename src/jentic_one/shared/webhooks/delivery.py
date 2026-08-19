"""Outbound webhook delivery — claims due deliveries, signs them, sends them.

Structured as a **scanner-style loop** (like ``CredentialExpiryScanner``) rather
than a ``JobKind`` handler, and that choice is load-bearing. ``WorkerLoop``
wraps every handler call in a single transaction
(``_execute_handler``: ``async with self._db.transaction()``), so a handler
would hold a database connection for the entire duration of its outbound HTTP
request. With a pool of ~10 connections, a handful of slow or hanging customer
endpoints would exhaust the pool and take down unrelated request handling. A
scanner owns its own ``DatabaseSession`` and so can enforce the rule that
matters:

    claim (session open) -> **close** -> POST -> reopen to record

Each tick therefore uses three short transactions and holds **no** connection
across the network call.

Retry policy: capped exponential backoff with jitter. The jitter is not
cosmetic — a provider outage causes many deliveries to fail at once, and without
jitter they would all retry in the same instant, repeatedly hammering an already
struggling endpoint. Attempts beyond the cap are dead-lettered rather than
retried forever.

A ``410 Gone`` gets special treatment: it is the receiver explicitly saying
"stop sending", so the endpoint is deactivated instead of retried.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.repos.webhook_repo import (
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
    WebhookEventRepository,
)
from jentic_one.shared.egress import build_pinned_transport
from jentic_one.shared.webhooks.signing import sign_payload

if TYPE_CHECKING:
    from jentic_one.shared.db.session import DatabaseSession

logger = structlog.get_logger(__name__)

# Resolves an endpoint's plaintext signing secret, or None when unavailable.
SecretResolver = Callable[[WebhookEndpoint], str | None]

_POLL_INTERVAL_SECONDS = 2.0
_BATCH_LIMIT = 10
_REQUEST_TIMEOUT_S = 10.0
_MAX_ATTEMPTS = 5
_BASE_BACKOFF_S = 5.0
_MAX_BACKOFF_S = 3600.0
_MAX_ERROR_LEN = 500
# Receivers that answer 2xx-but-huge would otherwise let us buffer arbitrary
# data from an untrusted endpoint; we only ever need the status code.
_MAX_RESPONSE_BYTES = 8192
_HTTP_GONE = 410


def backoff_delay(attempt: int, *, jitter: bool = True) -> timedelta:
    """Capped exponential backoff with full jitter for ``attempt`` (1-based).

    Full jitter (a uniform draw over ``[0, window]``) rather than a fixed
    multiplier, so that a fleet of deliveries failing simultaneously spreads out
    instead of retrying in lockstep.
    """
    window = min(_BASE_BACKOFF_S * (2 ** max(0, attempt - 1)), _MAX_BACKOFF_S)
    seconds = random.uniform(0, window) if jitter else window
    return timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class _PreparedDelivery:
    """Everything needed to send, gathered while the session was open.

    Deliberately a plain snapshot of primitives, not ORM objects: it is used
    *after* the session closes, and touching a detached instance's unloaded
    attributes there would raise.
    """

    delivery_id: str
    endpoint_id: str
    attempt: int
    target_url: str
    secret: str
    body: bytes
    event_id: str
    event_type: str


@dataclass(frozen=True, slots=True)
class _SendOutcome:
    """Result of one outbound attempt."""

    delivery_id: str
    endpoint_id: str
    attempt: int
    status_code: int | None
    error: str | None
    gone: bool = False

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.status_code is not None and self.status_code < 300


class WebhookDeliveryDispatcher:
    """Periodically drains the ``webhook_deliveries`` queue."""

    def __init__(
        self,
        admin_db: DatabaseSession,
        *,
        egress: Any | None = None,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        batch_limit: int = _BATCH_LIMIT,
        max_attempts: int = _MAX_ATTEMPTS,
        timeout_s: float = _REQUEST_TIMEOUT_S,
        secret_resolver: SecretResolver | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._admin_db = admin_db
        self._egress = egress
        self._poll_interval = poll_interval
        self._batch_limit = batch_limit
        self._max_attempts = max_attempts
        self._timeout = timeout_s
        # The plaintext signing secret cannot come from ``secret_hash``; a
        # resolver is injected so the PoC can supply secrets from memory while a
        # real deployment reads a secrets store. See §"secret storage" in the
        # build plan.
        self._secret_resolver = secret_resolver
        self._client = client
        self._owns_client = client is None
        self._running = False

    async def run(self) -> None:
        """Main loop — drain periodically until cancelled.

        One bad tick must never kill the loop: transient DB or network errors
        are logged and the loop continues (mirrors ``WorkerLoop.run`` and the
        scanners).
        """
        self._running = True
        logger.info("webhook_delivery_dispatcher_started")
        try:
            while self._running:
                try:
                    await self.drain_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("webhook_delivery_tick_error")
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.info("webhook_delivery_dispatcher_cancelled")
        finally:
            self._running = False
            await self._aclose_client()
            logger.info("webhook_delivery_dispatcher_stopped")

    def stop(self) -> None:
        self._running = False

    async def drain_once(self) -> int:
        """Claim, send and record one batch. Returns the number attempted.

        The three phases are deliberately separate transactions so that no
        database connection is held while awaiting the network.
        """
        prepared = await self._claim_batch()
        if not prepared:
            return 0

        # Phase 2: no session open here. Sends run concurrently so one slow
        # endpoint doesn't delay the rest of the batch.
        outcomes = await asyncio.gather(*(self._send(item) for item in prepared))

        await self._record_outcomes(outcomes)
        return len(outcomes)

    async def _claim_batch(self) -> list[_PreparedDelivery]:
        """Phase 1 — claim due rows and snapshot what sending needs."""
        prepared: list[_PreparedDelivery] = []
        async with self._admin_db.transaction() as session:
            claimed = await WebhookDeliveryRepository.claim_due(session, limit=self._batch_limit)
            for delivery in claimed:
                endpoint = await WebhookEndpointRepository.get_by_id(session, delivery.endpoint_id)
                event = await WebhookEventRepository.get_by_id(session, delivery.event_id)
                if endpoint is None or event is None or not endpoint.target_url:
                    # Nothing to send to. Dead-letter rather than retry: no
                    # number of attempts will conjure a target.
                    await WebhookDeliveryRepository.mark_failed(
                        session,
                        delivery.id,
                        status_code=None,
                        error="endpoint or event missing, or endpoint has no target_url",
                        retry_in=None,
                    )
                    continue
                secret = self._resolve_secret(endpoint)
                if secret is None:
                    await WebhookDeliveryRepository.mark_failed(
                        session,
                        delivery.id,
                        status_code=None,
                        error="no signing secret available for endpoint",
                        retry_in=None,
                    )
                    continue
                prepared.append(
                    _PreparedDelivery(
                        delivery_id=delivery.id,
                        endpoint_id=endpoint.id,
                        attempt=delivery.attempt_count,
                        target_url=endpoint.target_url,
                        secret=secret,
                        body=_serialise(event.id, event.event_type, event.payload),
                        event_id=event.id,
                        event_type=event.event_type,
                    )
                )
        return prepared

    async def _send(self, item: _PreparedDelivery) -> _SendOutcome:
        """Phase 2 — sign and POST. No database session is open here."""
        headers = sign_payload(item.secret, item.event_id, item.body).as_dict()
        headers["content-type"] = "application/json"
        headers["user-agent"] = "JenticOne-Webhooks/1.0"
        try:
            client = await self._ensure_client()
            response = await client.post(
                item.target_url,
                content=item.body,
                headers=headers,
                timeout=self._timeout,
            )
            error = None if response.status_code < 300 else f"HTTP {response.status_code}"
            return _SendOutcome(
                delivery_id=item.delivery_id,
                endpoint_id=item.endpoint_id,
                attempt=item.attempt,
                status_code=response.status_code,
                error=error,
                gone=response.status_code == _HTTP_GONE,
            )
        except Exception as exc:
            # Any failure to reach the target is a delivery failure, never a
            # dispatcher crash: a bad URL (including one the SSRF guard
            # refuses), a DNS failure, a timeout or a TLS error must all just
            # fail this one delivery.
            return _SendOutcome(
                delivery_id=item.delivery_id,
                endpoint_id=item.endpoint_id,
                attempt=item.attempt,
                status_code=None,
                error=f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_LEN],
            )

    async def _record_outcomes(self, outcomes: list[_SendOutcome]) -> None:
        """Phase 3 — persist results, scheduling retries or dead-lettering."""
        async with self._admin_db.transaction() as session:
            for outcome in outcomes:
                if outcome.succeeded:
                    assert outcome.status_code is not None
                    await WebhookDeliveryRepository.mark_succeeded(
                        session, outcome.delivery_id, status_code=outcome.status_code
                    )
                    continue

                if outcome.gone:
                    # The receiver has asked us to stop. Retrying would be
                    # abuse, so deactivate the endpoint and dead-letter.
                    await WebhookEndpointRepository.deactivate(session, outcome.endpoint_id)
                    await WebhookDeliveryRepository.mark_failed(
                        session,
                        outcome.delivery_id,
                        status_code=outcome.status_code,
                        error="endpoint returned 410 Gone; deactivated",
                        retry_in=None,
                    )
                    logger.warning(
                        "webhook_endpoint_deactivated",
                        endpoint_id=outcome.endpoint_id,
                        reason="410_gone",
                    )
                    continue

                exhausted = outcome.attempt >= self._max_attempts
                await WebhookDeliveryRepository.mark_failed(
                    session,
                    outcome.delivery_id,
                    status_code=outcome.status_code,
                    error=outcome.error or "unknown error",
                    retry_in=None if exhausted else backoff_delay(outcome.attempt),
                )
                logger.warning(
                    "webhook_delivery_failed",
                    delivery_id=outcome.delivery_id,
                    endpoint_id=outcome.endpoint_id,
                    attempt=outcome.attempt,
                    status_code=outcome.status_code,
                    dead_lettered=exhausted,
                )

    def _resolve_secret(self, endpoint: WebhookEndpoint) -> str | None:
        if self._secret_resolver is None:
            return None
        return self._secret_resolver(endpoint)

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Build the outbound client lazily, always SSRF-guarded.

        ``target_url`` is operator-supplied, so every request goes through the
        DNS-pinning transport — without it, a URL resolving to a private address
        (or one that re-resolves after validation) would let a configured
        webhook probe internal infrastructure.

        ``follow_redirects`` is off deliberately: a redirect is an SSRF bypass,
        letting a public URL bounce us to an internal one after validation.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=build_pinned_transport(self._egress),
                timeout=self._timeout,
                follow_redirects=False,
            )
            self._owns_client = True
        return self._client

    async def _aclose_client(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


def _serialise(event_id: str, event_type: str, payload: dict[str, Any]) -> bytes:
    """Build the exact bytes to sign *and* send.

    Serialised once: signing one encoding and transmitting another is the
    classic cause of "signature valid locally, invalid at the receiver".
    ``sort_keys`` keeps a resend byte-identical to the original.
    """
    return json.dumps(
        {"id": event_id, "type": event_type, "data": payload},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
