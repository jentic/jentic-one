"""Protocols for repository access — avoids importing surface modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from jentic_one.shared.auth.identity import Identity


@dataclass(frozen=True, slots=True)
class InjectedAuth:
    """Auth material to apply to an outbound upstream request.

    Carries headers, query params, cookies, **and** server variables so an
    ``apiKey in: query`` or ``apiKey in: cookie`` credential is never silently
    dropped (a headers-only return would lose both). Cookie entries are merged
    into the outbound ``Cookie`` header by the call-site (sync router / worker).
    Server-variable entries are substituted into the upstream URL template.

    ``credential_id`` / ``credential_name`` attribute the material back to the
    stored credential the resolver chose (#740) so downstream call-sites can
    stamp ``Jentic-Credential-*`` response headers, persist attribution on the
    execution record, and join the ``CREDENTIAL_ACCESSED`` audit event to an
    execution via ``trace_id``. Both are ``None`` when no credential was used
    (inline auth, credential-less API, or a resolve-fail before injection).
    """

    headers: dict[str, str]
    query_params: dict[str, str]
    cookies: dict[str, str]
    server_variables: dict[str, str] | None = None
    credential_id: str | None = None
    credential_name: str | None = None


class CredentialInjector(Protocol):
    """Resolves + decrypts the credential for an API tuple into applyable auth.

    Shared so the **same** broker credential service can be dependency-injected
    into the async worker (``ExecutionHandler``) without ``shared/jobs/``
    importing ``broker/``. Takes the resolved ``identity`` (a shared type) rather
    than a bare ``str`` so the service can distinguish actor types for future
    per-actor-type token exchange.
    """

    async def inject(
        self,
        *,
        api_vendor: str,
        api_name: str,
        api_version: str,
        identity: Identity,
        credential_name: str | None = None,
        trace_id: str | None = None,
    ) -> InjectedAuth:
        """Return the auth to apply; empty ``InjectedAuth`` when there is no credential path."""
        ...


@dataclass(frozen=True, slots=True)
class UpstreamExecRequest:
    """The upstream call the worker hands to the shared execution pipeline.

    Transport-neutral-ish (HTTP today) so ``shared/jobs/`` can describe an
    upstream call without importing the broker's ``RunnerRequest``. The broker's
    ``UpstreamExecutor`` adapter maps it onto the real pipeline.
    """

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout_s: float
    # Identity/discovery metadata for the execution record (vendor/name/version,
    # toolkit, operation, trace, execution id) — opaque to the worker, consumed
    # by the executor when it persists the ``executions`` row.
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UpstreamExecResult:
    """The pipeline's verbatim upstream result, returned to the worker.

    Mirrors the broker's ``RunnerResult`` shape without the worker importing it,
    so the handler can build its ``JobResultPayload`` + lifecycle event from a
    shared type.
    """

    status_code: int
    body: bytes
    content_type: str | None
    duration_ms: int


class UpstreamExecutor(Protocol):
    """Runs one upstream call through the **shared** ``BrokerExecutionPipeline``.

    The async worker depends on this protocol — never on ``broker/`` — so the
    concrete broker adapter (``PipelineExecutor``, wrapping
    ``run_execution(broker=default_broker(runner))``) can be dependency-
    injected at worker startup. This is the "one pipeline, two callers" seam
    (§00 / §05 / §11 RN-0.3): the worker goes through the **same** composed
    runner (circuit breaker + per-host bulkhead + post-response enrichment) and
    the **same** ``executions``-row persistence as the sync router, instead of a
    second raw-``httpx`` path.

    Implementations own the ``executions`` record write (the pipeline persists
    it); the worker keeps only the job-result + lifecycle-event persistence.
    """

    async def execute(
        self, request: UpstreamExecRequest, *, session: Any
    ) -> UpstreamExecResult: ...


class JobRecord(Protocol):
    """Minimal job record interface."""

    id: str
    kind: str
    status: str


class JobRepoProtocol(Protocol):
    """Protocol for job repository operations used by the worker."""

    async def create(
        self,
        session: Any,
        *,
        kind: Any,
        status: Any,
        parent_job_id: str | None = None,
        execution_id: str | None = None,
        error: str | None = None,
    ) -> JobRecord: ...

    async def get_by_id(self, session: Any, job_id: str) -> JobRecord | None: ...

    async def update(
        self,
        session: Any,
        job_id: str,
        *,
        status: Any | None = None,
        error: str | None = None,
        execution_id: str | None = None,
    ) -> JobRecord: ...

    async def claim_next_queued(self, session: Any) -> JobRecord | None: ...


class JobResultRepoProtocol(Protocol):
    """Protocol for job result repository operations used by the worker."""

    async def create(
        self,
        session: Any,
        *,
        job_id: str,
        kind: str,
        body: dict[str, Any],
        content_type: str | None = None,
        available_until: datetime | None = None,
    ) -> Any: ...

    async def delete_expired(self, session: Any) -> int: ...
