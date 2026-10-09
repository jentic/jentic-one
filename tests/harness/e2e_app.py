"""Run the combined app for a local real-backend e2e against loopback OAuth servers.

The control plane's OAuth calls (token exchange, device authorization,
identity probe, an approver's own OAuth client) are checked with
``validate_upstream_url`` and sent through ``build_strict_pinned_transport``,
which both refuse every loopback target and have no config knob — on
purpose, in production. A local e2e whose fake vendor
(``tests.harness.fake_oauth``) listens on 127.0.0.1 needs those calls to
reach it, so this launcher swaps both, at their control-plane call sites,
for versions that also admit 127.0.0.0/8, then starts the app exactly like
``python -m jentic_one``.

Test-only: never use it to serve anything real.

    JENTIC_CONFIG_FILE=... uv run python -m tests.harness.e2e_app
"""

from __future__ import annotations

import sys

import httpx

from jentic_one import __main__ as app_main
from jentic_one.control.services.credentials.providers import oauth2
from jentic_one.control.services.integrations import (
    connect_session_service,
    device_authorization,
    identity_echo,
)
from jentic_one.control.services.integrations.flow_handlers import auth_code
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.egress import DnsPinningTransport
from jentic_one.shared.url_validation import validate_upstream_url

_LOOPBACK = EgressConfig(allowed_private_subnets=["127.0.0.0/8"])


def _loopback_pinned_transport() -> httpx.AsyncBaseTransport:
    return DnsPinningTransport(httpx.AsyncHTTPTransport(), _LOOPBACK)


def _loopback_validate_upstream_url(raw_url: str, egress: EgressConfig | None = None) -> str:
    return validate_upstream_url(raw_url, egress or _LOOPBACK)


for _module in (oauth2, device_authorization, identity_echo, auth_code):
    _module.build_strict_pinned_transport = _loopback_pinned_transport  # type: ignore[attr-defined]

for _module in (oauth2, connect_session_service, device_authorization, identity_echo, auth_code):
    _module.validate_upstream_url = _loopback_validate_upstream_url  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(app_main.main())
