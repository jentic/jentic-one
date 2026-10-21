"""Shared job-enqueue API and worker loop.

This package provides the job enqueue function and worker loop without importing
any surface-specific modules (admin, registry, broker, control). It uses protocol
classes to interact with repositories, which are injected at runtime.
"""

from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.handlers import JobHandler, JobHandlerRegistry
from jentic_one.shared.jobs.wakeup import JOB_NOTIFY_CHANNEL, notify_job_enqueued
from jentic_one.shared.jobs.worker import WorkerLoop

__all__ = [
    "JOB_NOTIFY_CHANNEL",
    "ExecutionHandler",
    "JobHandler",
    "JobHandlerRegistry",
    "WorkerLoop",
    "enqueue_job",
    "notify_job_enqueued",
]
