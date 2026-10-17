"""Credential resolver — loads and returns credential metadata for injection."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialCandidate,
    CredentialIdNotFoundError,
    CredentialNameNotFoundError,
    CredentialNotProvisionedError,
)
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.services.credentials.mapping import to_wire
from jentic_one.shared.context import Context
from jentic_one.shared.credential_selection_cache import CredentialHeader, CredentialSelectionCache
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
from jentic_one.shared.url import server_variables_compatible


class ResolvedCredential(BaseModel):
    """Result of credential resolution — enough data for inject_auth."""

    credential_id: str
    # Human-readable credential name from the stored row (`Credential.name`).
    # Carried alongside ``credential_id`` so ``InjectedAuth`` can attribute the
    # material back to the stored credential without a second DB round-trip
    # (#740). Always populated by the resolver; never a secret.
    name: str
    # Owner of the stored credential (`Credential.created_by`). Credential use
    # and health events name it as their subject so the owner sees another
    # actor's use of the credential under owner-scoped event reads.
    created_by: str | None = None
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
    """Resolves a credential for an API tuple from the control DB.

    With a ``cache``, the vendor's candidate headers come from the
    :class:`CredentialSelectionCache` while fresh. Every filter, including the
    caller's binding boundary, still runs on each call, and the winner's secret
    material is always read from the control DB.
    """

    def __init__(self, ctx: Context, *, cache: CredentialSelectionCache | None = None) -> None:
        self._ctx = ctx
        self._cache = cache if cache is not None and cache.enabled else None

    async def resolve(
        self,
        *,
        api: APIReference,
        caller: str,
        credential_name: str | None = None,
        credential_id: str | None = None,
        allowed_credential_ids: Collection[str] | None = None,
        request_server_variables: Mapping[str, str] | None = None,
        server_variables_unresolved: bool = False,
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
                — only queued async payloads from before the boundary existed
                omit the key; every live path passes the bound ids.
                An **empty** collection is a real, deny-all filter — the caller
                is bound to nothing — never a wildcard.
            request_server_variables: The concrete server-variable values of
                the request URL (from discovery). A credential whose stored
                ``server_variables`` give a different value for one of them does
                not cover the request — e.g. a ``region=us`` credential is never
                a match for an ``/eu/…`` URL.
            server_variables_unresolved: Discovery could not determine the
                request URL's server-variable values; a credential scoped by
                ``server_variables`` is then never a match (fail closed).

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

        def select_winner(headers: Sequence[CredentialHeader]) -> CredentialHeader:
            return self._select(
                headers,
                api=api,
                credential_name=credential_name,
                credential_id=credential_id,
                allowed_credential_ids=allowed_credential_ids,
                request_server_variables=request_server_variables,
                server_variables_unresolved=server_variables_unresolved,
            )

        cache = self._cache
        if cache is not None:
            cached = cache.get(api.vendor)
            if cached is not None:
                resolved = await self._resolve_cached(select_winner(cached))
                if resolved is not None:
                    return resolved
                # The winner changed under the cached header: re-resolve fresh.
                cache.discard(api.vendor)

        # Read-only: the header query and the winner's material query need no
        # shared snapshot. At READ COMMITTED (the server default; the
        # application sets no isolation level) each already takes its own, so
        # autocommit drops the BEGIN/ROLLBACK round trips without changing what
        # is read.
        async with self._ctx.control_db.read_session() as session:
            generation = cache.generation if cache is not None else 0
            rows = await CredentialRepository.list_by_vendor(session, api.vendor)
            headers = tuple(CredentialHeader.from_row(row) for row in rows)
            if cache is not None:
                cache.put(api.vendor, headers, generation=generation)

            winner = select_winner(headers)
            credential = next(row for row in rows if row.id == winner.id)
            # ``list_by_vendor`` returns headers only; read secret material for
            # the single winner alone, so unbound/inactive/non-covering
            # candidates' rows are never selected (and 404/409 read none).
            await CredentialRepository.load_material(session, [credential])
            rel = CredentialRepository.material_relationship(credential.type)
            material = getattr(credential, rel) if rel is not None else None
            return self._build_resolved(winner, material)

    async def _resolve_cached(self, header: CredentialHeader) -> ResolvedCredential | None:
        """Build the cached winner, reading its material fresh; ``None`` when stale.

        A type without material (``NO_AUTH``) needs no DB read. Otherwise the
        single material read also returns the winner's current header, and any
        difference from the cached one (deleted, disabled, re-typed, re-scoped,
        renamed) sends the caller back to the DB.
        """
        if CredentialRepository.material_relationship(header.type) is None:
            return self._build_resolved(header, None)
        async with self._ctx.control_db.read_session() as session:
            found = await CredentialRepository.get_active_material(session, header.id, header.type)
        if found is None:
            return None
        credential, material = found
        if CredentialHeader.from_row(credential) != header:
            return None
        return self._build_resolved(header, material)

    def _select(
        self,
        candidates: Sequence[CredentialHeader],
        *,
        api: APIReference,
        credential_name: str | None,
        credential_id: str | None,
        allowed_credential_ids: Collection[str] | None,
        request_server_variables: Mapping[str, str] | None,
        server_variables_unresolved: bool,
    ) -> CredentialHeader:
        """Pick the one credential for the call (see :meth:`resolve` for the order)."""
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
        covering: list[tuple[CredentialHeader, CredentialScope]] = []
        for c in candidates:
            if not c.active:
                continue
            scope = canonical_credential_scope(
                vendor=c.api_vendor, name=c.api_name, version=c.api_version
            )
            if not credential_covers(scope, vendor=api.vendor, name=api.name, version=api.version):
                continue
            if not server_variables_compatible(
                c.server_variables,
                request_server_variables,
                unresolved=server_variables_unresolved,
            ):
                continue
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
        return matches[0]

    @staticmethod
    def _to_candidate(credential: CredentialHeader) -> CredentialCandidate:
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

    def _build_resolved(self, credential: CredentialHeader, material: Any) -> ResolvedCredential:
        """Assemble the result from the header and its material child (or ``None``)."""
        stored_type = StoredCredentialType(credential.type)
        wire_type = to_wire(stored_type)
        server_variables = (
            dict(credential.server_variables) if credential.server_variables else None
        )

        if wire_type == CredentialType.BEARER_TOKEN:
            tvc = material
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                created_by=credential.created_by,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=server_variables,
                encrypted_secret=tvc.encrypted_token_value if tvc else None,
            )

        if wire_type == CredentialType.API_KEY:
            cak = material
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                created_by=credential.created_by,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=server_variables,
                encrypted_secret=cak.encrypted_key if cak else None,
                location=CredentialLocation(cak.location) if cak else CredentialLocation.HEADER,
                field_name=cak.field_name if cak else "Authorization",
            )

        if wire_type == CredentialType.BASIC:
            bc = material
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                created_by=credential.created_by,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=server_variables,
                username=bc.username if bc else None,
                encrypted_password=bc.encrypted_password if bc else None,
            )

        if wire_type == CredentialType.OAUTH2:
            token = material
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                created_by=credential.created_by,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=server_variables,
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
                created_by=credential.created_by,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=server_variables,
            )

        if wire_type == CredentialType.SIGV4:
            sig = material
            return ResolvedCredential(
                credential_id=credential.id,
                name=credential.name,
                created_by=credential.created_by,
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=server_variables,
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
