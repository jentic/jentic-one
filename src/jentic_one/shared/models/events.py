"""Event-related enums shared across modules."""

import platform
from enum import StrEnum


class EventSeverity(StrEnum):
    """Severity level for platform events."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventType:
    """Namespaced event type constants."""

    IMPORT_COMPLETED = "import.completed"
    IMPORT_FAILED = "import.failed"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    EXECUTION_REPEATED_FAILURE = "execution.repeated_failure"
    CREDENTIAL_EXPIRING_SOON = "credential.expiring_soon"
    CREDENTIAL_EXPIRED = "credential.expired"
    CREDENTIAL_ACCESSED = "credential.accessed"
    ACCESS_REQUEST_FILED = "access_request.filed"
    ACCESS_REQUEST_APPROVED = "access_request.approved"
    ACCESS_REQUEST_DENIED = "access_request.denied"
    ACCESS_REQUEST_WITHDRAWN = "access_request.withdrawn"
    UPSTREAM_CIRCUIT_OPEN = "upstream.circuit_open"
    JOB_FAILED_PERMANENTLY = "job.failed_permanently"
    UNAUTHORIZED_ACCESS_ATTEMPT = "security.unauthorized_access_attempt"
    # A registered catalog/imported API's upstream spec changed (detected by the
    # update-notify sweep). Emitted with requires_action=True — the operator resolves
    # it by re-importing the upstream spec (one-click in the UI / `jentic catalog
    # outdated` in the CLI), which the ImportHandler settles via
    # settle_actionable_events. Deduped on the observed spec digest so it fires once
    # per real change, not every sweep.
    CATALOG_UPDATE_AVAILABLE = "catalog.update_available"
    # A registered API's upstream spec changed AND that change collides with a
    # confirmed overlay's *base* (the overlay was materialized over an older base;
    # the upstream now differs from that base). Distinct from CATALOG_UPDATE_AVAILABLE
    # because the resolution differs: adopting the upstream would *supersede* the
    # operator's overlay, so this is an operator decision (re-import → auto-deprecate,
    # gated) rather than a routine "update available" nudge. Emitted by the Flow-3
    # sweep once per (digest, class) pair.
    CATALOG_UPDATE_CONFLICTS_OVERLAY = "catalog.update_conflicts_overlay"

    # An overlay's lifecycle changed in a way a human should see beyond the audit log
    # (L2/L3): today emitted when an authorized catalog re-import auto-deprecates a
    # live confirmed overlay (A4b). Carries the deprecating actor + reason so the
    # overlay author is attributed/notified and the UI can surface "deprecated by
    # re-import on <date>" rather than a silent status flip behind the audit log.
    # requires_action=False — it is a notification, not an inbox item.
    OVERLAY_DEPRECATED = "overlay.deprecated"

    # --- Product-telemetry event types (issue #446) ----------------------
    # These flow through emit_event (the single entry point) like any other
    # internal event; the ones present in TELEMETRY_EVENTS are also forwarded
    # to the anonymous product-telemetry sink when telemetry is enabled.
    INSTANCE_INITIALIZED = "instance.initialized"
    INSTANCE_BOOTED = "instance.booted"
    CREDENTIAL_STORED = "credential.stored"
    CREDENTIAL_CONNECTED = "credential.connected"
    CREDENTIAL_CONNECTION_FAILED = "credential.connection_failed"
    CREDENTIAL_REFRESH_FAILED = "credential.refresh_failed"
    CREDENTIAL_NOT_PROVISIONED = "credential.not_provisioned"
    CREDENTIAL_UNDECRYPTABLE = "credential.undecryptable"
    CREDENTIAL_BOUND_TO_TOOLKIT = "credential.bound_to_toolkit"
    CREDENTIAL_UNBOUND_FROM_TOOLKIT = "credential.unbound_from_toolkit"
    TOOLKIT_CREATED = "toolkit.created"
    TOOLKIT_KEY_CREATED = "toolkit.key_created"
    TOOLKIT_PERMISSION_RULE_SET = "toolkit.permission_rule_set"
    TOOLKIT_BOUND_TO_AGENT = "toolkit.bound_to_agent"
    TOOLKIT_UNBOUND_FROM_AGENT = "toolkit.unbound_from_agent"
    AGENT_CREATED = "agent.created"
    AGENT_SELF_REGISTERED = "agent.self_registered"
    AGENT_REGISTRATION_APPROVED = "agent.registration_approved"
    AGENT_REGISTRATION_DENIED = "agent.registration_denied"
    PBAC_DENIED = "broker.pbac_denied"
    # Emitted when the broker denies an execute with 403 ``no_toolkit_binding``
    # AND no toolkit yet serves the requested API — the caller's next step is
    # for an operator to provision a credential (which is what makes a toolkit
    # serve the API). Distinct from ``CREDENTIAL_NOT_PROVISIONED`` (424, fires
    # when a bound toolkit's credential is unresolvable at inject time): this
    # event is the *pre-binding* signal, giving operators visibility into
    # agent-needed APIs before a doomed access request appears. Despite the
    # ``broker.`` namespace, the control plane also emits it as a file-time
    # advisory for the same condition (see
    # ``AccessRequestService._advise_unserved_bind_references``).
    TOOLKIT_BINDING_UNSERVED = "broker.toolkit_binding_unserved"

    ALL: frozenset[str] = frozenset(
        {
            IMPORT_COMPLETED,
            IMPORT_FAILED,
            EXECUTION_COMPLETED,
            EXECUTION_FAILED,
            EXECUTION_REPEATED_FAILURE,
            CREDENTIAL_EXPIRING_SOON,
            CREDENTIAL_EXPIRED,
            CREDENTIAL_ACCESSED,
            ACCESS_REQUEST_FILED,
            ACCESS_REQUEST_APPROVED,
            ACCESS_REQUEST_DENIED,
            ACCESS_REQUEST_WITHDRAWN,
            UPSTREAM_CIRCUIT_OPEN,
            JOB_FAILED_PERMANENTLY,
            UNAUTHORIZED_ACCESS_ATTEMPT,
            CATALOG_UPDATE_AVAILABLE,
            CATALOG_UPDATE_CONFLICTS_OVERLAY,
            OVERLAY_DEPRECATED,
            INSTANCE_INITIALIZED,
            INSTANCE_BOOTED,
            CREDENTIAL_STORED,
            CREDENTIAL_CONNECTED,
            CREDENTIAL_CONNECTION_FAILED,
            CREDENTIAL_REFRESH_FAILED,
            CREDENTIAL_NOT_PROVISIONED,
            CREDENTIAL_UNDECRYPTABLE,
            CREDENTIAL_BOUND_TO_TOOLKIT,
            CREDENTIAL_UNBOUND_FROM_TOOLKIT,
            TOOLKIT_CREATED,
            TOOLKIT_KEY_CREATED,
            TOOLKIT_PERMISSION_RULE_SET,
            TOOLKIT_BOUND_TO_AGENT,
            TOOLKIT_UNBOUND_FROM_AGENT,
            AGENT_CREATED,
            AGENT_SELF_REGISTERED,
            AGENT_REGISTRATION_APPROVED,
            AGENT_REGISTRATION_DENIED,
            PBAC_DENIED,
            TOOLKIT_BINDING_UNSERVED,
        }
    )


class ErrorSource(StrEnum):
    """Closed-enum tag splitting *where* a failure originated.

    Attached to failure events (``broker_execution_failed``,
    ``credential_refresh_failed``) — a fixed, anonymous classifier, never a
    free-form error string.
    """

    AUTH_JENTIC = "auth_jentic"
    AUTH_THIRDPARTY = "auth_thirdparty"
    # Granular third-party rejections, split by upstream status so the funnel can
    # tell a credential rejection (401) apart from a permission/business denial
    # (403) — both mean "the auth the user configured was rejected upstream", but
    # at different precision (401 is RFC-tight; 403 mixes auth + authorization).
    AUTH_THIRDPARTY_UNAUTHORIZED = "auth_thirdparty_unauthorized"
    AUTH_THIRDPARTY_FORBIDDEN = "auth_thirdparty_forbidden"
    # DEFERRED (v1): the catalog-vs-imported spec-provenance split on
    # broker_execution_failed is not wired yet — the spec's provenance is not
    # carried on ExecuteRequestContext/ResolveResult at execution-failure time.
    # See docs/plans/issue-446-product-telemetry.md items 14/18 (deferral note).
    UPSTREAM_CATALOG = "upstream_catalog"
    UPSTREAM_IMPORTED = "upstream_imported"


class SpecSource(StrEnum):
    """Closed-enum tag splitting a spec import by provenance.

    DEFERRED (v1): declared and validated for ``IMPORT_COMPLETED`` but not yet
    passed at the ``worker.py`` emit point — the catalog-vs-local provenance is
    not threaded into the job result today. See
    docs/plans/issue-446-product-telemetry.md item 18 (deferral note).
    """

    CATALOG = "catalog"
    LOCAL = "local"


class ImportFailReason(StrEnum):
    """Closed-enum tag splitting a spec-import failure by phase.

    DEFERRED (v1): declared and validated for ``IMPORT_FAILED`` but not yet
    passed at the emit point — the import handler stringifies all
    ``IngestStageError``s uniformly, so validation-vs-fetch is indistinguishable
    at ``_terminal_job`` without typed exceptions. See
    docs/plans/issue-446-product-telemetry.md item 18 (deferral note).
    """

    VALIDATION = "validation"
    FETCH = "fetch"


class HostOs(StrEnum):
    """Closed-enum tag naming the host OS family, sent once per boot.

    Attached only to ``instance_booted`` — the lifecycle event emitted on every
    startup — so the OS rides on one request per boot under the same opaque
    instance id, and on no other event. Per-boot (rather than once-ever)
    matches how comparable products report environment facts (n8n's "Instance
    started", GitLab's Service Ping, Grafana's usage report) and lets the
    dimension self-heal: a lost request or a config moved to another machine
    is corrected on the next boot.

    Detection order matters because the recommended install runs the backend in
    Docker, where ``platform.system()`` reports the *container's* kernel
    (always Linux), not the operator's machine. The onboarding CLI runs on the
    host, so it stamps ``telemetry.host_os`` (from Go's ``runtime.GOOS``) into
    the generated config; ``resolve`` prefers that and only falls back to
    runtime detection for hand-rolled configs. Anything unrecognised collapses
    to ``OTHER``, so no free-form platform string can reach the wire.
    """

    LINUX = "linux"
    DARWIN = "darwin"
    WINDOWS = "windows"
    OTHER = "other"

    @classmethod
    def current(cls) -> "HostOs":
        """Classify the running platform into the closed set."""
        system = platform.system().lower()
        try:
            return cls(system)
        except ValueError:
            return cls.OTHER

    @classmethod
    def resolve(cls, configured: str | None) -> "HostOs":
        """Prefer the install-time config value, else detect at runtime.

        ``configured`` is the raw ``telemetry.host_os`` string; surrounding
        whitespace is forgiven (a quoted hand-edit like ``" darwin "``), but a
        value outside the closed set degrades to OTHER rather than falling back
        to runtime detection — a stamped-but-garbled value must not silently
        become the container's OS.
        """
        if configured is None or not configured.strip():
            return cls.current()
        try:
            return cls(configured.strip().lower())
        except ValueError:
            return cls.OTHER


#: Union of every closed-enum tag type. A tag on the wire is always a member of
#: one of these — there is deliberately no free-form variant.
EventTag = ErrorSource | SpecSource | ImportFailReason | HostOs


#: Which closed-enum tag type each event may carry. ``emit_event`` validates
#: supplied tags against this map: a tag whose type is not allowed for the event
#: is dropped (with a logged warning) and the event still emits. An event absent
#: from this map accepts no tags.
EVENT_TAGS: dict[str, type[StrEnum]] = {
    EventType.EXECUTION_FAILED: ErrorSource,
    EventType.CREDENTIAL_REFRESH_FAILED: ErrorSource,
    EventType.IMPORT_COMPLETED: SpecSource,
    EventType.IMPORT_FAILED: ImportFailReason,
    EventType.INSTANCE_BOOTED: HostOs,
}
