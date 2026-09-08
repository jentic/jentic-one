"""License clients for the AWS Marketplace entitlement check (no boto3).

One protocol, two implementations — the checker doesn't know which:

- :class:`MeteringLicenseClient` (usage/hourly pricing) calls the AWS
  Marketplace **Metering Service** ``RegisterUsage``. For container products
  one successful call both verifies the entitlement and starts metering.
- :class:`LicenseManagerClient` (contract pricing) calls AWS **License
  Manager** ``CheckoutLicense`` against the license AWS Marketplace creates in
  the buyer's account, then ``CheckInLicense`` to release the seat the probe
  consumed (counted dimensions have a finite ``MaxCount``).

Both are a single SigV4-signed ``x-amz-json-1.1`` POST built on the project's
existing signer (``shared/aws/sigv4.py``) and ``httpx`` — no new dependency.
Every response maps to a three-way :class:`LicenseVerdict`; the *only* verdict
that locks a deployment out immediately is an explicit not-entitled error from
AWS. Anything ambiguous (throttle, 5xx, network, missing credentials) is
``UNKNOWN`` and subject to the checker's grace window.
"""

from __future__ import annotations

import asyncio
import enum
import json
import uuid
from typing import Protocol

import httpx
import structlog

from jentic_one.integrations.aws_marketplace.credentials import (
    CredentialProvider,
    CredentialResolutionError,
)
from jentic_one.shared.aws.sigv4 import sign_request
from jentic_one.shared.config import EntitlementConfig

_log = structlog.get_logger(__name__)

_REQUEST_TIMEOUT_S = 10.0

# Contract pricing only: how long to wait before the single retry that
# disambiguates transient seat contention from a genuinely missing license
# (both surface as NoEntitlementsAllowedException; see LicenseManagerClient).
_SEAT_CONTENTION_RETRY_DELAY_S = 1.5

# The public key AWS Marketplace vends for RegisterUsage response signatures.
# We do not verify the (optional) response JWT — transport security is TLS and
# the verdict mapping only trusts the HTTP status/error type — but the field is
# required in the request body.
_METERING_PUBLIC_KEY_VERSION = 1

# The fixed issuer fingerprint of licenses created by AWS Marketplace (from the
# AWS "container product License Manager integration" documentation). Verify
# against the current doc page when onboarding a listing — AWS revises it.
_MARKETPLACE_ISSUER_FINGERPRINT = "aws:294406891311:AWS/Marketplace:issuer-fingerprint"

# Error ``__type`` values that mean "AWS answered, and the answer is no".
_METERING_NOT_ENTITLED_ERRORS = frozenset(
    {"CustomerNotEntitledException", "PlatformNotSupportedException"}
)
_LICENSE_MANAGER_NOT_ENTITLED_ERRORS = frozenset(
    {
        "NoEntitlementsAllowedException",
        "EntitlementNotAllowedException",
        "ResourceNotFoundException",
    }
)


class LicenseVerdict(enum.Enum):
    """Three-way answer from one license check."""

    ENTITLED = "entitled"
    NOT_ENTITLED = "not_entitled"
    UNKNOWN = "unknown"


class LicenseClient(Protocol):
    """One entitlement check against AWS. Never raises — maps to a verdict."""

    async def check(self) -> LicenseVerdict: ...


def build_license_client(config: EntitlementConfig, http: httpx.AsyncClient) -> LicenseClient:
    """Select the client for the configured pricing model."""
    provider = CredentialProvider(
        http,
        region=config.region,
        service=("license-manager" if config.pricing_model == "contract" else "aws-marketplace"),
    )
    if config.pricing_model == "contract":
        return LicenseManagerClient(config, http, provider)
    return MeteringLicenseClient(config, http, provider)


class _BaseLicenseClient:
    """Shared signed-POST mechanics; subclasses supply body/target/mapping."""

    #: SigV4 service name and the API's ``X-Amz-Target`` header value.
    service: str
    target: str
    not_entitled_errors: frozenset[str]

    def __init__(
        self,
        config: EntitlementConfig,
        http: httpx.AsyncClient,
        provider: CredentialProvider,
    ) -> None:
        self._config = config
        self._http = http
        self._provider = provider

    def _default_url(self) -> str:
        raise NotImplementedError

    def _body(self) -> dict[str, object]:
        raise NotImplementedError

    async def check(self) -> LicenseVerdict:
        url = self._config.endpoint or self._default_url()
        response = await self._signed_post(url, self.target, self._body())
        if response is None:
            return LicenseVerdict.UNKNOWN
        if response.status_code == 200:
            await self._on_entitled(response)
            return LicenseVerdict.ENTITLED
        error_type = _aws_error_type(response)
        if error_type in self.not_entitled_errors:
            _log.info(
                "entitlement.not_entitled",
                target=self.target,
                error_type=error_type,
            )
            return LicenseVerdict.NOT_ENTITLED
        _log.warning(
            "entitlement.check_inconclusive",
            target=self.target,
            status_code=response.status_code,
            error_type=error_type,
        )
        return LicenseVerdict.UNKNOWN

    async def _signed_post(
        self, url: str, target: str, body_dict: dict[str, object]
    ) -> httpx.Response | None:
        """One SigV4-signed x-amz-json-1.1 POST; ``None`` for transport failures."""
        body = json.dumps(body_dict).encode()
        try:
            material = await self._provider.resolve()
        except CredentialResolutionError:
            _log.warning("entitlement.credentials_unresolved", exc_info=True)
            return None
        headers = {
            "content-type": "application/x-amz-json-1.1",
            "x-amz-target": target,
        }
        headers.update(sign_request(method="POST", url=url, body=body, material=material))
        try:
            return await self._http.post(
                url, content=body, headers=headers, timeout=_REQUEST_TIMEOUT_S
            )
        except httpx.HTTPError:
            _log.warning("entitlement.check_request_failed", target=target, exc_info=True)
            return None

    async def _on_entitled(self, response: httpx.Response) -> None:
        """Hook for subclasses that must react to a successful check."""


def _aws_error_type(response: httpx.Response) -> str | None:
    """Extract the bare error name from an AWS JSON error response.

    AWS reports errors as ``{"__type": "…#CustomerNotEntitledException", …}``
    (the prefix before ``#`` varies) or via the ``x-amzn-errortype`` header.
    """
    raw: str | None
    try:
        payload = response.json()
        raw = payload.get("__type") if isinstance(payload, dict) else None
    except ValueError:
        raw = None
    if not raw:
        raw = response.headers.get("x-amzn-errortype")
    if not raw:
        return None
    return raw.split("#")[-1].split(":")[0]


class MeteringLicenseClient(_BaseLicenseClient):
    """Usage pricing: Metering Service ``RegisterUsage``.

    Per the AWS docs, for container products the metering control plane bills
    per task from the launch-time call onward — the periodic re-check is our
    own hardening (an eventually-locked-out cancelled subscription), not an
    AWS billing requirement.
    """

    service = "aws-marketplace"
    target = "AWSMPMeteringService.RegisterUsage"
    not_entitled_errors = _METERING_NOT_ENTITLED_ERRORS

    def _default_url(self) -> str:
        return f"https://metering.marketplace.{self._config.region}.amazonaws.com/"

    def _body(self) -> dict[str, object]:
        return {
            "ProductCode": self._config.product_code,
            "PublicKeyVersion": _METERING_PUBLIC_KEY_VERSION,
            "Nonce": uuid.uuid4().hex,
        }


class LicenseManagerClient(_BaseLicenseClient):
    """Contract pricing: License Manager ``CheckoutLicense`` + ``CheckInLicense``.

    ``ProductSKU`` is the Marketplace **product ID** from the portal
    (``config.license_sku``), not the product code. The checkout lists every
    configured dimension (``config.license_dimensions`` — the live listing
    defines ``users`` and ``executions``); License Manager grants the checkout
    only if the buyer's license covers all of them, so one call gates on both.

    Two behaviours verified against the live listing (2026-08-28):

    - A successful PROVISIONAL checkout **consumes a seat** from the
      dimension's ``MaxCount`` for up to 60 minutes. A 1-seat contract plus
      two pods re-checking hourly would lock the buyer out of their own
      license, so every successful checkout is immediately followed by
      ``CheckInLicense`` — the check is a pure entitlement probe and holds a
      seat only for the round-trip.
    - Seat contention (another holder checked out concurrently) and a
      genuinely absent license return the **same**
      ``NoEntitlementsAllowedException``, and a false NOT_ENTITLED is cached
      for a full refresh interval. One short-delay retry disambiguates a
      transient collision (e.g. app + broker booting together) from a real
      denial.
    """

    service = "license-manager"
    target = "AWSLicenseManager.CheckoutLicense"
    not_entitled_errors = _LICENSE_MANAGER_NOT_ENTITLED_ERRORS

    def _default_url(self) -> str:
        return f"https://license-manager.{self._config.region}.amazonaws.com/"

    def _body(self) -> dict[str, object]:
        # Counted dimensions require Unit "Count" + a Value: the live listing's
        # license carries `Unit: "Count", MaxCount: N` for users/executions, and
        # a `Unit: "None"` checkout is rejected with
        # NoEntitlementsAllowedException (verified against the live listing,
        # 2026-08-28). Value "1" probes for one available seat, which the
        # check-in below releases immediately.
        entitlements: list[dict[str, str]] = [
            {"Name": dimension, "Unit": "Count", "Value": "1"}
            for dimension in self._config.license_dimensions
        ]
        return {
            "CheckoutType": "PROVISIONAL",
            "ProductSKU": self._config.license_sku,
            "KeyFingerprint": _MARKETPLACE_ISSUER_FINGERPRINT,
            "Entitlements": entitlements,
            "ClientToken": uuid.uuid4().hex,
        }

    async def check(self) -> LicenseVerdict:
        verdict = await super().check()
        if verdict is LicenseVerdict.NOT_ENTITLED:
            # Could be seat contention rather than a missing license (the
            # error type is identical). Retry once after a short delay; a
            # genuinely non-entitled deployment just answers the same twice.
            await asyncio.sleep(_SEAT_CONTENTION_RETRY_DELAY_S)
            verdict = await super().check()
        return verdict

    async def _on_entitled(self, response: httpx.Response) -> None:
        """Release the seat the successful checkout just consumed."""
        try:
            payload = response.json()
        except ValueError:
            payload = None
        token = payload.get("LicenseConsumptionToken") if isinstance(payload, dict) else None
        if not token:
            # Seat auto-expires after MaxTimeToLiveInMinutes (60) — degraded
            # but not fatal; flag it because 1-seat contracts will contend.
            _log.warning("entitlement.checkin_token_missing")
            return
        url = self._config.endpoint or self._default_url()
        checkin = await self._signed_post(
            url,
            "AWSLicenseManager.CheckInLicense",
            {"LicenseConsumptionToken": token},
        )
        if checkin is None or checkin.status_code != 200:
            _log.warning(
                "entitlement.checkin_failed",
                status_code=None if checkin is None else checkin.status_code,
                error_type=None if checkin is None else _aws_error_type(checkin),
            )
