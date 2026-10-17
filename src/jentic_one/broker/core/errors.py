"""Broker domain-exception taxonomy.

Pure module — **no** FastAPI/httpx/DB imports. Core/services/adapters raise these
typed domain exceptions; the single FastAPI handler in ``broker/web/errors.py``
maps the taxonomy to ``application/problem+json``. This keeps the transport
coupling out of the domain/service layers: domain errors in, problem+json out.

Every error also carries the **agent-recovery contract**: an ``error_origin``
(``broker`` | ``upstream``: who generated the response — a ``BrokerError`` is
always the broker's own response, so it keeps the ``broker`` default) and,
where a deterministic recovery exists, a structured ``AgentDirective``
(``broker/core/directives.py``) so the autonomous-agent caller can recover
without reading docs.

Operation-discovery failures (operation not found, method not allowed, ambiguous
match, too many candidates) are not ``BrokerError`` subclasses: the registry
raises the same types, so they live in ``shared/discovery/errors.py`` and the
broker renders them through ``broker/core/problem.py``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from jentic_one.broker.core.directives import AgentDirective
from jentic_one.shared.broker.execution import ErrorOrigin


class BrokerError(Exception):
    """Base broker domain error.

    Carries the problem+json ``detail``/``type`` plus optional extension members,
    the ``error_origin`` and an optional ``AgentDirective``. ``headers`` are extra
    response headers the central handler should attach (e.g. ``Retry-After``).

    ``default_type`` is the problem ``type`` a subclass renders when the raise
    site passes none, so a class with a stable wire identity (one a client keys
    recovery on) never falls back to ``about:blank``.
    """

    default_type: ClassVar[str] = "about:blank"

    def __init__(
        self,
        detail: str,
        *,
        type: str | None = None,
        extra: dict[str, Any] | None = None,
        origin: ErrorOrigin = ErrorOrigin.BROKER,
        directive: AgentDirective | None = None,
        headers: dict[str, str] | None = None,
        instance: str | None = None,
        pre_send: bool = False,
        local_only: bool = False,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.type = type or self.default_type
        self.extra = extra or {}
        self.origin = origin
        self.directive = directive
        self.headers = headers or {}
        # RFC 9457 ``instance`` — the request path this problem occurred on, so
        # the body identifies the specific occurrence (kept consistent with the
        # sibling ``jentic.problem_details`` errors that already set it).
        self.instance = instance
        # Whether the failure happened *before any request bytes hit the wire*
        # (connect-phase). A pre-send failure is safe to retry for ANY method —
        # the upstream cannot have acted; a post-send failure (read timeout, drop
        # after send) is only safe to retry for idempotent HTTP methods (#1799).
        # Defaults to False (assume bytes were sent).
        self.pre_send = pre_send
        # Whether the failure is local to the broker's own shared resources
        # (e.g. outbound connection-pool exhaustion) rather than the target
        # upstream host. A broker-local failure is excluded from per-host
        # circuit-breaker accounting so one host saturating the shared pool
        # cannot trip another host's breaker (#1795). Defaults to False.
        self.local_only = local_only


class UpstreamUrlNotAllowedError(BrokerError):
    """SSRF boundary: private-IP / metadata host / unformable upstream URL (400)."""


class UpgradeNotSupportedError(BrokerError):
    """A protocol-upgrade attempt (WebSocket / h2c) the buffered proxy can't carry (426)."""


class PayloadTooLargeError(BrokerError):
    """Request body exceeds the broker body cap (413)."""


class MutationRequiresIdempotencyKeyError(BrokerError):
    """An opt-in agent-safety guard requiring an Idempotency-Key on a mutation (428)."""


class IdempotencyConflictError(BrokerError):
    """An Idempotency-Key was reused with a *different* request fingerprint (409)."""


class IdempotencyInProgressError(BrokerError):
    """An Idempotency-Key retry arrived while the original is still in-flight (409).

    Distinct ``type`` from :class:`IdempotencyConflictError` (same status) so the
    client can tell "same key, different body" (don't retry) from "original still
    running" (retry after ``Retry-After``).
    """


class IdempotencyUnavailableError(BrokerError):
    """The Idempotency-Key claim could not be recorded, so the call was not run (503).

    Raised when the shared-state backend refuses the claim write (the
    in-process backend's ``idem`` namespace is full). The call is refused
    rather than run unclaimed, since an unrecorded claim cannot dedupe a retry.
    """


class InvalidRevisionPinError(BrokerError):
    """A ``Jentic-Revision`` pin that fails the spec regex / is unknown / is archived (422).

    Covers the parameter-shape failure (malformed value), an unknown revision,
    and an ``archived`` revision — all parameter-validation failures the agent
    fixes by changing the header (same 422 class as FastAPI ``RequestValidationError``).
    """


class UnauthorizedRevisionPinError(BrokerError):
    """A ``Jentic-Revision`` pin to a ``draft`` revision the caller may not access (403).

    Distinct from :class:`InvalidRevisionPinError` (422): the pin is well-formed
    and the revision exists, but it is an unpublished ``draft`` the caller does
    not own — an entitlement failure, not a parameter-shape failure.
    """


class CircuitOpenError(BrokerError):
    """The circuit breaker is open for the target upstream (503).

    ``report`` says whether this refusal is the one that reports the open
    circuit (the ``upstream.circuit_open`` event). The breaker sets it for
    one refusal per host per open period, shared across processes through
    the state backend; a raise site without a breaker leaves it ``True``.
    """

    def __init__(self, detail: str, *, report: bool = True, **kwargs: Any) -> None:
        super().__init__(detail, **kwargs)
        self.report = report


class RateLimitExceededError(BrokerError):
    """A broker-side rate limit was exceeded (429)."""


class CredentialNotProvisionedError(BrokerError):
    """No credential is provisioned for the resolved API/caller (424)."""


class CredentialUndecryptableError(BrokerError):
    """A resolved credential's stored ciphertext cannot be decrypted (424).

    Same-class failure as :class:`CredentialNotProvisionedError` (the request
    is fine; a stored dependency is unusable), but a different recovery: the
    caller cannot re-provision itself — an operator has to re-add the
    credential because the encryption key that produced the stored blob is
    gone. Common cause: a reinstall regenerated the encryption keyset under
    the same key id, so lookup succeeds and AES-GCM authentication fails
    (see ``shared/crypto/encryption.py`` — ``InvalidTag`` →
    :class:`~jentic_one.shared.crypto.DecryptionError`); other paths include
    a hand-rotated key without keeping the retired entry, a
    corrupted row, or a database restored under a different key. The
    ``prompt_human`` directive tells the agent to escalate to an operator
    rather than retry. Never carry ciphertext or key material in the body
    (redaction rule).
    """


class CredentialNeedsReconnectError(BrokerError):
    """The OAuth2 grant was revoked/disconnected — the caller must reconnect (401)."""


class CredentialRefreshTransientError(BrokerError):
    """The IdP returned a transient error during token refresh (502).

    ``broker`` origin: the refresh runs before dispatch, so the upstream API
    never saw the call and the 502 is the broker's own response.
    """

    default_type = "refresh_transient_error"


class InvalidCredentialNameError(BrokerError):
    """The Jentic-Credential-Name header value doesn't match any active credential (400)."""


class AmbiguousCredentialMatchError(BrokerError):
    """More than one of the caller's credentials covers the call and none is chosen (409).

    The raise site sets the wire ``type`` (``ambiguous_credential_binding`` on
    the direct-binding path, ``ambiguous_credential`` otherwise) and carries the
    candidates so the caller can pick one with ``Jentic-Credential-Id``.
    """


class ActionDeniedError(BrokerError):
    """The request was denied by a permission rule on the caller's binding (403)."""


class ApprovalPendingLimitError(BrokerError):
    """The agent already holds the configured maximum of pending approvals (403)."""


class ApprovalHoldUnavailableError(BrokerError):
    """A require-approval rule matched but this deployment cannot hold the call (503).

    A held call's payload is encrypted at rest with the credentials keyset;
    without one configured there is nowhere safe to keep it, so the call is
    refused rather than run or stored in plaintext.
    """


class CredentialIdentityMismatchError(BrokerError):
    """Bound, but no bound credential's identity covers this API (403).

    Distinct from :class:`ActionDeniedError` and the missing-binding denial
    (``no_credential_binding``): the agent *is* bound, but the bound
    credential's stored API identity does not cover the resolved operation
    identity (#747/#748). The recovery is to fix/re-provision the
    **credential** — this is not agent-recoverable, and requesting a new
    binding would not help.
    """


class CredentialBindingSuspendedError(BrokerError):
    """The agent's binding to a credential covering this API is suspended (403).

    Distinct from the missing-binding denial (``no_credential_binding``): the
    operator suspended the agent's access, so only the operator can restore
    it (resume the binding). The directive never suggests a new connect —
    the connect flow refuses one while the suspension stands.
    """

    default_type = "credential_binding_suspended"


class UpstreamTimeoutError(BrokerError):
    """The upstream did not respond within the deadline, or the pool timed out (504).

    ``broker`` origin: the broker gave up and generated this response; no
    upstream answer exists. Typed ``upstream_timeout`` (read phase, the request
    may have reached the upstream) or ``upstream_connect_failed`` (connect or
    pool-acquire phase, ``pre_send``: no request bytes were sent; pool
    exhaustion also sets ``local_only=True``).
    """

    default_type = "upstream_timeout"


class UpstreamTransportError(BrokerError):
    """The broker could not talk to the upstream at the transport level (502).

    The upstream dropped the connection (including partway through the response
    body), refused it, or broke the HTTP protocol, so no usable upstream answer
    exists. ``broker`` origin: the broker generated this response. Typed
    ``upstream_transport_error`` (the request may have reached the upstream) or
    ``upstream_connect_failed`` (connect phase, ``pre_send``: no request bytes
    were sent). A pool, connect, or read *timeout* is
    :class:`UpstreamTimeoutError` (504) instead.
    """

    default_type = "upstream_transport_error"


class DeadlineExceededError(BrokerError):
    """The overall request deadline elapsed before the call completed (504).

    Distinct from :class:`UpstreamTimeoutError` (a single attempt's connect/read
    timeout): this is the **whole-call wall-clock budget** the ``DeadlineRunner``
    enforces *around* the transport (and, once it lands, the retry loop). Same
    ``504`` class, but the cause is "we ran out of time across the envelope", not
    "one attempt stalled". ``broker`` origin — the broker cut the call off and
    generated this response — and the agent's directive points at the
    async-pivot / wait recovery.
    """

    default_type = "deadline_exceeded"


class UpstreamResponseTooLargeError(BrokerError):
    """The upstream response body exceeded the broker's response-size cap (502).

    Enforced mid-stream so a hostile/large upstream can't OOM the instance — the
    response-side counterpart to :class:`PayloadTooLargeError`. ``broker`` origin
    (the broker aborted the vendor's response and generated this one); the
    vendor sent too much, so the agent can ``switch_toolkit`` to an alternative
    vendor.
    """

    default_type = "upstream_response_too_large"


class UpstreamBodyInterruptedError(BrokerError):
    """The upstream failed at the transport level partway through a streamed body.

    Raised from the streaming body iterator, after the response status line has
    been sent, so it never becomes an error response: the streaming edge logs
    it as ``upstream_network_failure``, aborts the response, and records
    ``transport_error`` on the execution. ``transport_error`` is the ``httpx``
    exception class name (never its message, which can quote request
    material). Typed ``upstream_timeout`` for a read stall, else
    ``upstream_transport_error``. The streaming edge classifies by this class,
    never by the problem ``type`` or ``extra`` members.
    """

    default_type = "upstream_transport_error"

    def __init__(self, detail: str, *, transport_error: str, type: str | None = None) -> None:
        super().__init__(detail, type=type)
        self.transport_error = transport_error


class RunnerSchemeUnsupportedError(BrokerError):
    """No runner is registered for the upstream URL's scheme (501).

    The broker has no transport that can speak this scheme (e.g. an ``ftp://``
    upstream when only the HTTP runner is registered). Distinct from an
    ``OperationNotFoundError`` (``shared/discovery/errors.py``; 404 — the scheme
    is known but the URL maps to no operation): here the broker simply *cannot*
    carry the protocol, so the capability gap is the agent's to resolve (use a
    different upstream).
    """


class RunnerUnavailableError(BrokerError):
    """A runner exists for the scheme but is degraded/unavailable (503).

    A runner whose ``startup()`` failed is marked unavailable rather than aborting
    app start (so a flaky non-HTTP runner never blocks HTTP proxying). A request
    routed to that scheme is *rejected, not dropped* — a transient ``503`` the
    agent can retry — distinct from :class:`RunnerSchemeUnsupportedError` (501,
    no such runner at all).
    """
