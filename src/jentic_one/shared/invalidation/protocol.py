"""The ``InvalidationBus`` seam: cross-process cache invalidation messages.

A process that caches authorization state (the broker's token, API-key, rule
and binding caches) subscribes to the source tables that state is read from.
When another process commits a change to one of those tables, the bus delivers
an :class:`InvalidationMessage` and the subscriber drops the affected entries,
so a revoke takes effect before the cache TTL lapses.

Publishing is not a Python concern: on PostgreSQL, ``AFTER`` row triggers on
the source tables call ``pg_notify`` (see the admin and control migrations),
so a notification is sent only when the writing transaction commits. The TTLs
stay the safety net — a bus that misses messages (SQLite, a dropped LISTEN
connection) degrades to TTL-bounded staleness, never to an unbounded one.

A topic names one source table as ``"<database>.<table>"`` (for example
``"admin.access_tokens"``), where ``<database>`` is the logical database
(``admin``/``control``) the table lives in.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: The PostgreSQL ``NOTIFY`` channel every invalidation trigger publishes on.
#: The trigger function in the admin and control migrations hard-codes the same
#: literal; ``tests/unit/shared/invalidation`` pins the two together.
PG_INVALIDATION_CHANNEL = "jentic_one_invalidation"

#: Payload schema version the trigger function emits (``"v"`` field).
PAYLOAD_VERSION = 1


def topic_for(database: str, table: str) -> str:
    """The topic for ``table`` in the logical ``database`` (``"admin.agents"``)."""
    return f"{database}.{table}"


@dataclass(frozen=True, slots=True)
class InvalidationMessage:
    """One source-table change (or a coarse flush) delivered to subscribers.

    ``keys`` maps each key column the trigger reports to the distinct non-null
    values the changed row held (an ``UPDATE`` that rewrites a key column
    reports both the old and the new value). ``flush`` set means "the affected
    keys are unknown — drop everything cached from this topic": the bus sends it
    after a reconnect (notifications may have been missed while disconnected)
    and the trigger sends it instead of a payload that would exceed the
    ``NOTIFY`` size limit.
    """

    topic: str
    operation: str = ""
    keys: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    flush: bool = False

    def values(self, column: str) -> tuple[str, ...]:
        """The reported values for ``column`` (empty when it was not reported)."""
        return self.keys.get(column, ())


#: A subscriber callback. Called on the event loop, synchronously: handlers
#: only drop in-memory cache entries, so they must not block or await.
InvalidationHandler = Callable[[InvalidationMessage], None]


@runtime_checkable
class InvalidationBus(Protocol):
    """Delivers cross-process invalidation messages to in-process subscribers.

    Lifecycle: subscribers register with :meth:`subscribe` while the app is
    being wired (before :meth:`start`); the app lifespan calls :meth:`start`
    after ``ctx.startup()`` and :meth:`stop` before ``ctx.shutdown()``. Both are
    idempotent. A bus with no subscriptions holds no connection.

    Delivery contract an implementation must keep (pinned by
    :class:`jentic_one.testing.BaseInvalidationBusContract`):

    - a message reaches every handler subscribed to its topic, and no other;
    - a raising handler does not stop delivery to the others;
    - after a lost connection is re-established, every handler receives one
      ``flush`` message per subscribed topic, because messages sent while it was
      disconnected are gone;
    - nothing is delivered after :meth:`stop`.
    """

    def subscribe(self, topic: str, handler: InvalidationHandler) -> None:
        """Register ``handler`` for messages on ``topic``."""
        ...

    async def start(self) -> None:
        """Begin delivering messages (opens any connection the bus needs)."""
        ...

    async def stop(self) -> None:
        """Stop delivering messages and release every connection."""
        ...


def build_trigger_payload(
    *,
    schema: str,
    table: str,
    operation: str,
    keys: Mapping[str, Sequence[str]] | None = None,
    flush: bool = False,
) -> str:
    """Render a payload in the trigger function's wire format.

    The production publisher is the SQL trigger; this mirrors its output so
    tests and tooling can ``pg_notify`` an equivalent message.
    """
    body: dict[str, object] = {"v": PAYLOAD_VERSION, "s": schema, "t": table, "op": operation}
    if flush:
        body["flush"] = True
    else:
        body["k"] = {col: list(vals) for col, vals in (keys or {}).items()}
    return json.dumps(body, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class TriggerPayload:
    """A decoded trigger payload, before its schema is mapped to a database."""

    schema: str
    table: str
    operation: str
    keys: Mapping[str, tuple[str, ...]]
    flush: bool


def parse_trigger_payload(raw: str) -> TriggerPayload | None:
    """Decode a trigger payload; ``None`` for anything malformed or unknown."""
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict) or body.get("v") != PAYLOAD_VERSION:
        return None
    schema, table = body.get("s"), body.get("t")
    if not isinstance(schema, str) or not isinstance(table, str):
        return None
    keys: dict[str, tuple[str, ...]] = {}
    raw_keys = body.get("k") or {}
    if isinstance(raw_keys, dict):
        for col, vals in raw_keys.items():
            if isinstance(vals, list):
                keys[str(col)] = tuple(str(v) for v in vals if v is not None)
    return TriggerPayload(
        schema=schema,
        table=table,
        operation=str(body.get("op") or ""),
        keys=keys,
        flush=bool(body.get("flush")),
    )
