"""Behavioural contract for ``InvalidationBus`` implementations.

Unlike the signature-only compliance bases in :mod:`jentic_one.testing.compliance`,
this one drives a live bus: a subclass supplies a factory plus the two
transport-specific actions the contract cannot perform generically —
delivering a message through the real channel, and severing the connection —
and inherits tests proving delivery, isolation, reconnect-flush and clean
shutdown.

The test methods are ``async def``; the consuming suite runs them with
pytest-asyncio (``asyncio_mode = "auto"``, or ``pytestmark =
pytest.mark.asyncio`` in the subclass's module). Like the other bases it is
named ``Base*`` so pytest never collects it directly.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence

from jentic_one.shared.invalidation import (
    InvalidationBus,
    InvalidationHandler,
    InvalidationMessage,
)
from jentic_one.testing.compliance import assert_signature_matches


class _Recorder:
    """An ``InvalidationHandler`` that remembers what it was sent."""

    def __init__(self) -> None:
        self.messages: list[InvalidationMessage] = []

    def __call__(self, message: InvalidationMessage) -> None:
        self.messages.append(message)

    def keyed(self) -> list[InvalidationMessage]:
        return [m for m in self.messages if not m.flush]

    def flushes(self) -> list[InvalidationMessage]:
        return [m for m in self.messages if m.flush]


class BaseInvalidationBusContract:
    """Subclass and implement the hooks to prove an ``InvalidationBus`` conforms.

    Hooks:

    - :meth:`bus_factory` — a fresh, *unstarted* bus.
    - :meth:`publish` — make ``bus`` deliver a keyed change on ``topic``
      through its real transport (e.g. a ``pg_notify`` from another
      connection). Called only while the bus is started, except by
      :meth:`test_nothing_delivered_after_stop`.
    - :meth:`force_reconnect` — sever the bus's connection so it must
      re-establish it; return ``False`` when the transport has no connection
      to lose (the reconnect test then has nothing to prove and passes).

    ``topic_a``/``topic_b`` are two distinct topics :meth:`publish` can target.
    """

    topic_a: str = "admin.access_tokens"
    topic_b: str = "admin.agents"
    #: How long to wait for an asynchronous delivery before failing.
    delivery_timeout_s: float = 5.0
    #: How long to watch for a delivery that must *not* happen.
    quiet_period_s: float = 0.3

    async def bus_factory(self) -> InvalidationBus:
        raise NotImplementedError("Subclass must override bus_factory()")

    async def publish(
        self, bus: InvalidationBus, topic: str, keys: Mapping[str, Sequence[str]]
    ) -> None:
        raise NotImplementedError("Subclass must override publish()")

    async def force_reconnect(self, bus: InvalidationBus) -> bool:
        return False

    async def _eventually(self, condition: Callable[[], bool], what: str) -> None:
        deadline = time.monotonic() + self.delivery_timeout_s
        while not condition():
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"timed out after {self.delivery_timeout_s}s waiting for {what}"
                )
            await asyncio.sleep(0.01)

    async def _started(self, *subscriptions: tuple[str, InvalidationHandler]) -> InvalidationBus:
        bus = await self.bus_factory()
        for topic, handler in subscriptions:
            bus.subscribe(topic, handler)
        await bus.start()
        return bus

    async def test_is_invalidation_bus(self) -> None:
        bus = await self.bus_factory()
        assert isinstance(bus, InvalidationBus)
        for method in ("subscribe", "start", "stop"):
            assert_signature_matches(type(bus), InvalidationBus, method)

    async def test_stop_is_idempotent_and_safe_before_start(self) -> None:
        unstarted = await self.bus_factory()
        await unstarted.stop()
        bus = await self._started((self.topic_a, _Recorder()))
        await bus.stop()
        await bus.stop()

    async def test_start_without_subscribers_is_clean(self) -> None:
        bus = await self.bus_factory()
        await bus.start()
        await bus.stop()

    async def test_delivers_to_subscribers_of_the_topic_only(self) -> None:
        on_a, also_a, on_b = _Recorder(), _Recorder(), _Recorder()
        bus = await self._started(
            (self.topic_a, on_a), (self.topic_a, also_a), (self.topic_b, on_b)
        )
        try:
            await self.publish(bus, self.topic_a, {"id": ["k1"]})
            await self._eventually(
                lambda: bool(on_a.keyed()) and bool(also_a.keyed()),
                f"delivery on {self.topic_a}",
            )
            await asyncio.sleep(self.quiet_period_s)
        finally:
            await bus.stop()
        for recorder in (on_a, also_a):
            [message] = recorder.keyed()
            assert message.topic == self.topic_a
            assert message.values("id") == ("k1",)
        assert not on_b.keyed(), f"{self.topic_b} handler received a {self.topic_a} message"

    async def test_raising_handler_does_not_starve_others(self) -> None:
        def explode(_message: InvalidationMessage) -> None:
            raise RuntimeError("handler failure")

        survivor = _Recorder()
        bus = await self._started((self.topic_a, explode), (self.topic_a, survivor))
        try:
            await self.publish(bus, self.topic_a, {"id": ["k1"]})
            await self._eventually(
                lambda: bool(survivor.keyed()), "delivery past a failing handler"
            )
            # The bus keeps delivering after a handler raised.
            await self.publish(bus, self.topic_a, {"id": ["k2"]})
            await self._eventually(lambda: len(survivor.keyed()) == 2, "a second delivery")
        finally:
            await bus.stop()

    async def test_reconnect_flushes_every_subscribed_topic(self) -> None:
        on_a, on_b = _Recorder(), _Recorder()
        bus = await self._started((self.topic_a, on_a), (self.topic_b, on_b))
        try:
            assert not on_a.flushes() and not on_b.flushes(), "flush before any reconnect"
            if not await self.force_reconnect(bus):
                return
            await self._eventually(
                lambda: bool(on_a.flushes()) and bool(on_b.flushes()),
                "a flush on every topic after reconnect",
            )
            assert {m.topic for m in on_a.flushes()} == {self.topic_a}
            # Delivery resumes on the new connection.
            await self.publish(bus, self.topic_a, {"id": ["after"]})
            await self._eventually(
                lambda: any(m.values("id") == ("after",) for m in on_a.keyed()),
                "delivery after reconnect",
            )
        finally:
            await bus.stop()

    async def test_nothing_delivered_after_stop(self) -> None:
        recorder = _Recorder()
        bus = await self._started((self.topic_a, recorder))
        await bus.stop()
        await self.publish(bus, self.topic_a, {"id": ["late"]})
        await asyncio.sleep(self.quiet_period_s)
        assert not recorder.messages, "a stopped bus delivered a message"
