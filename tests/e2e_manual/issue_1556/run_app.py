"""Start ``python -m jentic_one`` with the control plane's OAuth egress opened to loopback.

The control plane's OAuth token, device-authorization and identity-probe calls
have no operator-configurable egress policy: they always refuse private and
loopback targets. The e2e loop's fake authorization server runs on
``127.0.0.1``, so this launcher widens exactly those two strict defaults
(``validate_upstream_url`` with no policy and ``build_strict_pinned_transport``)
to admit ``127.0.0.0/8`` before the app imports them. Everything else — the
broker and ingest egress policies — comes from the config file as usual.

Test-only. Never use it to run a real deployment.
"""

from __future__ import annotations

import sys

import httpx

from jentic_one.shared import egress as _egress
from jentic_one.shared import url_validation as _url_validation
from jentic_one.shared.config import EgressConfig

_LOOPBACK = EgressConfig(allowed_private_subnets=["127.0.0.0/8"])
_original_validate = _url_validation.validate_upstream_url


def _validate_upstream_url(raw_url: str, egress: EgressConfig | None = None) -> str:
    return _original_validate(raw_url, egress if egress is not None else _LOOPBACK)


def _build_strict_pinned_transport() -> httpx.AsyncBaseTransport:
    return _egress.DnsPinningTransport(httpx.AsyncHTTPTransport(), _LOOPBACK)


_url_validation.validate_upstream_url = _validate_upstream_url
_egress.build_strict_pinned_transport = _build_strict_pinned_transport

from jentic_one.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
