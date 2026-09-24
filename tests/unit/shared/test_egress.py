"""Unit tests for the shared egress helper ``build_pinned_transport``.

The DNS-pinning transport itself is exercised in ``tests/unit/broker/test_dns_pin.py``
(via the broker re-export). These pin the shared ``build_pinned_transport`` contract
that the registry fetchers rely on: enabled → a pinning transport, disabled/None → the
default (``None``) so httpx behaviour is unchanged.
"""

from __future__ import annotations

from jentic_one.broker.adapters import egress as broker_egress
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.egress import DnsPinningTransport, build_pinned_transport


def test_build_pinned_transport_enabled_returns_pinning_transport() -> None:
    transport = build_pinned_transport(EgressConfig(dns_pinning_enabled=True))
    assert isinstance(transport, DnsPinningTransport)


def test_build_pinned_transport_disabled_returns_none() -> None:
    assert build_pinned_transport(EgressConfig(dns_pinning_enabled=False)) is None


def test_build_pinned_transport_none_egress_returns_none() -> None:
    # No egress policy → no pin (httpx default transport), unchanged behaviour.
    assert build_pinned_transport(None) is None


def test_broker_reexport_is_the_same_object() -> None:
    """The broker adapter must re-export the shared symbols (single implementation)."""
    assert broker_egress.DnsPinningTransport is DnsPinningTransport
    assert broker_egress.build_pinned_transport is build_pinned_transport
