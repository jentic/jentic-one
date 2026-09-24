"""AWS credential resolution for the entitlement check (no boto3).

Resolves SigV4 signing material from the standard AWS runtime sources, in the
same precedence order as the AWS SDKs' default chain — but implemented on
``httpx`` + stdlib so the entitlement gate adds no new dependency (mirroring
``shared/aws/sigv4.py``'s posture):

1. **Static env** — ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY``
   (+ optional ``AWS_SESSION_TOKEN``). Never expires.
2. **ECS/Fargate task role** — the container-credentials endpoint advertised
   via ``AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`` (or ``…_FULL_URI`` with an
   optional ``AWS_CONTAINER_AUTHORIZATION_TOKEN``).
3. **EKS IRSA** — ``AWS_WEB_IDENTITY_TOKEN_FILE`` + ``AWS_ROLE_ARN``,
   exchanged at STS via ``AssumeRoleWithWebIdentity``. That STS action accepts
   **unsigned** requests (the web-identity token is the proof), so there is no
   signing bootstrap problem.

Temporary credentials are cached per provider until shortly before expiry.
Secret values are never logged — only which source matched (redaction rule).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from jentic_one.shared.aws.sigv4 import SigV4Material

_log = structlog.get_logger(__name__)

# The fixed link-local host serving ECS/Fargate task-role credentials when only
# the RELATIVE_URI variable is set. See the AWS "container credential provider"
# documentation.
_ECS_CREDENTIALS_HOST = "http://169.254.170.2"
# Refresh temporary credentials this many seconds before they actually expire,
# so an in-flight check never signs with material AWS already rejects.
_EXPIRY_SKEW_S = 120.0


class CredentialResolutionError(Exception):
    """No AWS credential source matched (or the matching source failed)."""


@dataclass(slots=True)
class _CachedMaterial:
    material: SigV4Material
    # ``time.monotonic()`` deadline after which the material must be re-resolved;
    # ``None`` means it never expires (static env keys).
    refresh_at: float | None


def _expiry_to_monotonic_deadline(expiration_iso: str | None) -> float | None:
    """Convert an ISO-8601 ``Expiration`` to a monotonic refresh deadline."""
    if not expiration_iso:
        return None
    expires_at = datetime.fromisoformat(expiration_iso.replace("Z", "+00:00"))
    remaining = (expires_at - datetime.now(UTC)).total_seconds()
    return time.monotonic() + max(remaining - _EXPIRY_SKEW_S, 0.0)


class CredentialProvider:
    """Resolves and caches SigV4 material for one (region, service) pair.

    One instance is owned by the license client so the cache and its
    single-flight lock are shared across the checker's refresh calls. The
    ``httpx.AsyncClient`` is injected (not owned) so tests stub the metadata /
    STS endpoints with ``httpx.MockTransport`` and production shares the
    client the license client already holds.
    """

    def __init__(self, http: httpx.AsyncClient, *, region: str, service: str) -> None:
        self._http = http
        self._region = region
        self._service = service
        self._cached: _CachedMaterial | None = None
        self._lock = asyncio.Lock()

    async def resolve(self) -> SigV4Material:
        """Return valid signing material, re-resolving expired temporaries.

        Raises :class:`CredentialResolutionError` when no source is configured
        or the matching source fails — the checker maps that to an ``UNKNOWN``
        verdict, never a crash.
        """
        cached = self._cached
        now = time.monotonic()
        if cached is not None and (cached.refresh_at is None or now < cached.refresh_at):
            return cached.material

        async with self._lock:
            cached = self._cached
            now = time.monotonic()
            if cached is not None and (cached.refresh_at is None or now < cached.refresh_at):
                return cached.material
            resolved = await self._resolve_uncached()
            self._cached = resolved
            return resolved.material

    async def _resolve_uncached(self) -> _CachedMaterial:
        env = os.environ
        if env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY"):
            _log.info("entitlement.credentials_resolved", source="static_env")
            return _CachedMaterial(
                material=self._material(
                    access_key_id=env["AWS_ACCESS_KEY_ID"],
                    secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
                    session_token=env.get("AWS_SESSION_TOKEN"),
                ),
                refresh_at=None,
            )
        if env.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI") or env.get(
            "AWS_CONTAINER_CREDENTIALS_FULL_URI"
        ):
            material = await self._resolve_container()
            _log.info("entitlement.credentials_resolved", source="container_task_role")
            return material
        if env.get("AWS_WEB_IDENTITY_TOKEN_FILE") and env.get("AWS_ROLE_ARN"):
            material = await self._resolve_web_identity()
            _log.info("entitlement.credentials_resolved", source="web_identity")
            return material
        raise CredentialResolutionError(
            "no AWS credential source configured (checked static env, "
            "container task role, web identity)"
        )

    async def _resolve_container(self) -> _CachedMaterial:
        """ECS/Fargate task-role credentials from the container endpoint."""
        env = os.environ
        url = env.get("AWS_CONTAINER_CREDENTIALS_FULL_URI") or (
            _ECS_CREDENTIALS_HOST + env["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"]
        )
        headers: dict[str, str] = {}
        auth_token = env.get("AWS_CONTAINER_AUTHORIZATION_TOKEN")
        if auth_token:
            headers["authorization"] = auth_token
        try:
            response = await self._http.get(url, headers=headers)
            response.raise_for_status()
            doc: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CredentialResolutionError("container credential endpoint request failed") from exc
        return _CachedMaterial(
            material=self._material(
                access_key_id=doc["AccessKeyId"],
                secret_access_key=doc["SecretAccessKey"],
                session_token=doc.get("Token"),
            ),
            refresh_at=_expiry_to_monotonic_deadline(doc.get("Expiration")),
        )

    async def _resolve_web_identity(self) -> _CachedMaterial:
        """EKS IRSA: exchange the mounted OIDC token at STS (unsigned call)."""
        env = os.environ
        try:
            token = Path(env["AWS_WEB_IDENTITY_TOKEN_FILE"]).read_text().strip()
        except OSError as exc:
            raise CredentialResolutionError("web identity token file unreadable") from exc
        body = urlencode(
            {
                "Action": "AssumeRoleWithWebIdentity",
                "Version": "2011-06-15",
                "RoleArn": env["AWS_ROLE_ARN"],
                "RoleSessionName": "jentic-entitlement",
                "WebIdentityToken": token,
            }
        )
        try:
            response = await self._http.post(
                f"https://sts.{self._region}.amazonaws.com/",
                content=body,
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                    "accept": "application/json",
                },
            )
            response.raise_for_status()
            doc = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CredentialResolutionError("STS AssumeRoleWithWebIdentity failed") from exc
        try:
            credentials = doc["AssumeRoleWithWebIdentityResponse"][
                "AssumeRoleWithWebIdentityResult"
            ]["Credentials"]
        except (KeyError, TypeError) as exc:
            raise CredentialResolutionError("unexpected STS response shape") from exc
        expiration = credentials.get("Expiration")
        # STS's JSON serialisation returns the expiry as an epoch number.
        refresh_at: float | None
        if isinstance(expiration, int | float):
            refresh_at = time.monotonic() + max(
                expiration - datetime.now(UTC).timestamp() - _EXPIRY_SKEW_S, 0.0
            )
        else:
            refresh_at = _expiry_to_monotonic_deadline(expiration)
        return _CachedMaterial(
            material=self._material(
                access_key_id=credentials["AccessKeyId"],
                secret_access_key=credentials["SecretAccessKey"],
                session_token=credentials.get("SessionToken"),
            ),
            refresh_at=refresh_at,
        )

    def _material(
        self, *, access_key_id: str, secret_access_key: str, session_token: str | None
    ) -> SigV4Material:
        return SigV4Material(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region=self._region,
            service=self._service,
            session_token=session_token,
        )
