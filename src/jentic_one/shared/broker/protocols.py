"""Protocols and data types for broker token resolution and toolkit binding checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.schemas import APIReference


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """A resolved operation with its API identity and extracted path parameters."""

    operation_id: str
    api: APIReference
    path_params: dict[str, str]


class RevisionPinOutcome(StrEnum):
    """Outcome of resolving a ``Jentic-Revision`` pin to a concrete revision.

    A neutral, transport-free enum so the resolver (which lives in ``registry/``)
    never leaks registry exception types across the architecture boundary — the
    broker maps each outcome to its own domain exception (§10).
    """

    RESOLVED = "resolved"
    """The pin resolved to a usable revision (``published`` or an owned ``draft``)."""

    UNKNOWN = "unknown"
    """The API or revision label does not exist (→ 422)."""

    ARCHIVED = "archived"
    """The revision exists but is ``archived`` — not pinnable from the hot path (→ 422)."""

    FORBIDDEN = "forbidden"
    """The revision is an unpublished ``draft`` the caller does not own (→ 403)."""


@dataclass(frozen=True, slots=True)
class RevisionPinResult:
    """The resolved ``revision_id`` (when ``RESOLVED``) plus the classifying outcome."""

    outcome: RevisionPinOutcome
    revision_id: uuid.UUID | None = None


@runtime_checkable
class RegistryResolverProtocol(Protocol):
    """Resolves a METHOD+URL to an operation + API identity against the registry.

    The broker depends only on this protocol; the concrete implementation (today
    an in-process registry-DB read, potentially an HTTP client later) is injected
    onto app state at startup so the broker never imports ``jentic_one.registry``.
    """

    async def resolve_operation(
        self, *, method: str, url: str, revision_id: uuid.UUID | None = None
    ) -> ResolveResult | None: ...

    async def resolve_revision_pin(
        self,
        *,
        vendor: str,
        name: str,
        version: str,
        rev_label: str,
        identity: Identity,
    ) -> RevisionPinResult:
        """Translate a ``vendor:name:version=rev_…`` pin to a ``revision_id`` (§10).

        Performs the in-process lookup + access-rule check and returns a neutral
        :class:`RevisionPinResult`; it never raises a registry-specific exception
        across the boundary. The broker maps the outcome to its domain taxonomy
        (``UNKNOWN``/``ARCHIVED`` → 422, ``FORBIDDEN`` → 403).
        """
        ...


@runtime_checkable
class TokenResolverProtocol(Protocol):
    """Resolves an opaque access token to actor identity."""

    async def resolve_access_token(self, token: str) -> Identity | None: ...


@runtime_checkable
class RuleEvaluatorProtocol(Protocol):
    """Evaluates toolkit permission rules against an inbound request.

    Returns ``True`` if the request is allowed, ``False`` if denied. Evaluation
    follows a first-match-wins policy over an ordered rule list; an exhausted
    list defaults to deny (secure-by-default).
    """

    async def evaluate(
        self,
        *,
        toolkit_id: str,
        method: str,
        path: str,
        operation_id: str | None,
        api_vendor: str = "",
    ) -> bool: ...


@runtime_checkable
class ToolkitBindingCheckerProtocol(Protocol):
    """Checks whether an agent has a binding to a specific toolkit."""

    async def has_binding(self, agent_id: str, toolkit_id: str) -> bool: ...


@runtime_checkable
class ToolkitDeriverProtocol(Protocol):
    """Derives which of an agent's toolkits contain a given API identity.

    ``[]`` → 403, ``[one]`` → use it, ``[many]`` → 409 (caller disambiguates with
    the ``Jentic-Toolkit-Id`` header).
    """

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> list[str]: ...

    async def any_toolkit_serves_api(self, *, vendor: str, name: str, version: str) -> bool:
        """Return whether *any* toolkit (for any owner) serves the given API.

        A toolkit only "serves" an API once a credential for it is bound. This
        distinguishes the "no toolkit exists yet — provision a credential first"
        state from the "a toolkit serves it but this agent isn't bound" state, so
        a ``no_toolkit_binding`` denial can hand the caller a recovery step that
        can actually be completed (see issue #683). Unscoped by design: it drives
        recovery guidance, not authorization.
        """
        ...


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """Outcome of an idempotency claim attempt.

    Attributes:
        claimed: ``True`` if this caller won the claim and should execute the
            operation; ``False`` if a prior request already claimed the key.
        existing_response: When ``claimed`` is ``False`` and the prior request
            has completed, the stored response bytes to replay; ``None`` if the
            prior request is still in flight (caller should treat as a conflict).
    """

    claimed: bool
    existing_response: bytes | None = None


@runtime_checkable
class IdempotencyStore(Protocol):
    """Cross-instance idempotency: claim a key, then store the final response.

    The concrete implementation (§07) is backed by an ``AtomicStore`` so claims
    are atomic across broker instances.
    """

    async def claim(self, caller: str, key: str, fingerprint: str) -> IdempotencyClaim: ...

    async def complete(self, caller: str, key: str, response: bytes) -> None: ...


@runtime_checkable
class TelemetrySink(Protocol):
    """Receives broker telemetry records.

    Intentionally permissive (``record: object``) — the concrete record shape and
    sink implementation land in a later PR.
    """

    async def record(self, record: object) -> None: ...


# ---------------------------------------------------------------------------
# Transport-neutral runner value objects (RN-0.1)
#
# These types are the *transport-neutral* foundation for the pluggable upstream
# runners roadmap (design: ``docs/design/designs/broker/impl/11-pluggable-runners.md``).
# They are deliberately **not** HTTP-shaped: the web layer maps an HTTP method to a
# neutral :class:`Verb`, headers travel in ``metadata`` as an ``HttpRunner`` detail,
# and ``code`` is a normalised result code rather than "an HTTP status".
#
# RN-0.1 lands these alongside the existing HTTP-shaped
# ``broker/adapters/runners/base.py`` objects (``RunnerRequest``/``RunnerResult``/
# ``UpstreamRunner``), which stay live and unchanged. The incremental migration of
# the live runner path onto :class:`PluggableUpstreamRunner` (registry, capability
# gating, the decorator envelope) is deferred to later §11 sub-PRs.
# ---------------------------------------------------------------------------


class Verb(StrEnum):
    """Neutral upstream operation vocabulary — **not** an HTTP method.

    The web layer maps an inbound HTTP method to one of these verbs; runners map a
    verb to their transport-native operation. New verbs are added here as non-HTTP
    runners land (e.g. ``PUBLISH`` for MQTT) — a bare verb string is never used.
    """

    GET = "get"
    PUT = "put"
    POST = "post"
    DELETE = "delete"
    PUBLISH = "publish"


@dataclass(frozen=True, slots=True)
class Target:
    """A parsed upstream destination — **not** a URL string.

    Holds the structured pieces a runner needs to reach an upstream. ``extra`` carries
    transport-specific addressing (e.g. an MQTT ``topic``) so the shared type stays
    free of any one transport's vocabulary.
    """

    scheme: str
    host: str
    port: int | None = None
    path: str = "/"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UpstreamRequest:
    """A transport-neutral upstream request handed to a :class:`PluggableUpstreamRunner`."""

    target: Target
    verb: Verb
    payload: bytes | None = None
    options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UpstreamResult:
    """A transport-neutral upstream result.

    ``code`` is a **normalised** result code (the web layer maps it back to an HTTP
    status for the ``HttpRunner`` 1:1 case), not inherently "an HTTP status".
    """

    ok: bool
    code: int
    payload: bytes
    content_type: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunnerCapabilities:
    """What a runner can do — used by later sub-PRs to gate the execution envelope."""

    verbs: frozenset[Verb]
    credential_types: frozenset[CredentialType]
    one_shot_only: bool
    max_payload_bytes: int
    supports_async: bool
    supports_idempotency: bool
    supports_retries: bool


@runtime_checkable
class EgressPolicy(Protocol):
    """Minimal placeholder for the scheme-aware egress policy (RN-1.1, deferred).

    The real ``EgressPolicy`` (per-runner allowed schemes, host allowlists,
    private-IP/metadata blocking, DNS pinning) is built in RN-1.1 on top of §08/E2.
    RN-0.1 only needs a type for the :meth:`PluggableUpstreamRunner.validate_target`
    signature; this defines just the ``check`` shape so the protocol is mypy-strict
    clean without prematurely building the full policy.
    """

    def check(self, target: Target) -> None: ...


@runtime_checkable
class PluggableUpstreamRunner(Protocol):
    """Transport-neutral, pooled upstream runner (RN-0.1 foundation protocol).

    This is the neutral successor to the HTTP-shaped
    ``broker/adapters/runners/base.py::UpstreamRunner``; that one stays live and is
    migrated onto this shape in a later §11 sub-PR. Runners are **long-lived pooled
    objects** — ``startup()``/``aclose()`` own the connection lifecycle — and apply
    the credential **inside** :meth:`run` (HTTP sets a per-request header; MQTT/FTP
    authenticate at connection establishment).

    ``credential`` is typed ``object | None`` for now: the concrete resolved-credential
    type lives in ``broker/services/credentials`` and ``shared`` must not import
    ``broker`` (layering, enforced by ``tests/arch/test_module_boundaries.py``). The
    credential-application slice that tightens this type lands in a later sub-PR.
    """

    name: str

    def capabilities(self) -> RunnerCapabilities: ...

    async def startup(self) -> None: ...

    async def aclose(self) -> None: ...

    def validate_target(self, req: UpstreamRequest, policy: EgressPolicy) -> None: ...

    async def run(self, req: UpstreamRequest, credential: object | None) -> UpstreamResult: ...
