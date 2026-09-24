"""Credential resolver — loads and returns credential metadata for injection."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from pydantic import BaseModel

from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialCandidate,
    CredentialIdNotFoundError,
    CredentialNameNotFoundError,
    CredentialNotProvisionedError,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.services.credentials.mapping import to_wire
from jentic_one.shared.context import Context
from jentic_one.shared.models.api_identity import (
    CredentialScope,
    canonical_credential_scope,
    credential_covers,
    credential_specificity,
)
from jentic_one.shared.models.credentials import (
    CredentialLocation,
    CredentialType,
    StoredCredentialType,
)
from jentic_one.shared.schemas import APIReference


class ResolvedCredential(BaseModel):
    """Result of credential resolution — enough data for inject_auth."""

    credential_id: str
    # Human-readable credential name from the stored row (`Credential.name`).
    # Carried alongside ``credential_id`` so ``InjectedAuth`` can attribute the
    # material back to the stored credential without a second DB round-trip
    # (#740). Always populated by the resolver; never a secret.
    name: str
    wire_type: CredentialType
    stored_type: StoredCredentialType
    provider: str
    server_variables: dict[str, str] | None = None

    # bearer_token / api_key
    encrypted_secret: str | None = None

    # api_key injection params
    location: CredentialLocation | None = None
    field_name: str | None = None

    # basic
    username: str | None = None
    encrypted_password: str | None = None

    # oauth2 access token
    encrypted_access_token: str | None = None
    encrypted_refresh_token: str | None = None
    token_expires_at: datetime | None = None
    provider_account_ref: str | None = None

    # sigv4
    access_key_id: str | None = None
    encrypted_secret_access_key: str | None = None
    encrypted_session_token: str | None = None
    aws_region: str | None = None
    aws_service: str | None = None


class CredentialResolver:
    """Resolves a credential for an API tuple from the control DB."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def resolve(
        self,
        *,
        api: APIReference,
        caller: str,
        credential_name: str | None = None,
        credential_id: str | None = None,
        allowed_credential_ids: Collection[str] | None = None,
    ) -> ResolvedCredential:
        """Resolve a single active credential for the API tuple.

        Args:
            api: API vendor/name/version tuple to resolve credentials for.
            caller: Identity of the requesting party — reserved for future ACL/audit-logging.
            credential_name: Optional human-readable name to disambiguate multiple matches.
            credential_id: Optional exact credential id — the authoritative
                disambiguation signal (``Jentic-Credential-Id``); applied before
                the name filter and specificity narrowing.
            allowed_credential_ids: When not ``None``, the **injection boundary**
                (theme-5 Q-02): only these credential ids may resolve, filtered
                *before* coverage matching. ``None`` means "no binding filter"
                (the legacy toolkit path, whose binding check happens upstream).
                An **empty** collection is a real, deny-all filter — the caller
                is bound to nothing — never a wildcard.

        Raises CredentialNotProvisionedError if no match.
        Raises AmbiguousCredentialError if >1 match and no credential_name given.
        Raises CredentialNameNotFoundError if credential_name doesn't match any candidate.
        Raises CredentialIdNotFoundError if credential_id doesn't match any candidate.

        Resolution order: restrict to ``allowed_credential_ids`` (pre-coverage),
        filter to credentials whose stored scope *covers* the API, then apply
        ``credential_id`` (strongest signal — an explicit id is exact), then —
        if a ``credential_name`` is given — restrict to that name across **all**
        covering credentials (an explicit name can select a
        covering-but-less-specific credential), and only then apply
        most-specific-wins to break ties.
        """
        async with self._ctx.control_db.session() as session:
            candidates = await CredentialRepository.list_by_vendor(session, api.vendor)

            # Injection boundary (Q-02): an unbound credential must never be
            # considered, even if it covers the API. Applied before coverage
            # matching so nothing downstream (name filter, specificity, the
            # ambiguity 409 candidate list) can ever surface an unbound row.
            if allowed_credential_ids is not None:
                allowed = set(allowed_credential_ids)
                candidates = [c for c in candidates if c.id in allowed]

            # Coverage + specificity via the shared seam. A credential's stored
            # scope (canonicalized here so legacy '' / non-slug rows compare on
            # the same footing) covers the concrete operation when each axis is
            # either unscoped (NULL → wildcard) or equal. Precompute (cred, scope)
            # once so the scope isn't recomputed per predicate below.
            covering: list[tuple[Credential, CredentialScope]] = []
            for c in candidates:
                if not c.active:
                    continue
                scope = canonical_credential_scope(
                    vendor=c.api_vendor, name=c.api_name, version=c.api_version
                )
                if credential_covers(scope, vendor=api.vendor, name=api.name, version=api.version):
                    covering.append((c, scope))

            if not covering:
                raise CredentialNotProvisionedError(api.vendor, api.name, api.version)

            # An explicit credential_id is exact — the authoritative tie-breaker
            # (Jentic-Credential-Id). Applied first: an id names one row, so no
            # later filter can meaningfully narrow further; a name/specificity
            # pass after an id match would only manufacture spurious errors.
            if credential_id is not None:
                by_id = [(c, s) for (c, s) in covering if c.id == credential_id]
                if not by_id:
                    raise CredentialIdNotFoundError(
                        api.vendor,
                        api.name,
                        api.version,
                        credential_id,
                        [self._to_candidate(c) for (c, _) in covering],
                    )
                covering = by_id

            # An explicit credential_name is the strongest *name-side* signal
            # (only an exact id beats it), so it searches *all* covering
            # credentials — including a
            # covering-but-less-specific one (e.g. the vendor-wide credential
            # while a pin also exists). Applying it before specificity narrowing
            # means naming that credential resolves it instead of a spurious
            # CredentialNameNotFoundError.
            if credential_name is not None:
                named = [(c, s) for (c, s) in covering if c.name == credential_name]
                if not named:
                    raise CredentialNameNotFoundError(
                        api.vendor,
                        api.name,
                        api.version,
                        credential_name,
                        [self._to_candidate(c) for (c, _) in covering],
                    )
                covering = named

            # Among the remaining covering credentials, most-specific-wins: a
            # vendor.name.version pin beats a vendor.name which beats a bare
            # vendor wildcard, so a vendor-wide credential coexisting with a pin
            # doesn't force a spurious 409.
            best = max(credential_specificity(s) for (_, s) in covering)
            matches = [c for (c, s) in covering if credential_specificity(s) == best]

            if len(matches) > 1:
                raise AmbiguousCredentialError(
                    api.vendor,
                    api.name,
                    api.version,
                    len(matches),
                    candidates=[self._to_candidate(c) for c in matches],
                )

            credential = matches[0]
            stored_type = StoredCredentialType(credential.type)
            wire_type = to_wire(stored_type)

            return self._build_resolved(credential, wire_type, stored_type)

    @staticmethod
    def _to_candidate(credential: Credential) -> CredentialCandidate:
        """Build a distinguishable ambiguity candidate (issue #643).

        ``last4`` is the tail of the non-secret credential id — never the secret
        — so two same-named credentials render distinctly in the 409 body.
        """
        return CredentialCandidate(
            id=credential.id,
            name=credential.name,
            last4=credential.id[-4:],
            created_at=credential.created_at,
        )

    def _build_resolved(
        self,
        credential: Credential,
        wire_type: CredentialType,
        stored_type: StoredCredentialType,
    ) -> ResolvedCredential:

        if wire_type == CredentialType.BEARER_TOKEN:
            tvc = credential.token_value_credential
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=credential.server_variables,
                encrypted_secret=tvc.encrypted_token_value if tvc else None,
            )

        if wire_type == CredentialType.API_KEY:
            cak = credential.customer_api_key
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=credential.server_variables,
                encrypted_secret=cak.encrypted_key if cak else None,
                location=CredentialLocation(cak.location) if cak else CredentialLocation.HEADER,
                field_name=cak.field_name if cak else "Authorization",
            )

        if wire_type == CredentialType.BASIC:
            bc = credential.basic_credential
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=credential.server_variables,
                username=bc.username if bc else None,
                encrypted_password=bc.encrypted_password if bc else None,
            )

        if wire_type == CredentialType.OAUTH2:
            token = credential.oauth_token
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=credential.server_variables,
                encrypted_access_token=token.encrypted_access_token if token else None,
                encrypted_refresh_token=token.encrypted_refresh_token if token else None,
                token_expires_at=token.expires_at if token else None,
                provider_account_ref=credential.provider_account_ref,
            )

        if wire_type == CredentialType.NO_AUTH:
            # No secret to resolve — the API needs no auth. inject_auth returns an
            # empty InjectionResult for this wire type. Server variables still
            # apply so region/host templating works for no-auth APIs (#603).
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=credential.server_variables,
            )

        if wire_type == CredentialType.SIGV4:
            sig = credential.sigv4_credential
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=credential.server_variables,
                access_key_id=sig.access_key_id if sig else None,
                encrypted_secret_access_key=sig.encrypted_secret_access_key if sig else None,
                encrypted_session_token=sig.encrypted_session_token if sig else None,
                aws_region=sig.region if sig else None,
                aws_service=sig.service if sig else None,
            )

        raise CredentialNotProvisionedError(
            credential.api_vendor,
            credential.api_name or "",
            credential.api_version or "",
        )
