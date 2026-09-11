"""Control-surface access filter builder for dynamic query scoping."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.scopes import (
    ORG_ADMIN,
    OWNER_ACCESS_REQUESTS_READ,
    OWNER_CREDENTIALS_READ,
)

_OWNER_MODELS: dict[type[Any], Any] = {
    Credential: Credential.created_by,
}

_DELEGATION_SCOPES: dict[type[Any], str] = {
    Credential: OWNER_CREDENTIALS_READ,
    AccessRequest: OWNER_ACCESS_REQUESTS_READ,
}

# ---------------------------------------------------------------------------
# Extra access-filter providers (extension seam).
#
# An out-of-tree extension (e.g. the enterprise package) may widen READ-only
# visibility beyond ownership — e.g. a "shared-with-me" grant — WITHOUT this OSS
# module importing the extension. A provider is `fn(identity, model) -> clause |
# None`; it returns an extra OR-clause for a model it widens, or None otherwise.
# Providers run ONLY on read paths (`include_shared=True`); mutations stay
# owner-only. OSS ships zero providers, so with no extension present behaviour is
# byte-for-byte the owner-scoped default. Mirrors `register_telemetry_event`.
# ---------------------------------------------------------------------------
AccessFilterProvider = Callable[[Identity, type], "ColumnElement[bool] | None"]

_ACCESS_FILTER_PROVIDERS: list[AccessFilterProvider] = []


def register_access_filter_provider(provider: AccessFilterProvider) -> None:
    """Register an extra READ-only visibility-clause provider (idempotent).

    Call at import time from a registering package (see the enterprise
    ``register()``). The clause a provider returns is OR-merged into the caller's
    owner-scoped read filter. Any table the clause references must be readable by
    the control DB role (i.e. live in the ``control`` schema).
    """
    if provider not in _ACCESS_FILTER_PROVIDERS:
        _ACCESS_FILTER_PROVIDERS.append(provider)


def _provider_clauses(identity: Identity, model: type[Any]) -> list[ColumnElement[bool]]:
    """Collect the non-null clauses every registered provider yields for a model."""
    clauses: list[ColumnElement[bool]] = []
    for provider in _ACCESS_FILTER_PROVIDERS:
        extra = provider(identity, model)
        if extra is not None:
            clauses.append(extra)
    return clauses


def _binding_visibility_clause(
    model: type[Any],
    bound_credential_ids: list[str] | None = None,
) -> ColumnElement[bool] | None:
    """Extra visibility grant for ``Credential`` from direct bindings.

    Returns ``None`` when there is nothing to add (no ids, or a model whose
    visibility is not widened by bindings). A credential is visible directly
    by id when it appears in ``bound_credential_ids`` (theme 5 phase 1 — the
    agent's own direct ``agent_credential_bindings``, resolved by the service
    from the admin DB, suspended bindings already excluded). This stays within
    the control DB — the ids are supplied by the caller, so no admin table is
    referenced here.
    """
    if model is Credential and bound_credential_ids:
        return Credential.id.in_(bound_credential_ids)
    return None


def build_access_filters(
    identity: Identity,
    model: type[Any],
    *,
    bound_credential_ids: list[str] | None = None,
    include_shared: bool = False,
) -> list[ColumnElement[bool]]:
    """Build SQLAlchemy filter expressions scoping queries to the caller's visibility.

    Rules (evaluated in order):
    1. org:admin -> no restriction (empty list).
    2. Agent with delegation scope + parent_actor_id -> OR filter.
    3. Otherwise -> owner == self.

    ``bound_credential_ids`` widens visibility for the ``Credential`` model:
    an agent may always read a credential it holds an active (non-suspended)
    ``agent_credential_bindings`` row for — regardless of owner scoping
    (issues #665/#682). This matters for an orphaned agent
    (``created_by``/owner ``None``) that owns nothing yet is legitimately
    bound to a credential. Bindings live in the admin DB, so the service
    resolves the ids there (via
    :meth:`PrerequisiteRepository.list_credential_ids_for_agent`) and passes
    them in; this module stays single-DB and free of admin imports.
    ``None``/empty leaves the owner-only behaviour unchanged. Read call sites
    only; writes stay owner-scoped.

    ``include_shared`` (READ call sites only) invokes any registered
    access-filter providers (see :func:`register_access_filter_provider`) and
    OR-merges their clauses — e.g. an extension's "shared-with-me" grant. It must
    NOT be set on write call sites, so a sharee can see but never mutate. With no
    providers registered (stock OSS) it is a no-op.

    Raises ValueError for an unknown model or empty sub.
    """
    if ORG_ADMIN in identity.permissions:
        return []

    if not identity.sub:
        raise ValueError("empty sub reached scoped read")

    if model in _OWNER_MODELS:
        col = _OWNER_MODELS[model]
        delegation_scope = _DELEGATION_SCOPES.get(model)
        if (
            delegation_scope is not None
            and delegation_scope in identity.permissions
            and identity.parent_actor_id is not None
        ):
            owner_clause: ColumnElement[bool] = or_(
                col == identity.sub, col == identity.parent_actor_id
            )
        else:
            owner_clause = col == identity.sub
        clauses: list[ColumnElement[bool]] = [owner_clause]
        binding_clause = _binding_visibility_clause(model, bound_credential_ids)
        if binding_clause is not None:
            clauses.append(binding_clause)
        if include_shared:
            clauses.extend(_provider_clauses(identity, model))
        return [or_(*clauses)] if len(clauses) > 1 else clauses

    if model is AccessRequest:
        sub = identity.sub
        delegation_scope = _DELEGATION_SCOPES[AccessRequest]
        if delegation_scope in identity.permissions and identity.parent_actor_id is not None:
            return [
                or_(
                    AccessRequest.created_by == sub,
                    AccessRequest.filer_owner_id == sub,
                    AccessRequest.created_by == identity.parent_actor_id,
                    AccessRequest.filer_owner_id == identity.parent_actor_id,
                )
            ]
        return [
            or_(
                AccessRequest.created_by == sub,
                AccessRequest.filer_owner_id == sub,
            )
        ]

    raise ValueError(f"Unknown model for access scoping: {model.__name__}")


def credential_owner_scope(identity: Identity) -> list[str] | None:
    """Return the credential owner ids visible to ``identity``, or ``None`` for all.

    ``None`` means an ``org:admin`` decider who may act across every owner. For
    everyone else the scope is their own ``sub`` plus, when they hold the
    credential-read delegation scope and have a parent, the parent owner id —
    mirroring :func:`build_access_filters` for the ``Credential`` model. Used to
    confine reference-based ``credential:bind`` effect resolution to credentials
    the decider can actually see (theme-5 Phase 3, hard problem 8 — the
    binding-widened half of that axis is pushed down separately as an id list).
    """
    if ORG_ADMIN in identity.permissions:
        return None
    if not identity.sub:
        raise ValueError("empty sub reached credential owner scope")
    owners = [identity.sub]
    if OWNER_CREDENTIALS_READ in identity.permissions and identity.parent_actor_id is not None:
        owners.append(identity.parent_actor_id)
    return owners
