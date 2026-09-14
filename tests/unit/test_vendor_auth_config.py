"""Pydantic validator pins for ``VendorAuthConfig``.

The ``vendor`` field is decomposed at credential-create time via
``entry.vendor.split("/", 1)[0]`` — a bare string with no ``/`` silently
produces a slugged credential that mismatches the broker's per-operation
identity check. The validator refuses the bad shape at config-load so the
mismatch surfaces at boot, not at first connect.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorIdentityProbeConfig,
)

_FLOW = VendorDeviceAuthorizationFlowConfig(
    client_id="cid",
    authorization_endpoint="https://github.com/login/device/code",
    token_endpoint="https://github.com/login/oauth/access_token",
)
_PROBE = VendorIdentityProbeConfig(
    endpoint="https://api.github.com/user",
    identity_field="login",
    display_template="@{login}",
)


def test_vendor_accepts_domain_slash_name() -> None:
    entry = VendorAuthConfig(
        vendor="github.com/api.github.com",
        display_name="GitHub",
        flows=[_FLOW],
        identity_probe=_PROBE,
    )
    assert entry.vendor == "github.com/api.github.com"


@pytest.mark.parametrize(
    "bad_vendor",
    [
        "github.com",  # no slash at all — the pre-fix silent-bug shape
        "/api.github.com",  # leading slash
        "github.com/",  # trailing slash
        "",  # empty
    ],
)
def test_vendor_rejects_malformed(bad_vendor: str) -> None:
    with pytest.raises(ValidationError):
        VendorAuthConfig(
            vendor=bad_vendor,
            display_name="GitHub",
            flows=[_FLOW],
            identity_probe=_PROBE,
        )
