"""In-memory ``InvalidationBus`` implementations: a no-op and an in-process bus."""

from __future__ import annotations

from collections import defaultdict

import structlog

from jentic_one.shared.invalidation.protocol import InvalidationHandler, InvalidationMessage

logger = structlog.get_logger(__name__)


class SubscriberRegistry:
    """Topic → handlers table with failure-isolated dispatch.

    Shared by every bus implementation so delivery semantics (one raising
    handler never starves the rest; a reconnect flush reaches every topic)
    cannot drift between them.
    """

    def __init__(self) -> None:
        self._handlers: defaultdict[str, list[InvalidationHandler]] = defaultdict(list)

    def add(self, topic: str, handler: InvalidationHandler) -> None:
        self._handlers[topic].append(handler)

    @property
    def topics(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def __bool__(self) -> bool:
        return bool(self._handlers)

    def dispatch(self, message: InvalidationMessage) -> None:
        """Call every handler subscribed to ``message.topic``; isolate failures."""
        for handler in tuple(self._handlers.get(message.topic, ())):
            try:
                handler(message)
            except Exception:
                logger.exception(
                    "invalidation_handler_failed",
                    topic=message.topic,
                    operation=message.operation,
                    flush=message.flush,
                )

    def flush_all(
        self, *, operation: str = "reconnect", databases: frozenset[str] | None = None
    ) -> None:
        """Send one ``flush`` message to every subscribed topic.

        ``databases`` restricts the flush to topics of those logical databases
        (the part of ``"<database>.<table>"`` before the dot).
        """
        for topic in tuple(self._handlers):
            if databases is not None and topic.partition(".")[0] not in databases:
                continue
            self.dispatch(InvalidationMessage(topic=topic, operation=operation, flush=True))


class NoopInvalidationBus:
    """The bus for backends without a notification channel (SQLite).

    Accepts subscriptions and never delivers: cache TTLs bound staleness. A
    single-file SQLite database cannot take multiple writer processes anyway,
    so there is no other process whose writes need to reach this one.
    """

    def subscribe(self, topic: str, handler: InvalidationHandler) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class InProcessInvalidationBus:
    """Delivers :meth:`publish`-ed messages to subscribers in the same process.

    For tests and single-process composition (an extension that mutates state
    in-process and wants its caches to drop entries immediately). Messages are
    delivered synchronously from :meth:`publish` while the bus is started.
    """

    def __init__(self) -> None:
        self._subscribers = SubscriberRegistry()
        self._running = False

    def subscribe(self, topic: str, handler: InvalidationHandler) -> None:
        self._subscribers.add(topic, handler)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def publish(self, message: InvalidationMessage) -> None:
        """Deliver ``message`` to its topic's subscribers (dropped when stopped)."""
        if self._running:
            self._subscribers.dispatch(message)

    async def simulate_reconnect(self) -> None:
        """Behave as a bus whose connection was lost and re-established."""
        if self._running:
            self._subscribers.flush_all()
