"""Actor-related enums shared across modules."""

from enum import StrEnum


class ActorType(StrEnum):
    """Type of authenticated actor: a human user or an agent.

    Historical records may carry older actor-type values that are no longer
    issued; treat unrecognised values as opaque labels.
    """

    # Retired members (kept out of the docstring, which is published in the
    # OpenAPI spec): ``toolkit`` (theme-5 Phase 4) and ``service_account``
    # (theme-8 Phase 4). Their strings survive on historical rows (audit,
    # execution records, telemetry, control-DB actor-id columns); read paths use
    # actor_type_label_from_id or treat the string as opaque, never
    # ActorType(...), which would raise.
    USER = "user"
    AGENT = "agent"


class Origin(StrEnum):
    """Request origin surface — how the action was initiated."""

    CLI = "cli"
    DASHBOARD = "dashboard"
    API = "api"
    AGENT = "agent"
    SYSTEM = "system"
    MCP = "mcp"


def origin_or_none(value: str | None) -> Origin | None:
    """Coerce a persisted/threaded origin string back to the enum.

    Emit sites receive the origin as a plain string (job payloads, persisted
    execution rows); an absent or unrecognised value degrades to ``None`` so a
    garbage origin can never become an event tag.
    """
    if not value:
        return None
    try:
        return Origin(value)
    except ValueError:
        return None


_PREFIX_TO_ACTOR_TYPE: dict[str, ActorType] = {
    "usr_": ActorType.USER,
    "agnt_": ActorType.AGENT,
}

#: Id prefix of the retired service-account actor (theme 8) and the
#: ``actor_type`` string its historical rows carry. Not an ActorType member:
#: it can never be an identity again, only a label on residual rows.
RETIRED_SERVICE_ACCOUNT_ID_PREFIX = "sva_"
RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE = "service_account"


def actor_type_from_id(actor_id: str) -> ActorType:
    """Derive ActorType from a prefixed KSUID (``usr_...`` or ``agnt_...``).

    Raises ``ValueError`` on any other prefix, including the retired
    ``sva_``; callers labelling historical rows use
    :func:`actor_type_label_from_id` instead.
    """
    for prefix, actor_type in _PREFIX_TO_ACTOR_TYPE.items():
        if actor_id.startswith(prefix):
            return actor_type
    raise ValueError(f"Cannot derive ActorType from id={actor_id!r}: unrecognised prefix")


def actor_type_label_from_id(actor_id: str) -> str:
    """Actor-type string for a persisted actor id, tolerant of retired ids.

    Theme-8 Phase 4 (L4): control-DB columns such as
    ``connect_sessions.initiator_actor_id`` and ``credentials.created_by``
    keep ``sva_`` ids from before the service-account migration. Those map
    to the retired ``"service_account"`` label (never an identity) instead of
    raising; any other unrecognised prefix still raises ``ValueError``.
    """
    if actor_id.startswith(RETIRED_SERVICE_ACCOUNT_ID_PREFIX):
        return RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE
    return actor_type_from_id(actor_id).value


class ActorStatus(StrEnum):
    """Lifecycle status of an agent."""

    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class ActorVerb(StrEnum):
    """Lifecycle transition verbs for agents."""

    APPROVE = "approve"
    DENY = "deny"
    DISABLE = "disable"
    ENABLE = "enable"
