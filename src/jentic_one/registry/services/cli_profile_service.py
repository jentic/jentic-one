"""Provision a CLI profile's APIs: make every API the profile declares exist and be live.

Per source kind (``shared/cli_profiles``):

- ``catalog``: the public-catalog import, through :meth:`CatalogService.ensure_imported`
  (enqueued; nothing when the entry is already registered);
- ``shipped``: the spec shipped in the package, imported inline with origin
  ``cli_profile`` so it goes live in the same transaction (a no-op when the
  API's current revision already has the shipped spec's digest);
- ``builtin``: nothing, the broker serves it.

Provisioning is idempotent and safe to run on several replicas at once: the
catalog path skips a registered entry, and two replicas importing the same
shipped spec race on the registry's ``(api, spec_digest)`` uniqueness, where
the loser's :class:`DuplicateRevisionError` is read back as "already live".
Nothing runs at boot: provisioning is triggered on demand, by a connect for
one of the profile's APIs (the catalog auto-import seam) or by an operator
(``POST /cli-profiles/{profile}:provision``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

import structlog

from jentic_one.registry.ingest.exc import (
    BaseIngestError,
    DuplicateRevisionError,
    HostOwnedByOtherVendorIngestError,
)
from jentic_one.registry.ingest.fetch import InlineSource, load_specification
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.errors import (
    CatalogEntryNotFoundError,
    CliProfileNotFoundError,
    RegistryServiceError,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.cli_profiles import (
    CliProfile,
    ProfileApi,
    SourceKind,
    load_profiles,
    profile_api_for_catalog_api_id,
    shipped_spec_text,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.models import RevisionOrigin

logger = structlog.get_logger(__name__)

ProvisionStatus = Literal[
    "live",
    "imported",
    "import_queued",
    "builtin",
    "held_for_review",
    "not_in_catalog",
    "failed",
]


@dataclass(frozen=True, slots=True)
class ProvisionedApi:
    """What provisioning did (or found) for one of a profile's APIs."""

    role: str
    source: str
    api_id: str
    vendor: str
    name: str
    version: str | None
    status: ProvisionStatus
    detail: str | None = None
    job_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    """A profile's provisioning outcome, one entry per declared API."""

    profile: str
    vendor: str
    vendor_enabled: bool
    apis: tuple[ProvisionedApi, ...]


@dataclass(frozen=True, slots=True)
class ApiAvailability:
    """Whether one of a profile's APIs is live in the registry (read-only)."""

    api: ProfileApi
    live: bool
    vendor: str
    name: str
    version: str | None


class CliProfileService:
    """Reads the shipped CLI profiles and provisions their APIs."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def _vendor_enabled(self, profile: CliProfile) -> bool:
        return self._ctx.config.cli_facades.vendor_enabled(profile.vendor.id)

    def get_profile(self, name: str) -> CliProfile:
        """The shipped profile ``name`` (raises :class:`CliProfileNotFoundError`)."""
        profiles = load_profiles()
        profile = profiles.get(name)
        if profile is None:
            raise CliProfileNotFoundError(name, sorted(profiles))
        return profile

    def list_profiles(self) -> list[tuple[CliProfile, bool]]:
        """Every shipped profile with whether its vendor's facade is enabled."""
        return [(p, self._vendor_enabled(p)) for _, p in sorted(load_profiles().items())]

    async def availability(self, profile: CliProfile) -> list[ApiAvailability]:
        """Whether each of ``profile``'s APIs is live, and under which identity."""
        out: list[ApiAvailability] = []
        async with self._ctx.registry_db.read_session() as session:
            for api in profile.apis:
                if api.source_kind == SourceKind.BUILTIN:
                    out.append(
                        ApiAvailability(
                            api,
                            True,
                            api.identity.vendor,
                            api.identity.name,
                            api.identity.version,
                        )
                    )
                    continue
                found = await ApiRepository.live_identity_for_catalog_api_id(
                    session, api.catalog_api_id
                )
                if found is None:
                    out.append(
                        ApiAvailability(
                            api,
                            False,
                            api.identity.vendor,
                            api.identity.name,
                            api.identity.version,
                        )
                    )
                else:
                    out.append(ApiAvailability(api, True, *found))
        return out

    async def provision(self, name: str, identity: Identity) -> ProvisionResult:
        """Make every API of profile ``name`` exist and be live (an explicit operator action)."""
        profile = self.get_profile(name)
        apis = tuple([await self._provision_api(api, identity) for api in profile.apis])
        logger.info(
            "cli_profile.provisioned",
            profile=profile.profile,
            statuses={a.role: a.status for a in apis},
        )
        return ProvisionResult(
            profile=profile.profile,
            vendor=profile.vendor.id,
            vendor_enabled=self._vendor_enabled(profile),
            apis=apis,
        )

    async def ensure_for_catalog_api_id(
        self, api_id: str, identity: Identity
    ) -> ProvisionResult | None:
        """Provision on demand for a connect to ``api_id``; ``None`` if no profile declares it.

        The connected API itself is always provisioned. When the profile's
        vendor facade is enabled, every other API of the profile is too, so
        the vendor's CLI finds all of them live; while it is off, only the
        connected API is (the connect is not a CLI-profile connect then). A
        ``profile_scoped`` profile's connect creates one credential for all of
        its APIs, so all of them are provisioned whatever the facade's state.
        """
        found = profile_api_for_catalog_api_id(api_id)
        if found is None:
            return None
        profile, target = found
        enabled = self._vendor_enabled(profile)
        apis = profile.apis if enabled or profile.profile_scoped else (target,)
        results = tuple([await self._provision_api(api, identity) for api in apis])
        return ProvisionResult(
            profile=profile.profile,
            vendor=profile.vendor.id,
            vendor_enabled=enabled,
            apis=results,
        )

    async def _provision_api(self, api: ProfileApi, identity: Identity) -> ProvisionedApi:
        if api.source_kind == SourceKind.BUILTIN:
            return self._view(api, "builtin")
        if api.source_kind == SourceKind.CATALOG:
            return await self._provision_catalog(api, identity)
        return await self._provision_shipped(api, identity)

    @staticmethod
    def _view(
        api: ProfileApi,
        status: ProvisionStatus,
        *,
        detail: str | None = None,
        job_id: str | None = None,
        identity: tuple[str, str, str | None] | None = None,
    ) -> ProvisionedApi:
        vendor, name, version = identity or (
            api.identity.vendor,
            api.identity.name,
            api.identity.version,
        )
        return ProvisionedApi(
            role=api.role.value,
            source=api.source,
            api_id=api.catalog_api_id,
            vendor=vendor,
            name=name,
            version=version,
            status=status,
            detail=detail,
            job_id=job_id,
        )

    async def _live_identity(self, api: ProfileApi) -> tuple[str, str, str] | None:
        async with self._ctx.registry_db.read_session() as session:
            return await ApiRepository.live_identity_for_catalog_api_id(session, api.catalog_api_id)

    async def _provision_catalog(self, api: ProfileApi, identity: Identity) -> ProvisionedApi:
        live = await self._live_identity(api)
        if live is not None:
            return self._view(api, "live", identity=live)
        try:
            job_id = await CatalogService(self._ctx).ensure_imported(api.catalog_api_id, identity)
        except CatalogEntryNotFoundError:
            return self._view(
                api,
                "not_in_catalog",
                detail=(
                    f"{api.catalog_api_id} is not in the public API catalog; import its "
                    "spec with POST /apis"
                ),
            )
        except RegistryServiceError as exc:
            logger.warning(
                "cli_profile.catalog_import_failed", api_id=api.catalog_api_id, error=str(exc)
            )
            return self._view(api, "failed", detail=str(exc))
        if job_id is None:
            # Registered (its spec URL backs a revision) but not under this catalog id.
            return self._view(api, "live")
        return self._view(api, "import_queued", job_id=job_id)

    async def _provision_shipped(self, api: ProfileApi, identity: Identity) -> ProvisionedApi:
        content = shipped_spec_text(api)
        digest = hashlib.sha256(content.encode()).hexdigest()
        version = api.identity.version
        assert version is not None
        if await self._shipped_is_live(api, digest):
            return self._view(api, "live")
        source = InlineSource(
            type="inline",
            content=content,
            filename=api.source_ref.rsplit("/", 1)[-1],
            vendor=api.catalog_api_id.split("/", 1)[0],
            api_name=api.identity.name,
            version=version,
            origin=RevisionOrigin.CLI_PROFILE.value,
            catalog_api_id=api.catalog_api_id,
            submitted_by=identity.sub,
        )
        try:
            spec = await load_specification(source, config=self._ctx.config.ingest)
            result = await Ingestor(self._ctx).ingest(spec, created_by=identity.sub)
        except (DuplicateRevisionError, DatabaseIntegrityError):
            # Another replica (or an earlier run) imported the same spec first.
            if await self._shipped_is_live(api, digest):
                return self._view(api, "live")
            return self._view(
                api,
                "failed",
                detail=(
                    "the shipped spec is already a revision of this API but not its current "
                    "one; promote it or delete that revision"
                ),
            )
        except HostOwnedByOtherVendorIngestError as exc:
            return self._view(api, "failed", detail=str(exc))
        except BaseIngestError as exc:
            # A replica that lost the race to create the API row fails inside a
            # pipeline stage (the unique ``(vendor, name, version)`` insert); the
            # winner has committed by then, so the spec reads as live.
            if await self._shipped_is_live(api, digest):
                return self._view(api, "live")
            logger.warning(
                "cli_profile.shipped_import_failed", api_id=api.catalog_api_id, error=str(exc)
            )
            return self._view(api, "failed", detail=str(exc))
        held = result.held_host_change is not None
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.REVISION,
            target_id=str(result.revision_id),
            actor_type=str(identity.actor_type),
            actor_id=identity.sub,
            after={
                "vendor": result.api_vendor,
                "name": result.api_name,
                "version": result.api_version,
                "state": result.state,
                "origin": RevisionOrigin.CLI_PROFILE.value,
            },
            reason="server_host_change_held" if held else "cli_profile_provisioned",
            origin=None,
        )
        if held:
            return self._view(
                api,
                "held_for_review",
                detail="the shipped spec changes the API's server hosts; promote it to apply",
            )
        return self._view(api, "imported")

    async def _shipped_is_live(self, api: ProfileApi, digest: str) -> bool:
        version = api.identity.version
        assert version is not None
        async with self._ctx.registry_db.read_session() as session:
            live, current = await ApiRepository.current_revision_digest(
                session, api.identity.vendor, api.identity.name, version
            )
        return live and current == digest


__all__ = [
    "ApiAvailability",
    "CliProfileService",
    "ProvisionResult",
    "ProvisionStatus",
    "ProvisionedApi",
]
