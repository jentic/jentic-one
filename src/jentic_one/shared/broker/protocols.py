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


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """Outcome of a permission-rule evaluation with just enough context to explain a deny.

    ``allowed`` is the same signal the pre-#578 bare-bool evaluator emitted.
    ``rules_loaded`` distinguishes two very different deny paths that used to
    collapse into one bare 403 (#578): a zero-length pool (nothing matched
    because there was nothing to match — wrong vendor, unbound credential,
    empty binding, misconfigured store) vs a non-empty pool where no rule
    happened to match. The router turns this into a two-variant detail
    sentence — no rule contents, no internal ids, redaction-safe.
    """

    allowed: bool
    rules_loaded: int


@runtime_checkable
class RuleEvaluatorProtocol(Protocol):
    """Evaluates toolkit permission rules against an inbound request.

    Returns a :class:`RuleEvaluation` with the allow/deny outcome and the
    size of the vendor-pooled rule list evaluated. Evaluation follows
    first-match-wins over an ordered rule list; an exhausted list defaults
    to deny (secure-by-default).
    """

    async def evaluate(
        self,
        *,
        toolkit_id: str,
        method: str,
        path: str,
        operation_id: str | None,
        api_vendor: str = "",
    ) -> RuleEvaluation: ...


@runtime_checkable
class ToolkitBindingCheckerProtocol(Protocol):
    """Checks whether an agent has a binding to a specific toolkit."""

    async def has_binding(self, agent_id: str, toolkit_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class IdentityMismatch:
    """A nearest-miss credential identity for an unresolved-but-bound API.

    Populated when the agent is bound to toolkit(s) but no credential's stored
    identity covers the (concrete) operation identity — the #747/#748 case. All
    fields are plain strings so the directive layer can serialize them directly
    without touching a pydantic model.
    """

    expected_vendor: str
    expected_name: str
    expected_version: str
    found_vendor: str
    found_name: str | None
    found_version: str | None
    would_match_if_normalized: bool


@dataclass(frozen=True, slots=True)
class ToolkitDerivation:
    """Result of toolkit derivation, carrying *why* the toolkit set may be empty.

    ``toolkits`` is the intersection callers use (``()`` → 403, one → use it,
    many → 409). The remaining fields let the broker distinguish the empty cases
    and emit the right recovery directive without a second DB round-trip:

    - ``agent_bound_any`` — the agent has at least one toolkit binding at all.
    - ``api_served_toolkits`` — every toolkit whose bound credential covers the
      API (independent of the agent). ``()`` means no toolkit serves the API yet;
      this subsumes the old ``any_toolkit_serves_api`` probe. **These ids can
      belong to other owners** (the derivation is agent-independent), so only its
      *truthiness* may be consumed here — the raw ids must not be serialized into
      a directive/response without owner-scoping, or they'd leak cross-tenant
      toolkit ids.
    - ``identity_mismatch`` — a nearest-miss for the diagnostic when the agent is
      bound but nothing serves the API because a bound credential's identity does
      not cover the operation.
    """

    toolkits: tuple[str, ...]
    agent_bound_any: bool
    api_served_toolkits: tuple[str, ...]
    identity_mismatch: IdentityMismatch | None


@runtime_checkable
class ToolkitDeriverProtocol(Protocol):
    """Derives which of an agent's toolkits contain a given API identity.

    Empty ``toolkits`` → 403, one → use it, many → 409 (caller disambiguates with
    the ``Jentic-Toolkit-Id`` header). The full :class:`ToolkitDerivation` also
    carries why an empty set is empty so the denial can pick the right directive.
    """

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> ToolkitDerivation: ...


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
