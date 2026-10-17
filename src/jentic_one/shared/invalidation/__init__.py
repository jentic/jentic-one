"""Cross-process cache invalidation: the ``InvalidationBus`` seam and its defaults.

See :mod:`jentic_one.shared.invalidation.protocol` for the contract,
``docs/architecture/composition-and-processes.md`` for how it is wired.
"""

from jentic_one.shared.invalidation.factory import INVALIDATION_DATABASES, build_invalidation_bus
from jentic_one.shared.invalidation.memory import (
    InProcessInvalidationBus,
    NoopInvalidationBus,
    SubscriberRegistry,
)
from jentic_one.shared.invalidation.postgres import (
    LISTEN_APPLICATION_NAME,
    PgListenInvalidationBus,
    PgListenTarget,
    listen_targets,
)
from jentic_one.shared.invalidation.protocol import (
    PAYLOAD_VERSION,
    PG_INVALIDATION_CHANNEL,
    InvalidationBus,
    InvalidationHandler,
    InvalidationMessage,
    TriggerPayload,
    build_trigger_payload,
    parse_trigger_payload,
    topic_for,
)

__all__ = [
    "INVALIDATION_DATABASES",
    "LISTEN_APPLICATION_NAME",
    "PAYLOAD_VERSION",
    "PG_INVALIDATION_CHANNEL",
    "InProcessInvalidationBus",
    "InvalidationBus",
    "InvalidationHandler",
    "InvalidationMessage",
    "NoopInvalidationBus",
    "PgListenInvalidationBus",
    "PgListenTarget",
    "SubscriberRegistry",
    "TriggerPayload",
    "build_invalidation_bus",
    "build_trigger_payload",
    "listen_targets",
    "parse_trigger_payload",
    "topic_for",
]
