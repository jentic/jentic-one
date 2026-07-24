"""Event-related enums shared across modules."""

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
    # agent-needed APIs before a doomed access request appears.
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
            INSTANCE_INITIALIZED,
            INSTANCE_BOOTED,
            CREDENTIAL_STORED,
            CREDENTIAL_CONNECTED,
            CREDENTIAL_CONNECTION_FAILED,
            CREDENTIAL_REFRESH_FAILED,
            CREDENTIAL_NOT_PROVISIONED,
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


#: Union of every closed-enum tag type. A tag on the wire is always a member of
#: one of these — there is deliberately no free-form variant.
EventTag = ErrorSource | SpecSource | ImportFailReason


#: Which closed-enum tag type each event may carry. ``emit_event`` validates
#: supplied tags against this map: a tag whose type is not allowed for the event
#: is dropped (with a logged warning) and the event still emits. An event absent
#: from this map accepts no tags.
EVENT_TAGS: dict[str, type[StrEnum]] = {
    EventType.EXECUTION_FAILED: ErrorSource,
    EventType.CREDENTIAL_REFRESH_FAILED: ErrorSource,
    EventType.IMPORT_COMPLETED: SpecSource,
    EventType.IMPORT_FAILED: ImportFailReason,
}
