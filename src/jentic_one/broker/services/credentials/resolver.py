"""Credential resolver — loads and returns credential metadata for injection."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialCandidate,
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


class CredentialResolver:
    """Resolves a credential for an API tuple from the control DB."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def resolve(
        self, *, api: APIReference, caller: str, credential_name: str | None = None
    ) -> ResolvedCredential:
        """Resolve a single active credential for the API tuple.

        Args:
            api: API vendor/name/version tuple to resolve credentials for.
            caller: Identity of the requesting party — reserved for future ACL/audit-logging.
            credential_name: Optional human-readable name to disambiguate multiple matches.

        Raises CredentialNotProvisionedError if no match.
        Raises AmbiguousCredentialError if >1 match and no credential_name given.
        Raises CredentialNameNotFoundError if credential_name doesn't match any candidate.
        """
        async with self._ctx.control_db.session() as session:
            candidates = await CredentialRepository.list_by_vendor(session, api.vendor)

            # Coverage + specificity via the shared seam. A credential's stored
            # scope (canonicalized here so legacy '' / non-slug rows compare on
            # the same footing) covers the concrete operation when each axis is
            # either unscoped (NULL → wildcard) or equal. Among covering
            # credentials, most-specific-wins: a vendor.name.version pin beats a
            # vendor.name which beats a bare vendor wildcard, so a vendor-wide
            # credential coexisting with a pin no longer forces a spurious 409.
            def _scope(c: Credential) -> CredentialScope:
                return canonical_credential_scope(
                    vendor=c.api_vendor, name=c.api_name, version=c.api_version
                )

            covering = [
                c
                for c in candidates
                if c.active
                and credential_covers(
                    _scope(c), vendor=api.vendor, name=api.name, version=api.version
                )
            ]

            if not covering:
                raise CredentialNotProvisionedError(api.vendor, api.name, api.version)

            best = max(credential_specificity(_scope(c)) for c in covering)
            matches = [c for c in covering if credential_specificity(_scope(c)) == best]

            candidate_objs = [self._to_candidate(c) for c in matches]

            if credential_name is not None:
                filtered = [c for c in matches if c.name == credential_name]
                if not filtered:
                    raise CredentialNameNotFoundError(
                        api.vendor, api.name, api.version, credential_name, candidate_objs
                    )
                matches = filtered

            if len(matches) > 1:
                raise AmbiguousCredentialError(
                    api.vendor, api.name, api.version, len(matches), candidates=candidate_objs
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
                wire_type=wire_type,
                stored_type=stored_type,
                provider=credential.provider,
                server_variables=credential.server_variables,
                encrypted_access_token=token.encrypted_access_token if token else None,
                encrypted_refresh_token=token.encrypted_refresh_token if token else None,
                token_expires_at=token.expires_at if token else None,
                provider_account_ref=credential.provider_account_ref,
            )

        raise CredentialNotProvisionedError(
            credential.api_vendor,
            credential.api_name or "",
            credential.api_version or "",
        )
