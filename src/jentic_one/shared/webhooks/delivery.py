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
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.repos.webhook_repo import (
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
    WebhookEventRepository,
)
from jentic_one.shared.config import WebhookConfig
from jentic_one.shared.egress import build_pinned_transport
from jentic_one.shared.webhooks import metrics as webhook_metrics
from jentic_one.shared.webhooks.signing import sign_payload

if TYPE_CHECKING:
    from jentic_one.shared.db.session import DatabaseSession

logger = structlog.get_logger(__name__)

# Resolves an endpoint's plaintext signing secret, or None when unavailable.
SecretResolver = Callable[[WebhookEndpoint], str | None]

_MAX_ERROR_LEN = 500
# Receivers that answer 2xx-but-huge would otherwise let us buffer arbitrary
# data from an untrusted endpoint; we only ever need the status code.
_MAX_RESPONSE_BYTES = 8192
_HTTP_GONE = 410


def _categorize_error(exc: Exception) -> str:
    """Map a send exception to a **categorized, non-sensitive** reason.

    The raw ``str(exc)`` can embed the pinned internal IP or the full target URL
    (``httpx`` includes them), and this string is persisted to ``last_error`` and
    returned by the read API + shown in the UI — so it must never carry that
    detail. We record only a stable low-cardinality category here; the full
    exception (with detail) is logged server-side by the caller for diagnosis.
    """
    if isinstance(exc, httpx.ConnectTimeout | httpx.PoolTimeout):
        return "connection_timeout"
    if isinstance(exc, httpx.ReadTimeout | httpx.WriteTimeout | httpx.TimeoutException):
        return "read_timeout"
    if isinstance(exc, httpx.ConnectError):
        # An SSRF-guard refusal, a DNS failure and an ordinary connect refusal
        # all surface as ConnectError; distinguishing them would require parsing
        # the message (which is exactly the sensitive text we must not expose),
        # so they share one safe category.
        return "connection_error"
    if isinstance(exc, httpx.ProtocolError):
        return "protocol_error"
    if isinstance(exc, httpx.TransportError):
        return "transport_error"
    return "delivery_error"


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
        config: WebhookConfig | None = None,
        poll_interval: float | None = None,
        batch_limit: int | None = None,
        max_attempts: int | None = None,
        timeout_s: float | None = None,
        secret_resolver: SecretResolver | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._admin_db = admin_db
        self._egress = egress
        # A ``WebhookConfig`` supplies the operator-tunable knobs (poll cadence,
        # batch size, retry policy, lease, per-endpoint cap, retention). The
        # per-arg overrides remain for tests that want to poke one value without
        # building a whole config; an explicit override always wins.
        self._config = config or WebhookConfig()
        self._poll_interval = (
            poll_interval if poll_interval is not None else self._config.dispatch_poll_interval_s
        )
        self._batch_limit = (
            batch_limit if batch_limit is not None else self._config.dispatch_batch_limit
        )
        self._max_attempts = max_attempts if max_attempts is not None else self._config.max_attempts
        self._timeout = timeout_s if timeout_s is not None else self._config.request_timeout_s
        # The plaintext signing secret cannot come from ``secret_hash``; a
        # resolver is injected so the PoC can supply secrets from memory while a
        # real deployment reads a secrets store. See §"secret storage" in the
        # build plan.
        self._secret_resolver = secret_resolver
        self._client = client
        self._owns_client = client is None
        self._running = False
        # Retention pruning shares this loop's tick clock (item 10); a running
        # tally decides when a sweep is due.
        self._tick_count = 0

    def _backoff_delay(self, attempt: int, *, jitter: bool = True) -> timedelta:
        """Capped exponential backoff with full jitter for ``attempt`` (1-based).

        Full jitter (a uniform draw over ``[0, window]``) rather than a fixed
        multiplier, so that a fleet of deliveries failing simultaneously spreads
        out instead of retrying in lockstep. Reads the base/cap from config.
        """
        window = min(
            self._config.base_backoff_s * (2 ** max(0, attempt - 1)),
            self._config.max_backoff_s,
        )
        seconds = random.uniform(0, window) if jitter else window
        return timedelta(seconds=seconds)

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
                try:
                    await self._maybe_prune()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("webhook_delivery_prune_error")
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

    async def _maybe_prune(self) -> int:
        """Delivery-log retention (item 10): sweep succeeded rows on a slow cadence.

        Runs on this loop's tick clock but only every ``prune_interval_ticks``
        ticks — retention shifts on the scale of days, so sweeping every tick
        would be wasteful. Disabled entirely when ``retention_succeeded_days`` or
        ``prune_interval_ticks`` is 0.
        """
        cfg = self._config
        if cfg.retention_succeeded_days <= 0 or cfg.prune_interval_ticks <= 0:
            return 0
        self._tick_count += 1
        if self._tick_count % cfg.prune_interval_ticks != 0:
            return 0
        return await self.prune_now()

    async def prune_now(self) -> int:
        """Run one retention sweep immediately (also the tests' entry point)."""
        cfg = self._config
        if cfg.retention_succeeded_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=cfg.retention_succeeded_days)
        async with self._admin_db.transaction() as session:
            deleted = await WebhookDeliveryRepository.prune_succeeded(
                session, older_than=cutoff, limit=cfg.prune_batch_limit
            )
        if deleted:
            logger.info("webhook_deliveries_pruned", deleted=deleted)
        return deleted

    async def _claim_batch(self) -> list[_PreparedDelivery]:
        """Phase 1 — claim due rows (leased) and snapshot what sending needs."""
        prepared: list[_PreparedDelivery] = []
        async with self._admin_db.transaction() as session:
            claimed = await WebhookDeliveryRepository.claim_due(
                session,
                limit=self._batch_limit,
                lease_s=self._config.lease_s,
                max_in_flight_per_endpoint=self._config.max_in_flight_per_endpoint,
            )
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
                    webhook_metrics.record_delivery("dead")
                    webhook_metrics.record_dead_letter()
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
                    webhook_metrics.record_delivery("dead")
                    webhook_metrics.record_dead_letter()
                    continue
                prepared.append(
                    _PreparedDelivery(
                        delivery_id=delivery.id,
                        endpoint_id=endpoint.id,
                        # ``attempt_count`` is bumped when the outcome is
                        # recorded, so the *n-th* attempt is ``count + 1``.
                        attempt=delivery.attempt_count + 1,
                        target_url=endpoint.target_url,
                        secret=secret,
                        body=_serialise(event.id, event.event_type, event.payload),
                        event_id=event.id,
                        event_type=event.event_type,
                    )
                )
        return prepared

    async def _send(self, item: _PreparedDelivery) -> _SendOutcome:
        """Phase 2 — sign and POST. No database session is open here.

        The response body is **streamed** and only up to ``_MAX_RESPONSE_BYTES``
        is read: a hostile receiver that answers ``200`` with a multi-GB body
        would otherwise OOM the dispatcher. We never need the body — only the
        status code — so once the cap is crossed the stream is abandoned. A body
        that merely *exceeds* the cap on a 2xx is treated as a delivery failure
        (the receiver is misbehaving), not a success.
        """
        headers = sign_payload(item.secret, item.event_id, item.body).as_dict()
        headers["content-type"] = "application/json"
        headers["user-agent"] = "JenticOne-Webhooks/1.0"
        started = time.monotonic()
        try:
            client = await self._ensure_client()
            async with client.stream(
                "POST",
                item.target_url,
                content=item.body,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                too_large = await self._body_exceeds_cap(response)
                status_code = response.status_code
            duration = time.monotonic() - started
            if too_large:
                logger.warning(
                    "webhook_response_too_large",
                    delivery_id=item.delivery_id,
                    endpoint_id=item.endpoint_id,
                    status_code=status_code,
                    cap_bytes=_MAX_RESPONSE_BYTES,
                )
                webhook_metrics.record_send_duration(duration, outcome="response_too_large")
                return _SendOutcome(
                    delivery_id=item.delivery_id,
                    endpoint_id=item.endpoint_id,
                    attempt=item.attempt,
                    status_code=status_code,
                    error="response_too_large",
                    gone=status_code == _HTTP_GONE,
                )
            error = None if status_code < 300 else f"http_error_{status_code}"
            webhook_metrics.record_send_duration(
                duration, outcome="ok" if error is None else "http_error"
            )
            return _SendOutcome(
                delivery_id=item.delivery_id,
                endpoint_id=item.endpoint_id,
                attempt=item.attempt,
                status_code=status_code,
                error=error,
                gone=status_code == _HTTP_GONE,
            )
        except Exception as exc:
            # Any failure to reach the target is a delivery failure, never a
            # dispatcher crash: a bad URL (including one the SSRF guard
            # refuses), a DNS failure, a timeout or a TLS error must all just
            # fail this one delivery. The full exception (which may embed the
            # pinned internal IP / target URL) is logged server-side only; the
            # persisted ``last_error`` is a safe category (see _categorize_error).
            duration = time.monotonic() - started
            category = _categorize_error(exc)
            logger.warning(
                "webhook_send_failed",
                delivery_id=item.delivery_id,
                endpoint_id=item.endpoint_id,
                category=category,
                error=str(exc),
            )
            webhook_metrics.record_send_duration(duration, outcome=category)
            return _SendOutcome(
                delivery_id=item.delivery_id,
                endpoint_id=item.endpoint_id,
                attempt=item.attempt,
                status_code=None,
                error=category,
            )

    @staticmethod
    async def _body_exceeds_cap(response: httpx.Response) -> bool:
        """Read the streamed body up to the cap; True if it exceeds it.

        Stops as soon as the running total crosses ``_MAX_RESPONSE_BYTES`` so we
        never buffer more than one chunk past the cap.
        """
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                return True
        return False

    async def _record_outcomes(self, outcomes: list[_SendOutcome]) -> None:
        """Phase 3 — persist results, scheduling retries or dead-lettering."""
        async with self._admin_db.transaction() as session:
            for outcome in outcomes:
                if outcome.succeeded:
                    assert outcome.status_code is not None
                    await WebhookDeliveryRepository.mark_succeeded(
                        session, outcome.delivery_id, status_code=outcome.status_code
                    )
                    webhook_metrics.record_delivery("succeeded")
                    continue

                if outcome.gone:
                    # The receiver has asked us to stop. Retrying would be
                    # abuse, so deactivate the endpoint and dead-letter.
                    await WebhookEndpointRepository.deactivate(session, outcome.endpoint_id)
                    await WebhookDeliveryRepository.mark_failed(
                        session,
                        outcome.delivery_id,
                        status_code=outcome.status_code,
                        error="endpoint_gone_deactivated",
                        retry_in=None,
                    )
                    logger.warning(
                        "webhook_endpoint_deactivated",
                        endpoint_id=outcome.endpoint_id,
                        reason="410_gone",
                    )
                    webhook_metrics.record_delivery("deactivated")
                    webhook_metrics.record_endpoint_deactivated("410_gone")
                    webhook_metrics.record_dead_letter()
                    continue

                exhausted = outcome.attempt >= self._max_attempts
                await WebhookDeliveryRepository.mark_failed(
                    session,
                    outcome.delivery_id,
                    status_code=outcome.status_code,
                    error=outcome.error or "delivery_error",
                    retry_in=None if exhausted else self._backoff_delay(outcome.attempt),
                )
                logger.warning(
                    "webhook_delivery_failed",
                    delivery_id=outcome.delivery_id,
                    endpoint_id=outcome.endpoint_id,
                    attempt=outcome.attempt,
                    status_code=outcome.status_code,
                    reason=outcome.error,
                    dead_lettered=exhausted,
                )
                webhook_metrics.record_delivery("dead" if exhausted else "failed")
                if exhausted:
                    webhook_metrics.record_dead_letter()

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
