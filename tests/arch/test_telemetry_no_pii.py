"""Arch guard: the telemetry wire payload can carry no PII by construction.

The whole privacy posture rests on the request being *exactly*
``{id, version, event, actor_type?, tags?, ts}`` — there is structurally nowhere
for an identity, URL, error string, or entity id to ride along. These tests
assert that invariant at the type/serialisation level so a future change that
adds a free-form field fails loudly here.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime

import pytest

from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.events import EventTag
from jentic_one.shared.telemetry.client import _serialise
from jentic_one.shared.telemetry.events import TelemetryEvent, TelemetryEventName
from jentic_one.shared.telemetry.sink import TelemetrySink

_ALLOWED_KEYS = {"id", "version", "event", "actor_type", "tags", "ts"}


@pytest.mark.arch
def test_serialised_payload_has_only_the_closed_key_set() -> None:
    """Tagged and untagged payloads contain only keys from the closed set."""
    untagged = _serialise(
        TelemetryEvent(name=TelemetryEventName.BROKER_EXECUTION, tags=(), ts=datetime.now(UTC)),
        instance_id="i",
        version="1.0.0",
    )
    assert set(untagged) == {"id", "version", "event", "ts"}
    assert set(untagged) <= _ALLOWED_KEYS

    # A tag from each closed enum may appear, but never any other key.
    for tag in EventTag.__args__:
        for value in tag:
            tagged = _serialise(
                TelemetryEvent(
                    name=TelemetryEventName.CREDENTIAL_REFRESH_FAILED,
                    tags=(value,),
                    ts=datetime.now(UTC),
                ),
                instance_id="i",
                version="1.0.0",
            )
            assert set(tagged) <= _ALLOWED_KEYS
            assert tagged["tags"] == [str(value)]


@pytest.mark.arch
def test_telemetry_event_has_no_free_form_fields() -> None:
    """TelemetryEvent carries only name/tags/actor_type/ts — no props/data/payload escape hatch."""
    field_names = {f.name for f in dataclasses.fields(TelemetryEvent)}
    assert field_names == {"name", "tags", "ts", "actor_type"}


@pytest.mark.arch
def test_actor_type_must_be_a_closed_enum_member() -> None:
    """A non-enum ``actor_type`` is dropped by the sink, never reaching the wire.

    The privacy claim is that ``actor_type`` can only ever be an ``ActorType``
    kind — so a future caller passing a raw label/email cannot leak it. Enforce
    that the guarantee is structural (the sink coerces + drops), not by
    convention.
    """
    sink = TelemetrySink(enabled=True, queue_max=8)

    # A raw non-enum string (e.g. an email) is dropped to None on the queued event.
    sink.record(TelemetryEventName.BROKER_EXECUTION, (), "operator@example.com")
    leaked = sink.queue.get_nowait()
    assert leaked.actor_type is None
    assert "actor_type" not in _serialise(leaked, instance_id="i", version="1.0.0")

    # A valid enum member is preserved and serialised as its (dash-normalised) value.
    sink.record(TelemetryEventName.BROKER_EXECUTION, (), ActorType.SERVICE_ACCOUNT.value)
    kept = sink.queue.get_nowait()
    assert kept.actor_type is ActorType.SERVICE_ACCOUNT
    assert _serialise(kept, instance_id="i", version="1.0.0")["actor_type"] == "service-account"


@pytest.mark.arch
def test_sink_record_exposes_no_free_form_props() -> None:
    """TelemetrySink.record accepts only (name, tags, actor_type) — no free-form kwargs."""
    params = inspect.signature(TelemetrySink.record).parameters
    assert set(params) == {"self", "name", "tags", "actor_type"}
    # No **kwargs catch-all that could smuggle props in.
    assert all(p.kind is not inspect.Parameter.VAR_KEYWORD for p in params.values())
