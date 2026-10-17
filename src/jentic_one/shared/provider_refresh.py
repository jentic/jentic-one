"""Keep a process's credential provider registry in step with the stored configs.

An admin write to ``provider_configs`` rebuilds the registry of the process that
served it. Every other process that resolves providers (control, the broker —
and, in the parts topology, control is not the process admin runs in) learns of
the change from the ``admin.provider_configs`` invalidation topic: the trigger
on that table notifies on commit, and :class:`ProviderConfigRefresher` turns
each notification into a :meth:`Context.refresh_providers` call.

Refreshes are debounced and single-flighted: a burst of notifications collapses
into one rebuild, and a notification that lands while a rebuild is running
causes exactly one more (the running one may have read the table before that
change committed). A reconnect ``flush`` from the bus triggers a refresh like
any other message — changes made while the bus was disconnected are unknown.
A failed rebuild is retried with capped backoff, so a database blip does not
leave the registry stale until the next write.

On a bus that never delivers (SQLite, or ``invalidation.enabled: false``) the
refresher stays idle and the registry changes only on the writing process.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import structlog

from jentic_one.shared.invalidation import (
    InvalidationBus,
    InvalidationMessage,
    NoopInvalidationBus,
    topic_for,
)

logger = structlog.get_logger(__name__)

#: The invalidation topic the ``provider_configs`` trigger publishes on.
PROVIDER_CONFIG_TOPIC = topic_for("admin", "provider_configs")

#: Window that collapses a burst of notifications into one rebuild.
DEFAULT_DEBOUNCE_S = 0.2
#: First retry delay after a failed rebuild; doubles up to the ceiling.
DEFAULT_RETRY_BASE_S = 1.0
DEFAULT_RETRY_MAX_S = 30.0


class ProviderConfigRefresher:
    """Runs a provider-registry refresh whenever the stored configs change.

    Lifecycle: :meth:`subscribe` at app build time (before the bus starts),
    :meth:`start` in the lifespan after the bus started, :meth:`stop` before
    the ``Context`` shuts down. One owned worker task does every refresh, so
    nothing outlives :meth:`stop`.
    """

    def __init__(
        self,
        refresh: Callable[[], Awaitable[object]],
        *,
        debounce_s: float = DEFAULT_DEBOUNCE_S,
        retry_base_s: float = DEFAULT_RETRY_BASE_S,
        retry_max_s: float = DEFAULT_RETRY_MAX_S,
    ) -> None:
        self._refresh = refresh
        self._debounce_s = debounce_s
        self._retry_base_s = retry_base_s
        self._retry_max_s = retry_max_s
        self._wanted = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        self._reconcile_on_start = False

    def subscribe(self, bus: InvalidationBus) -> None:
        """Refresh on every ``admin.provider_configs`` message from ``bus``.

        On a bus that delivers, :meth:`start` also runs one reconciling refresh:
        a change committed between the boot-time refresh and the bus's first
        ``LISTEN`` would otherwise go unseen.
        """
        bus.subscribe(PROVIDER_CONFIG_TOPIC, self.on_message)
        self._reconcile_on_start = not isinstance(bus, NoopInvalidationBus)

    def on_message(self, message: InvalidationMessage) -> None:
        """Bus handler: schedule a refresh (never blocks, never awaits)."""
        self.request()

    def request(self) -> None:
        """Ask for a refresh; ignored before :meth:`start` and after :meth:`stop`."""
        if self._task is None or self._stopped:
            return
        self._idle.clear()
        self._wanted.set()

    async def start(self) -> None:
        if self._task is not None or self._stopped:
            return
        self._task = asyncio.create_task(self._run(), name="provider-config-refresh")
        if self._reconcile_on_start:
            self.request()

    async def stop(self) -> None:
        """Cancel the worker (a rebuild in progress is abandoned, not swapped in)."""
        self._stopped = True
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._idle.set()

    async def wait_idle(self) -> None:
        """Wait until no refresh is pending or running (tests, diagnostics)."""
        await self._idle.wait()

    async def _run(self) -> None:
        failures = 0
        while True:
            await self._wanted.wait()
            if self._debounce_s > 0:
                await asyncio.sleep(self._debounce_s)
            self._wanted.clear()
            try:
                await self._refresh()
            except Exception as exc:
                failures += 1
                delay = min(self._retry_base_s * 2 ** (failures - 1), self._retry_max_s)
                logger.warning(
                    "provider_config_refresh_failed",
                    error_type=type(exc).__name__,
                    attempt=failures,
                    retry_in_s=delay,
                    actionable_step=(
                        "Check that this process can read the admin database and holds "
                        "the credential keyset; the refresh is retried automatically."
                    ),
                )
                await asyncio.sleep(delay)
                self._wanted.set()
                continue
            if failures:
                logger.info("provider_config_refresh_recovered", attempts=failures + 1)
            failures = 0
            if not self._wanted.is_set():
                self._idle.set()
