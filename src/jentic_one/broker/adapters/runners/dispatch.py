"""Per-call record of whether the transport may have put request bytes on the wire.

The :class:`~jentic_one.broker.adapters.runners.deadline.DeadlineRunner` cancels
whatever the envelope is awaiting when the overall budget runs out: a queued
per-host bulkhead permit, a retry backoff, or a send in progress. Only the
transport can tell those apart, so it records each send here. The deadline then
reports "nothing reached the upstream" (``pre_send``) only when the transport
took part in the call and every send it started was ruled out (``client.send``
itself failed to connect). A send the deadline cuts off mid-flight counts as sent, since the
request may already be on the wire. A call whose transport never reports here
(another scheme's runner, or a deadline that fires before the transport is
reached) also counts as sent, so the probe can only ever fail closed.

The probe lives in a context variable that the deadline sets for one call, so
nested runners reach it without any change to the ``UpstreamRunner`` signature.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(slots=True)
class DispatchProbe:
    """What the transport has reported about one call's sends."""

    reporting: bool = False
    """The transport has taken part in this call and reports its sends."""
    unresolved_sends: int = 0
    """Sends started and not ruled out: their request bytes may have reached the upstream.

    A count rather than a flag, so a send ruled out while another one is in
    flight (or after another one completed) never hides the other send.
    """

    def begin_send(self) -> None:
        """Count a send as started; it stays counted unless :meth:`send_ruled_out` follows."""
        self.unresolved_sends += 1

    def send_ruled_out(self) -> None:
        """One send counted by :meth:`begin_send` failed before sending any bytes."""
        self.unresolved_sends -= 1

    def nothing_sent(self) -> bool:
        """Whether the call provably never put request bytes on the wire."""
        return self.reporting and self.unresolved_sends == 0


_current: ContextVar[DispatchProbe | None] = ContextVar("broker_dispatch_probe", default=None)


@contextmanager
def track_dispatch() -> Iterator[DispatchProbe]:
    """Install a fresh probe for the call that runs inside the block."""
    probe = DispatchProbe()
    token = _current.set(probe)
    try:
        yield probe
    finally:
        _current.reset(token)


def current_dispatch_probe() -> DispatchProbe | None:
    """The probe of the call in progress, or ``None`` when nothing tracks it."""
    return _current.get()


__all__ = ["DispatchProbe", "current_dispatch_probe", "track_dispatch"]
