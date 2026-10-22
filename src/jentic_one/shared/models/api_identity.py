"""Canonical normalization and coverage for API identity fields.

The registry slugifies ``vendor``/``name`` on import; any other layer that
persists or compares those fields must use the *same* normalization, or an
otherwise-identical identity mismatches on exact string equality and silently
default-denies. Keep this helper as the single source of truth.

A **spec/operation identity is always concrete** ``(vendor, name, version)`` —
the registry hard-fails otherwise. A **credential is scoped** at ``vendor`` /
``vendor.name`` / ``vendor.name.version``; an unset ``name``/``version`` axis is
``None`` (the single wildcard sentinel — never ``''``). The coverage predicate,
canonical scope, and the SQL fragment builder below are the one definition of
"does this credential cover this operation?", shared by all three matchers
(broker runtime, control bind-time, broker credential resolution).

A **profile-scoped credential** (``cli_profile`` set, one connect for a CLI
profile such as ``github``) is stored vendor-wide and narrowed to exactly the
APIs its profile's shipped manifest declares: it covers an operation only when
the caller passes that profile as ``declared_by`` (Python) or ``:cli_profile``
(SQL) — :func:`jentic_one.shared.cli_profiles.declaring_profile` of the
operation identity. A matcher that does not know the declaring profile passes
``None``, so a profile credential is never covered by accident (fail closed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

API_FIELD_MAX_LENGTH = 100
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify_api_field(value: str) -> str:
    """Normalize an API vendor/name field to its canonical slug form.

    Lowercase, strip, replace runs of non-``[a-z0-9-]`` with a single hyphen,
    trim leading/trailing hyphens, and truncate to ``API_FIELD_MAX_LENGTH``.
    """
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:API_FIELD_MAX_LENGTH]


@dataclass(frozen=True, slots=True)
class CredentialScope:
    """A credential's stored API scope.

    ``name``/``version`` of ``None`` means "this axis is unscoped (wildcard) —
    covers any". ``vendor`` is always present (a credential is always at least
    vendor-scoped).
    """

    vendor: str
    name: str | None
    version: str | None
    #: The CLI profile a profile-scoped credential belongs to; ``None`` for
    #: every other credential.
    cli_profile: str | None = None


def canonical_credential_scope(
    *, vendor: str, name: str | None, version: str | None, cli_profile: str | None = None
) -> CredentialScope:
    """Canonicalize a *credential* scope for storage or comparison.

    - ``vendor``/``name`` slugified (name only when present),
    - empty string coerced to ``None`` (the single wildcard sentinel),
    - ``version`` trimmed and empty→``None`` but **never slugified** — slugifying
      would corrupt a real version (``1.1.4`` → ``1-1-4``).

    Credential-only: do **not** apply the empty→``None`` coercion to spec
    identities. Specs are always concrete and the registry enforces that
    separately; coercing there would silently weaken that guarantee.
    """
    slug_name = slugify_api_field(name) if name else ""
    trimmed_version = version.strip() if version else ""
    return CredentialScope(
        vendor=slugify_api_field(vendor),
        name=slug_name or None,
        version=trimmed_version or None,
        cli_profile=cli_profile or None,
    )


def credential_covers(
    scope: CredentialScope,
    *,
    vendor: str,
    name: str,
    version: str,
    declared_by: str | None = None,
) -> bool:
    """Return whether ``scope`` covers the (always concrete) operation identity.

    A ``None`` axis on the scope is an unscoped wildcard (covers any value);
    otherwise the axis must equal the operation axis in canonical form. The
    operation identity is normalized here so a legacy non-canonical stored scope
    and a concrete operation compare on the same footing.

    ``declared_by`` is the CLI profile declaring the operation identity
    (``shared.cli_profiles.declaring_profile``); a profile-scoped scope covers
    the operation only when it is that scope's profile.
    """
    if scope.cli_profile is not None and scope.cli_profile != declared_by:
        return False
    return (
        scope.vendor == slugify_api_field(vendor)
        and (scope.name is None or scope.name == slugify_api_field(name))
        and (scope.version is None or scope.version == version.strip())
    )


def credential_specificity(scope: CredentialScope) -> int:
    """Rank for most-specific-wins credential selection (M3 only).

    Each pinned axis adds one, so ``vendor.name.version`` (2) beats
    ``vendor.name`` (1) beats a bare ``vendor`` wildcard (0).
    """
    return (scope.name is not None) + (scope.version is not None)


def credential_coverage_where(
    *, alias: str = "c", name_scoped: bool = True, version_scoped: bool = True
) -> str:
    """Build the shared SQL coverage fragment.

    Binds ``:vendor``, ``:name``, ``:version`` and ``:cli_profile``.

    The one WHERE fragment used by the two raw-SQL matchers (runtime
    credential-binding derivation and bind-time credential selection). ``NULL``
    on a credential axis is
    the wildcard; there is no ``= ''`` branch (empty strings are coerced away on
    write and backfilled, so ``NULL`` is the only wildcard).

    ``name_scoped=False`` / ``version_scoped=False`` omit that axis's comparison
    entirely — used at bind time when the *reference* is itself a wildcard ("bind
    all of the vendor"), so the axis matches anything. Only plain comparisons are
    emitted (no casts / regex), so the fragment is dialect-portable across
    Postgres and SQLite.
    """
    clauses = [
        f"{alias}.api_vendor = :vendor",
        # A profile-scoped credential covers only the APIs its profile
        # declares: callers bind ``:cli_profile`` to the profile declaring the
        # API (``None`` when none does, which matches no profile credential).
        f"({alias}.cli_profile IS NULL OR {alias}.cli_profile = :cli_profile)",
    ]
    if name_scoped:
        clauses.append(f"({alias}.api_name IS NULL OR {alias}.api_name = :name)")
    if version_scoped:
        clauses.append(f"({alias}.api_version IS NULL OR {alias}.api_version = :version)")
    return " AND ".join(clauses)
