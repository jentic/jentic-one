"""Runtime configuration: enabled services and background workers."""

from __future__ import annotations

import threading
from typing import Any

import structlog
from pydantic import BaseModel, Field, model_validator

_logger = structlog.get_logger(__name__)


class ServicesConfig(BaseModel):
    """Service-level settings (immutable after boot)."""

    request_timeout_s: float = 30.0
    retry_max: int = 3
    retry_backoff_s: float = 1.0
    background_task_drain_timeout_s: float = Field(
        default=10.0,
        ge=0,
        description=(
            "On shutdown, how long (seconds) the app waits for work requests "
            "handed off after their response (e.g. streaming execution records) "
            "before cancelling what is still running. Runs before the databases "
            "close; each cancelled task is logged and counted on "
            "``app.background_tasks`` (``outcome=cancelled``)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_sa_sweep_age(cls, data: Any) -> Any:
        """Ignore ``services.service_account_sweep_min_stamp_age_hours``.

        The 0.40 age gate of the automatic service-account sweep. Theme-8
        Phase 4 removed the sweep's boot job: the upgrade now migrates, verifies
        and sweeps every service account before dropping the tables, so the
        setting has nothing left to gate. Harmless when left behind — dropped
        with a one-time warning, never a boot failure.
        """
        if isinstance(data, dict) and _RETIRED_SA_SWEEP_AGE_KEY in data:
            _warn_retired_sa_sweep_age_once()
            data = {k: v for k, v in data.items() if k != _RETIRED_SA_SWEEP_AGE_KEY}
        return data


_RETIRED_SA_SWEEP_AGE_KEY = "service_account_sweep_min_stamp_age_hours"
_retired_sa_sweep_age_warned = threading.Event()


def _warn_retired_sa_sweep_age_once() -> None:
    """One WARNING per process for the leftover setting (it is ignored).

    Latched like :func:`_warn_retired_direct_bindings_flag_once`: config is
    validated more than once per process.
    """
    if _retired_sa_sweep_age_warned.is_set():
        return
    _retired_sa_sweep_age_warned.set()
    _logger.warning(
        "config_retired_setting_ignored",
        setting=f"services.{_RETIRED_SA_SWEEP_AGE_KEY}",
        detail=(
            "removed in theme-8 Phase 4 with the automatic service-account sweep; "
            "the upgrade migrates and sweeps every service account itself, and the "
            "value is ignored"
        ),
        actionable_step=(
            f"Remove services.{_RETIRED_SA_SWEEP_AGE_KEY} from the config file or "
            "JENTIC__SERVICES__SERVICE_ACCOUNT_SWEEP_MIN_STAMP_AGE_HOURS from the environment."
        ),
    )


class WorkerConfig(BaseModel):
    """Background job-worker durability knobs.

    The worker claims a job, sets a **visibility deadline** (``visibility_timeout_s``
    from claim), and processes it. A job left ``RUNNING`` past that deadline by a
    crashed worker/pod is reclaimed on a later poll. A handler failure requeues the
    job with capped exponential backoff up to ``max_attempts`` claims; beyond that
    it is dead-lettered (poison-message handling) rather than looped forever.

    Each claim is a single atomic ``UPDATE`` (``FOR UPDATE SKIP LOCKED`` on
    Postgres, a serialised write transaction on SQLite), so any number of worker
    loops — across processes or replicas, each running up to ``concurrency``
    jobs — can poll the same queue without double-claiming a job.
    """

    # How long a claimed (RUNNING) job stays invisible to other workers. Size it
    # safely above the longest expected handler runtime (upstream timeout + slack)
    # so a healthy-but-slow job is never reclaimed mid-flight and double-processed.
    visibility_timeout_s: float = 120.0
    # Total claims before a repeatedly-failing job is dead-lettered.
    max_attempts: int = 5
    # Backoff before a failed job becomes claimable again: min(base * 2**(n-1), max).
    retry_backoff_base_s: float = 2.0
    retry_backoff_max_s: float = 60.0
    # Bounded wait for in-flight jobs to finish on graceful drain; past
    # this the worker stops claiming and lets the still-RUNNING job be reclaimed
    # via its visibility timeout after restart (no work dropped).
    drain_timeout_s: float = 25.0
    reconnect_backoff_base_s: float = Field(
        default=2.0,
        gt=0,
        le=300,
        description=(
            "First delay, in seconds, before the worker retries after losing its "
            "database connection. Doubles on each further failed attempt up to "
            "``reconnect_backoff_max_s``; each delay is then jittered into the "
            "upper half of its value so workers sharing a database do not retry "
            "in lockstep."
        ),
    )
    reconnect_backoff_max_s: float = Field(
        default=30.0,
        gt=0,
        le=3600,
        description=(
            "Cap, in seconds, on the worker's reconnect delay while the database "
            "is unreachable — a recovered database is picked up within this long."
        ),
    )
    poll_interval_s: float = Field(
        default=2.0,
        ge=0.25,
        le=60.0,
        description=(
            "Idle wait (seconds) between job-queue polls when nothing is claimable. "
            "A job enqueued in the same process wakes the worker immediately, so "
            "this mainly bounds pickup latency for jobs enqueued by another process "
            "and how quickly visibility-expired jobs are reclaimed. Every poll is a "
            "write transaction (the claim is an ``UPDATE``) even when the queue is "
            "empty — on SQLite it takes the database write lock that request writes "
            "also wait on — so each process issues about ``1 / poll_interval_s`` of "
            "them per second while idle."
        ),
    )
    concurrency: int = Field(
        default=1,
        ge=1,
        le=64,
        description=(
            "Maximum jobs one worker process runs at once (1 = serial). Postgres "
            "only: SQLite requires 1, because a job's handler holds the database "
            "write lock across its upstream call, so a second concurrent job's "
            "writes would time out. Each in-flight job holds about 2 admin-DB "
            "connections at once (its handler transaction plus the run-time "
            "authorization or credential-audit session it opens), so "
            "``2 * concurrency`` must stay below ``databases.admin.pool_max``, "
            "leaving the remainder for HTTP requests and other background work "
            "in the same process."
        ),
    )
    sweep_interval_s: float = Field(
        default=120.0,
        ge=1.0,
        le=86_400.0,
        description=(
            "Seconds between the worker's retention sweeps, which expire lapsed "
            "execution approvals and delete expired job results and long-expired "
            "access/refresh token rows. Measured in wall time, independent of "
            "``poll_interval_s`` and wake-ups."
        ),
    )


class RuntimeConfig(BaseModel):
    """Hot-reloadable runtime flags."""

    debug: bool = False
    log_level: str = "INFO"
    maintenance_mode: bool = False

    def reload(self, overrides: dict[str, Any]) -> RuntimeConfig:
        """Return a new RuntimeConfig with overrides applied."""
        data = self.model_dump()
        data.update(overrides)
        return RuntimeConfig.model_validate(data)
