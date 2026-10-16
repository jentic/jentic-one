"""Unit tests for the shared egress transport builders.

The DNS-pinning transport itself is exercised in ``tests/unit/broker/test_dns_pin.py``
(via the broker re-export). These pin the shared ``build_pinned_transport`` contract
that the registry fetchers rely on: enabled → a pinning transport, disabled/None → the
default (``None``) so httpx behaviour is unchanged. ``build_always_pinned_transport``
(the control plane's builder) always pins and validates every connection against
the policy it is given, whatever ``dns_pinning_enabled`` says.
"""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from jentic_one.broker.adapters import egress as broker_egress
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.egress import (
    DnsPinningTransport,
    build_always_pinned_transport,
    build_pinned_transport,
)


def test_build_pinned_transport_enabled_returns_pinning_transport() -> None:
    transport = build_pinned_transport(EgressConfig(dns_pinning_enabled=True))
    assert isinstance(transport, DnsPinningTransport)


def test_build_pinned_transport_disabled_returns_none() -> None:
    assert build_pinned_transport(EgressConfig(dns_pinning_enabled=False)) is None


def test_build_pinned_transport_none_egress_returns_none() -> None:
    # No egress policy → no pin (httpx default transport), unchanged behaviour.
    assert build_pinned_transport(None) is None


_ALLOWLIST_PINNING_OFF = EgressConfig(
    allowed_private_subnets=["10.50.0.0/16"],
    allowed_internal_domains=[".corp.internal"],
    dns_pinning_enabled=False,
)


def _addrinfo(ip: str) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]


async def _send_through_always_pinned(
    egress: EgressConfig | None, url: str, resolved_ip: str
) -> list[httpx.Request]:
    """Send one GET through ``build_always_pinned_transport``; return what reached the socket."""
    sent: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    with (
        patch.object(socket, "getaddrinfo", return_value=_addrinfo(resolved_ip)),
        patch.object(
            httpx, "AsyncHTTPTransport", side_effect=lambda *a, **k: httpx.MockTransport(_record)
        ),
    ):
        transport = build_always_pinned_transport(egress)
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get(url)
    return sent


async def test_build_always_pinned_transport_pins_allowlisted_host_with_pinning_flag_off() -> None:
    # ``dns_pinning_enabled=False`` is ignored: the request still goes to the
    # validated IP, with the name kept for Host / SNI.
    sent = await _send_through_always_pinned(
        _ALLOWLIST_PINNING_OFF, "https://idp.corp.internal/token", "10.50.0.7"
    )
    assert [(r.url.host, r.headers["Host"], r.extensions["sni_hostname"]) for r in sent] == [
        ("10.50.0.7", "idp.corp.internal", "idp.corp.internal")
    ]


@pytest.mark.parametrize(
    ("egress", "url", "resolved_ip"),
    [
        (None, "https://idp.corp.internal/token", "10.50.0.7"),
        (_ALLOWLIST_PINNING_OFF, "https://idp.corp.internal/token", "10.60.0.9"),
        (_ALLOWLIST_PINNING_OFF, "https://idp.other.internal/token", "10.50.0.7"),
        (_ALLOWLIST_PINNING_OFF, "https://127.0.0.1/token", "127.0.0.1"),
    ],
    ids=["strict-default", "subnet-not-allowlisted", "domain-not-allowlisted", "loopback"],
)
async def test_build_always_pinned_transport_refuses_outside_policy(
    egress: EgressConfig | None, url: str, resolved_ip: str
) -> None:
    with pytest.raises(ValueError):
        await _send_through_always_pinned(egress, url, resolved_ip)


def test_broker_reexport_is_the_same_object() -> None:
    """The broker adapter must re-export the shared symbols (single implementation)."""
    assert broker_egress.DnsPinningTransport is DnsPinningTransport
    assert broker_egress.build_pinned_transport is build_pinned_transport
