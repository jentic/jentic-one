"""Actor-related enums shared across modules."""

from enum import StrEnum


class ActorType(StrEnum):
    """Type of authenticated actor."""

    USER = "user"
    AGENT = "agent"
    SERVICE_ACCOUNT = "service_account"
    TOOLKIT = "toolkit"


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
    "sva_": ActorType.SERVICE_ACCOUNT,
}


def actor_type_from_id(actor_id: str) -> ActorType:
    """Derive ActorType from a prefixed KSUID (e.g. ``usr_...``, ``agnt_...``, ``sva_...``)."""
    for prefix, actor_type in _PREFIX_TO_ACTOR_TYPE.items():
        if actor_id.startswith(prefix):
            return actor_type
    raise ValueError(f"Cannot derive ActorType from id={actor_id!r}: unrecognised prefix")


class ActorStatus(StrEnum):
    """Lifecycle status shared by agents and service accounts."""

    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class ActorVerb(StrEnum):
    """Lifecycle transition verbs for agents and service accounts."""

    APPROVE = "approve"
    DENY = "deny"
    DISABLE = "disable"
    ENABLE = "enable"
