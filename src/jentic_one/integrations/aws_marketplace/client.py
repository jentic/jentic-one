"""License clients for the AWS Marketplace entitlement check (no boto3).

One protocol, two implementations — the checker doesn't know which:

- :class:`MeteringLicenseClient` (usage/hourly pricing) calls the AWS
  Marketplace **Metering Service** ``RegisterUsage``. For container products
  one successful call both verifies the entitlement and starts metering.
- :class:`LicenseManagerClient` (contract pricing) calls AWS **License
  Manager** ``CheckoutLicense`` against the license AWS Marketplace creates in
  the buyer's account.

Both are a single SigV4-signed ``x-amz-json-1.1`` POST built on the project's
existing signer (``shared/aws/sigv4.py``) and ``httpx`` — no new dependency.
Every response maps to a three-way :class:`LicenseVerdict`; the *only* verdict
that locks a deployment out immediately is an explicit not-entitled error from
AWS. Anything ambiguous (throttle, 5xx, network, missing credentials) is
``UNKNOWN`` and subject to the checker's grace window.
"""

from __future__ import annotations

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
        body = json.dumps(self._body()).encode()
        try:
            material = await self._provider.resolve()
        except CredentialResolutionError:
            _log.warning("entitlement.credentials_unresolved", exc_info=True)
            return LicenseVerdict.UNKNOWN
        headers = {
            "content-type": "application/x-amz-json-1.1",
            "x-amz-target": self.target,
        }
        headers.update(sign_request(method="POST", url=url, body=body, material=material))
        try:
            response = await self._http.post(
                url, content=body, headers=headers, timeout=_REQUEST_TIMEOUT_S
            )
        except httpx.HTTPError:
            _log.warning("entitlement.check_request_failed", target=self.target, exc_info=True)
            return LicenseVerdict.UNKNOWN
        if response.status_code == 200:
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
    """Contract pricing: License Manager ``CheckoutLicense`` (provisional)."""

    service = "license-manager"
    target = "AWSLicenseManager.CheckoutLicense"
    not_entitled_errors = _LICENSE_MANAGER_NOT_ENTITLED_ERRORS

    def _default_url(self) -> str:
        return f"https://license-manager.{self._config.region}.amazonaws.com/"

    def _body(self) -> dict[str, object]:
        entitlements: list[dict[str, str]] = []
        if self._config.license_dimension:
            entitlements.append({"Name": self._config.license_dimension, "Unit": "None"})
        return {
            "CheckoutType": "PROVISIONAL",
            "ProductSKU": self._config.license_sku,
            "KeyFingerprint": _MARKETPLACE_ISSUER_FINGERPRINT,
            "Entitlements": entitlements,
            "ClientToken": uuid.uuid4().hex,
        }
