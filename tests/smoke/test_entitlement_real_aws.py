"""Real-AWS entitlement check — exercises the license client against live AWS.

The unit suite covers the whole verdict/grace/lockout matrix with
``httpx.MockTransport``; these tests close the remaining gap by asking the
**real** AWS Marketplace APIs. They are opt-in and skip cleanly unless
explicitly enabled (the gating env var is deliberately separate from the
standard ``AWS_*`` variables — CI runners often carry ambient credentials and
these must never fire implicitly):

    ENTITLEMENT_REAL_AWS=1 \
    ENTITLEMENT_PRODUCT_CODE=<product code from the Marketplace portal> \
    ENTITLEMENT_LICENSE_SKU=<product ID from the portal> \
    ENTITLEMENT_LICENSE_DIMENSIONS=users,executions \
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... [AWS_SESSION_TOKEN=...] \
    [AWS_REGION=us-east-1] [ENTITLEMENT_PRICING_MODEL=contract|usage] \
    uv run pytest tests/smoke/test_entitlement_real_aws.py -m smoke --no-cov -rs

Two layers:

1. **Entitled** — a caller in an account subscribed to the test listing (AWS
   Seller Ops can allowlist accounts for a limited-visibility listing) gets
   ``ENTITLED`` for the real product code.
2. **Negative control** — a wrong product code must yield ``NOT_ENTITLED``
   (never ``UNKNOWN``), proving layer 1 exercised real entitlement semantics
   rather than a permissive endpoint.
"""

from __future__ import annotations

import os

import httpx
import pytest

from jentic_one.integrations.aws_marketplace.client import LicenseVerdict, build_license_client
from jentic_one.shared.config import EntitlementConfig

pytestmark = pytest.mark.smoke


def _config_from_env() -> EntitlementConfig:
    if os.environ.get("ENTITLEMENT_REAL_AWS") != "1":
        pytest.skip("real-AWS entitlement tests disabled (set ENTITLEMENT_REAL_AWS=1)")
    product_code = os.environ.get("ENTITLEMENT_PRODUCT_CODE")
    if not product_code:
        pytest.skip("ENTITLEMENT_PRODUCT_CODE not set")
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        pytest.skip("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY not set")
    pricing_model = os.environ.get("ENTITLEMENT_PRICING_MODEL", "contract")
    license_sku = os.environ.get("ENTITLEMENT_LICENSE_SKU")
    if pricing_model == "contract" and not license_sku:
        pytest.skip("contract pricing needs ENTITLEMENT_LICENSE_SKU (the product ID)")
    return EntitlementConfig(
        enabled=True,
        product_code=product_code,
        region=os.environ.get("AWS_REGION", "us-east-1"),
        pricing_model=pricing_model,  # type: ignore[arg-type]
        # Contract pricing: the Marketplace product ID (not the product code).
        license_sku=license_sku,
        # Comma-separated, e.g. "users,executions" (the live listing's dimensions).
        license_dimensions=os.environ.get("ENTITLEMENT_LICENSE_DIMENSIONS", ""),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_real_subscription_is_entitled() -> None:
    config = _config_from_env()
    async with httpx.AsyncClient() as http:
        verdict = await build_license_client(config, http).check()

    assert verdict is LicenseVerdict.ENTITLED


@pytest.mark.asyncio
async def test_wrong_product_is_not_entitled() -> None:
    """Negative control: proves the positive test exercised real semantics.

    Mutates the identifier the active client variant actually sends: the
    metering client sends ``product_code``; the license-manager client sends
    ``license_sku`` (the product ID) as ``ProductSKU``.
    """
    config = _config_from_env()
    if config.pricing_model == "contract":
        config = config.model_copy(update={"license_sku": "prod-wrongid00000000000000"})
    else:
        config = config.model_copy(update={"product_code": "prodwrongcode000000000000"})
    async with httpx.AsyncClient() as http:
        verdict = await build_license_client(config, http).check()

    assert verdict is LicenseVerdict.NOT_ENTITLED
