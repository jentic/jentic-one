"""``RunnerRegistry`` — scheme→runner selection + runner lifecycle.

The broker generalises from "an HTTP proxy" to "a credential-injecting upstream
executor with pluggable transports, selected by the upstream URL scheme". The
registry is that selection seam plus the owner of every runner's lifecycle.

**What it does (today, incrementally):**

- Maps a URL **scheme** (``http``/``https`` today) to a composed
  :class:`~jentic_one.broker.adapters.runners.base.UpstreamRunner` (the
  decorator-wrapped transport the sync router and async worker both dispatch
  through). It routes over the *live HTTP-shaped* runner — migrating onto the
  transport-neutral ``PluggableUpstreamRunner`` is future work.
- Owns runner **lifecycle**: ``startup()`` for every registered runner on app
  start, ``aclose()`` for all of them on shutdown (the graceful-drain
  step closes **every** runner, not just the HTTP client).

**Startup-failure semantics (a flaky non-HTTP runner must never block HTTP
proxying):** the core HTTP runner is registered ``required=True`` — if its
``startup()`` raises, the broker fails to start (it has no job without it). An
``optional`` runner whose ``startup()`` raises is logged and marked
**unavailable**; the broker still starts and serves HTTP, and a request routed to
that scheme returns ``503`` (:class:`RunnerUnavailableError`) — distinct from the
``501`` (:class:`RunnerSchemeUnsupportedError`) for a scheme with no runner at
all. ``aclose()`` errors on shutdown are logged and never block drain.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import structlog

from jentic_one.broker.adapters.runners.base import UpstreamRunner
from jentic_one.broker.core.exceptions import (
    RunnerSchemeUnsupportedError,
    RunnerUnavailableError,
)

logger = structlog.get_logger(__name__)

# A runner that exposes an async lifecycle is opened/closed by the registry; the
# live HTTP runner today has no startup()/aclose() of its own (its pool lifecycle
# is owned by HttpClientProvider in the lifespan), so both methods are optional
# and default to no-ops via getattr below.


@dataclass(slots=True)
class _Entry:
    """A registered runner plus the lifecycle policy and availability flag."""

    runner: UpstreamRunner
    required: bool
    available: bool = True


class RunnerRegistry:
    """Routes an upstream URL to its runner and owns every runner's lifecycle.

    Schemes are matched case-insensitively. A runner may serve several schemes
    (``http`` + ``https`` share the HTTP runner). The registry is built and
    populated in the app lifespan, then read by the sync handler's runner
    provider and the async worker's executor.
    """

    def __init__(self) -> None:
        self._by_scheme: dict[str, _Entry] = {}

    def register(
        self,
        schemes: str | list[str],
        runner: UpstreamRunner,
        *,
        required: bool = False,
    ) -> None:
        """Register ``runner`` for one or more URL schemes.

        ``required`` runners abort app start if their ``startup()`` fails;
        optional runners degrade to unavailable instead (see module docstring).
        A single :class:`_Entry` is shared across the runner's schemes so the
        availability flag and lifecycle are tracked once per runner.
        """
        if isinstance(schemes, str):
            schemes = [schemes]
        entry = _Entry(runner=runner, required=required)
        for scheme in schemes:
            self._by_scheme[scheme.lower()] = entry

    def select(self, url: str) -> UpstreamRunner:
        """Return the runner for ``url``'s scheme, or raise a routing error.

        Unknown scheme → ``501`` (:class:`RunnerSchemeUnsupportedError`); a
        registered-but-degraded runner → ``503`` (:class:`RunnerUnavailableError`).
        """
        scheme = urlsplit(url).scheme.lower()
        entry = self._by_scheme.get(scheme)
        if entry is None:
            raise RunnerSchemeUnsupportedError(
                detail=f"No upstream runner is registered for the '{scheme or '(none)'}' scheme.",
                type="runner_scheme_unsupported",
            )
        if not entry.available:
            raise RunnerUnavailableError(
                detail=f"The runner for the '{scheme}' scheme is currently unavailable.",
                type="runner_unavailable",
            )
        return entry.runner

    async def startup(self) -> None:
        """Open every registered runner. Required-runner failure aborts; optional degrades.

        Each distinct runner is started once even if it serves multiple schemes.
        A runner with no ``startup()`` (today's HTTP runner) is a no-op.
        """
        for entry in self._distinct_entries():
            runner_startup = getattr(entry.runner, "startup", None)
            if runner_startup is None:
                continue
            try:
                await runner_startup()
            except Exception:
                if entry.required:
                    logger.error(
                        "runner_startup_failed_required",
                        runner=type(entry.runner).__name__,
                    )
                    raise
                entry.available = False
                logger.warning(
                    "runner_startup_failed_degraded",
                    runner=type(entry.runner).__name__,
                )

    async def aclose(self) -> None:
        """Close every registered runner, best-effort — an error never blocks drain.

        The graceful-drain path calls this after the worker has drained, so no
        in-flight call hits a closed pool. Each distinct runner is closed once.
        """
        for entry in self._distinct_entries():
            runner_close = getattr(entry.runner, "aclose", None)
            if runner_close is None:
                continue
            try:
                await runner_close()
            except Exception:
                logger.warning(
                    "runner_aclose_failed",
                    runner=type(entry.runner).__name__,
                )

    def _distinct_entries(self) -> list[_Entry]:
        """The unique entries (one runner shared across schemes is visited once)."""
        seen: list[_Entry] = []
        for entry in self._by_scheme.values():
            if entry not in seen:
                seen.append(entry)
        return seen


__all__ = ["RunnerRegistry"]
