"""Pure classification, planning, and backup helpers for credential/API identity repair.

Covers two classes of stored identity defects across registry and control rows:

- Vendor-doubled catalog identities (#1079), such as ``posthog-com /
  posthog-com-posthog-api`` or ``github-com-api-github-com``, produced when a
  ``domain/sub`` catalog id was slugified as a single string rather than split
  into vendor and sub-API name.
- Path-derived credential scopes (#1388), such as ``github-com / openapi /
  api.github.com`` or unsplit ``openapi/main`` / ``openapi.json`` segments,
  produced when spec-file path components were stored in ``api_name`` or
  ``api_version``.

All functions here are pure (no database or network I/O) so the planner is
deterministic and shared across upgrade steps, CLI dry-run/apply/restore, and
unit tests without violating module boundaries.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from publicsuffixlist import PublicSuffixList

from jentic_one.shared.models.api_identity import (
    CredentialScope,
    canonical_credential_scope,
    credential_covers,
    slugify_api_field,
)
from jentic_one.shared.vendor_domain import vendor_from_api_id

BACKUP_FORMAT = "jentic-one-credential-identity-repair-backup"
BACKUP_SCHEMA_VERSION = 1

_SPEC_DIR_TOKENS = frozenset({"apis", "openapi", "swagger", "specs", "apis-openapi"})
_BRANCH_TOKENS = frozenset({"main", "master", "heads", "refs"})
_SPEC_FILE_TOKENS = frozenset(
    {
        "openapi.json",
        "openapi.yaml",
        "openapi.yml",
        "swagger.json",
        "swagger.yaml",
        "swagger.yml",
        "apis.json",
        "spec.json",
        "spec.yaml",
        "spec.yml",
    }
)
_SPEC_FILE_EXTENSIONS = (".json", ".yaml", ".yml")
_SEMVER_PREFIX_RE = re.compile(r"^v?\d+\.\d+", re.IGNORECASE)
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)

OPEN_CONNECT_SESSION_STATES = frozenset({"created", "awaiting_app", "polling"})


class IdentityRepairBackupError(ValueError):
    """Raised when a backup document is malformed, mismatched, or unsafe to restore."""


@cache
def _known_psl() -> PublicSuffixList:
    """Public Suffix List with unknown TLDs rejected so version strings do not match."""
    return PublicSuffixList(only_icann=False, accept_unknown=False)


def looks_like_domain_segment(value: str) -> bool:
    """Return True when ``value`` is a domain/hostname segment rather than a version.

    Checks against the bundled Public Suffix List with ``accept_unknown=False``
    and rejects numeric/semver prefixes, so ``api.github.com`` and
    ``stripe.com`` match while ``1.1.4``, ``1.1.4-beta.1``, and ``v2.0`` do not.
    """
    cleaned = value.strip().lower().rstrip(".")
    if not cleaned or "/" in cleaned or _SEMVER_PREFIX_RE.match(cleaned):
        return False
    if cleaned.endswith(_SPEC_FILE_EXTENSIONS):
        return False
    labels = cleaned.split(".")
    if len(labels) < 2:
        return False
    if not all(_HOST_LABEL_RE.match(label) for label in labels):
        return False
    if not labels[-1].isalpha() or len(labels[-1]) < 2:
        return False
    return _known_psl().publicsuffix(cleaned) is not None


def catalog_entry_api_name(api_id: str, manifest_api_ids: Iterable[str]) -> str:
    """Derive the workspace ``api_name`` seed for a catalog ``api_id``.

    A ``domain/sub`` entry yields ``sub`` unless another manifest entry collapses
    onto the same ``(vendor, slug(sub))``, in which case every clashing member
    keeps its full host-qualified ``api_id``. Bare-domain entries keep ``api_id``.
    """
    host, sep, sub = api_id.partition("/")
    if not sep or not sub:
        return api_id
    vendor = vendor_from_api_id(host)
    key = slugify_api_field(sub)
    for other in manifest_api_ids:
        if other == api_id:
            continue
        other_host, other_sep, other_sub = other.partition("/")
        if (
            other_sep
            and other_sub
            and slugify_api_field(other_sub) == key
            and vendor_from_api_id(other_host) == vendor
        ):
            return api_id
    return sub


@dataclass(frozen=True)
class RegistryApiSnapshot:
    """Minimal non-secret snapshot of a ``registry.apis`` row."""

    id: str
    vendor: str
    name: str
    version: str
    catalog_api_id: str | None = None


@dataclass(frozen=True)
class CredentialSnapshot:
    """Minimal non-secret snapshot of a ``control.credentials`` row."""

    id: str
    name: str
    created_by: str | None
    api_vendor: str
    api_name: str | None
    api_version: str | None
    catalog_api_id: str | None = None


@dataclass(frozen=True)
class ConnectSessionSnapshot:
    """Minimal snapshot of an ``api``-target ``control.connect_sessions`` row."""

    id: str
    credential_id: str
    agent_id: str | None
    initiator_actor_id: str
    state: str
    target_kind: str
    vendor: str
    api_name: str | None
    api_version: str | None


@dataclass(frozen=True)
class ApiReferenceCounts:
    """Cross-table reference counts for a ``registry.apis`` row."""

    revisions: int = 0
    operations: int = 0
    overlays: int = 0
    notes: int = 0
    pending_held_executions: int = 0
    execution_records: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "revisions": self.revisions,
            "operations": self.operations,
            "overlays": self.overlays,
            "notes": self.notes,
            "pending_held_executions": self.pending_held_executions,
            "execution_records": self.execution_records,
        }


@dataclass(frozen=True)
class CredentialReferenceCounts:
    """Cross-table reference counts for a ``control.credentials`` row."""

    active_bindings: int = 0
    suspended_bindings: int = 0
    bound_agent_credentials: int = 0
    permission_rules: int = 0
    open_connect_sessions: int = 0
    closed_connect_sessions: int = 0
    pending_held_executions: int = 0
    terminal_held_executions: int = 0
    execution_records: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "active_bindings": self.active_bindings,
            "suspended_bindings": self.suspended_bindings,
            "bound_agent_credentials": self.bound_agent_credentials,
            "permission_rules": self.permission_rules,
            "open_connect_sessions": self.open_connect_sessions,
            "closed_connect_sessions": self.closed_connect_sessions,
            "pending_held_executions": self.pending_held_executions,
            "terminal_held_executions": self.terminal_held_executions,
            "execution_records": self.execution_records,
        }


@dataclass(frozen=True)
class PlannedApiRepair:
    """Validated in-place identity repair for one ``registry.apis`` row."""

    api_id: str
    before_vendor: str
    before_name: str
    before_version: str
    before_catalog_api_id: str | None
    after_vendor: str
    after_name: str
    after_version: str
    after_catalog_api_id: str | None
    shape: str
    references: ApiReferenceCounts = field(default_factory=ApiReferenceCounts)

    def before_dict(self) -> dict[str, str | None]:
        return {
            "vendor": self.before_vendor,
            "name": self.before_name,
            "version": self.before_version,
            "catalog_api_id": self.before_catalog_api_id,
        }

    def after_dict(self) -> dict[str, str | None]:
        return {
            "vendor": self.after_vendor,
            "name": self.after_name,
            "version": self.after_version,
            "catalog_api_id": self.after_catalog_api_id,
        }


@dataclass(frozen=True)
class PlannedConnectSessionRepair:
    """Validated in-place identity repair for an open ``api``-target connect session."""

    session_id: str
    credential_id: str
    before_vendor: str
    before_name: str | None
    before_version: str | None
    after_vendor: str
    after_name: str | None
    after_version: str | None

    def before_dict(self) -> dict[str, str | None]:
        return {
            "vendor": self.before_vendor,
            "api_name": self.before_name,
            "api_version": self.before_version,
        }

    def after_dict(self) -> dict[str, str | None]:
        return {
            "vendor": self.after_vendor,
            "api_name": self.after_name,
            "api_version": self.after_version,
        }


@dataclass(frozen=True)
class PlannedCredentialRepair:
    """Validated in-place identity repair for one ``control.credentials`` row."""

    credential_id: str
    credential_name: str
    created_by: str | None
    before_vendor: str
    before_name: str | None
    before_version: str | None
    before_catalog_api_id: str | None
    after_vendor: str
    after_name: str | None
    after_version: str | None
    after_catalog_api_id: str | None
    shape: str
    references: CredentialReferenceCounts = field(default_factory=CredentialReferenceCounts)
    connect_sessions: tuple[PlannedConnectSessionRepair, ...] = ()

    def before_dict(self) -> dict[str, str | None]:
        return {
            "api_vendor": self.before_vendor,
            "api_name": self.before_name,
            "api_version": self.before_version,
            "catalog_api_id": self.before_catalog_api_id,
        }

    def after_dict(self) -> dict[str, str | None]:
        return {
            "api_vendor": self.after_vendor,
            "api_name": self.after_name,
            "api_version": self.after_version,
            "catalog_api_id": self.after_catalog_api_id,
        }


@dataclass(frozen=True)
class RepairFinding:
    """One structured inventory/report item emitted by dry-run or apply."""

    category: str  # "repaired" | "would_repair" | "unresolvable" | "conflict"
    surface: str  # "registry" | "control"
    resource_type: str  # "api" | "credential" | "connect_session"
    resource_id: str
    shape: str
    reason: str
    before: dict[str, Any]
    after: dict[str, Any] | None
    references: dict[str, int]
    remediation: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "surface": self.surface,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "shape": self.shape,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
            "references": self.references,
            "remediation": self.remediation,
            **self.detail,
        }

    def format_operator_line(self) -> str:
        """Concise, secret-free diagnostic line naming the row and remediation."""
        before_str = _render_identity_dict(self.before)
        target_part = f" -> {_render_identity_dict(self.after)}" if self.after is not None else ""
        return (
            f"[{self.category}] {self.surface}.{self.resource_type} {self.resource_id} "
            f"({before_str}{target_part}) [{self.reason}]: {self.remediation}"
        )


@dataclass(frozen=True)
class RepairPlan:
    """Complete preflight plan before any database mutation runs."""

    api_repairs: tuple[PlannedApiRepair, ...] = ()
    credential_repairs: tuple[PlannedCredentialRepair, ...] = ()
    open_session_repairs: tuple[PlannedConnectSessionRepair, ...] = ()
    unresolvable: tuple[RepairFinding, ...] = ()
    conflicts: tuple[RepairFinding, ...] = ()


@dataclass(frozen=True)
class RepairRunResult:
    """Outcome of a ``CredentialIdentityRepairService.run`` invocation."""

    dry_run: bool
    apis_scanned: int
    credentials_scanned: int
    repaired_apis: int
    repaired_credentials: int
    repaired_connect_sessions: int
    unresolvable_count: int
    conflict_count: int
    failed_closed: bool
    findings: tuple[RepairFinding, ...]
    backup: dict[str, Any]


@dataclass
class RestoreOutcome:
    """Row counts produced by restoring a repair backup document."""

    dry_run: bool
    restored_apis: int = 0
    restored_credentials: int = 0
    restored_connect_sessions: int = 0
    already_restored: int = 0


def _render_identity_dict(data: Mapping[str, Any]) -> str:
    vendor = data.get("vendor") or data.get("api_vendor") or "*"
    name = data.get("name") if "name" in data else data.get("api_name")
    version = data.get("version") if "version" in data else data.get("api_version")
    return f"{vendor}/{name or '*'}/{version or '*'}"


def build_backup_document(
    *,
    tool_version: str,
    apis: Sequence[PlannedApiRepair],
    credentials: Sequence[PlannedCredentialRepair],
    standalone_sessions: Sequence[PlannedConnectSessionRepair] = (),
    created_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Serialize planned or applied mutations into a reversible JSON document."""
    timestamp = (created_at or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).isoformat()
    api_rows = [
        {
            "id": item.api_id,
            "before": item.before_dict(),
            "after": item.after_dict(),
        }
        for item in apis
    ]
    cred_rows = [
        {
            "id": item.credential_id,
            "before": item.before_dict(),
            "after": item.after_dict(),
        }
        for item in credentials
    ]
    seen_sessions: set[str] = set()
    session_rows: list[dict[str, Any]] = []
    for cred in credentials:
        for cs in cred.connect_sessions:
            if cs.session_id not in seen_sessions:
                seen_sessions.add(cs.session_id)
                session_rows.append(
                    {
                        "id": cs.session_id,
                        "credential_id": cs.credential_id,
                        "before": cs.before_dict(),
                        "after": cs.after_dict(),
                    }
                )
    for cs in standalone_sessions:
        if cs.session_id not in seen_sessions:
            seen_sessions.add(cs.session_id)
            session_rows.append(
                {
                    "id": cs.session_id,
                    "credential_id": cs.credential_id,
                    "before": cs.before_dict(),
                    "after": cs.after_dict(),
                }
            )
    return {
        "format": BACKUP_FORMAT,
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": timestamp,
        "tool_version": tool_version,
        "tables": {
            "apis": {"row_count": len(api_rows), "rows": api_rows},
            "credentials": {"row_count": len(cred_rows), "rows": cred_rows},
            "connect_sessions": {"row_count": len(session_rows), "rows": session_rows},
        },
    }


def validate_backup_document(document: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Validate a backup document and return its per-table row lists."""
    if not isinstance(document, Mapping):
        raise IdentityRepairBackupError("backup document must be a JSON object")
    fmt = document.get("format")
    if fmt != BACKUP_FORMAT:
        raise IdentityRepairBackupError(
            f"unsupported backup format {fmt!r}; expected {BACKUP_FORMAT!r}"
        )
    version = document.get("schema_version")
    if version != BACKUP_SCHEMA_VERSION:
        raise IdentityRepairBackupError(
            f"unsupported schema_version {version!r}; expected {BACKUP_SCHEMA_VERSION}"
        )
    tables = document.get("tables")
    if not isinstance(tables, Mapping):
        raise IdentityRepairBackupError("backup document missing 'tables' object")
    parsed: dict[str, list[dict[str, Any]]] = {}
    required_keys: dict[str, tuple[str, ...]] = {
        "apis": ("vendor", "name", "version", "catalog_api_id"),
        "credentials": ("api_vendor", "api_name", "api_version", "catalog_api_id"),
        "connect_sessions": ("vendor", "api_name", "api_version"),
    }
    for table, keys in required_keys.items():
        section = tables.get(table)
        if not isinstance(section, Mapping):
            raise IdentityRepairBackupError(f"backup document missing table section {table!r}")
        rows = section.get("rows")
        row_count = section.get("row_count")
        if not isinstance(rows, list) or not isinstance(row_count, int) or row_count != len(rows):
            raise IdentityRepairBackupError(
                f"table {table!r}: row_count {row_count!r} does not match rows length"
            )
        validated_rows: list[dict[str, Any]] = []
        for idx, entry in enumerate(rows):
            if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str):
                raise IdentityRepairBackupError(f"table {table!r} row {idx}: missing string 'id'")
            before = entry.get("before")
            after = entry.get("after")
            if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                raise IdentityRepairBackupError(
                    f"table {table!r} row {entry['id']!r}: missing 'before'/'after' objects"
                )
            for label, payload in (("before", before), ("after", after)):
                missing = [k for k in keys if k not in payload]
                if missing:
                    raise IdentityRepairBackupError(
                        f"table {table!r} row {entry['id']!r} {label}: missing keys {missing}"
                    )
            validated_rows.append(dict(entry))
        parsed[table] = validated_rows
    return parsed


def has_candidate_anomalies(
    *,
    apis: Sequence[RegistryApiSnapshot],
    credentials: Sequence[CredentialSnapshot],
    manifest_entries: Sequence[Mapping[str, Any]] = (),
    revision_urls_by_api_id: Mapping[str, Sequence[str]] | None = None,
) -> bool:
    """Fast read-only check: return True when any API or credential row needs analysis."""
    manifest_ids = _collect_manifest_ids(apis, credentials, manifest_entries)
    url_to_catalog_id = _unique_spec_url_map(manifest_entries)
    cred_cat_map = _credential_catalog_id_by_doubled_slug(credentials)
    rev_urls = revision_urls_by_api_id or {}

    for api in apis:
        if (
            _detect_api_doubled_target(
                api,
                manifest_ids=manifest_ids,
                url_to_catalog_id=url_to_catalog_id,
                revision_urls=rev_urls.get(api.id, ()),
                cred_cat_map=cred_cat_map,
            )
            is not None
        ):
            return True

    for cred in credentials:
        canon = canonical_credential_scope(
            vendor=cred.api_vendor, name=cred.api_name, version=cred.api_version
        )
        if (
            cred.api_vendor != canon.vendor
            or cred.api_name != canon.name
            or cred.api_version != canon.version
        ):
            return True
        if (
            "/" in cred.api_vendor
            or (cred.api_name is not None and "/" in cred.api_name)
            or (cred.api_version is not None and "/" in cred.api_version)
        ):
            return True
        if not any(
            credential_covers(canon, vendor=a.vendor, name=a.name, version=a.version) for a in apis
        ) and (cred.api_name is not None or cred.api_version is not None or "-" in canon.vendor):
            return True
    return False


def _collect_manifest_ids(
    apis: Sequence[RegistryApiSnapshot],
    credentials: Sequence[CredentialSnapshot],
    manifest_entries: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for entry in manifest_entries:
        api_id = entry.get("api_id")
        if isinstance(api_id, str) and api_id:
            seen[api_id] = None
    for api in apis:
        if api.catalog_api_id:
            seen[api.catalog_api_id] = None
    for cred in credentials:
        if cred.catalog_api_id:
            seen[cred.catalog_api_id] = None
    return tuple(seen.keys())


def _unique_spec_url_map(manifest_entries: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    counts: dict[str, list[str]] = {}
    for entry in manifest_entries:
        spec_url = entry.get("spec_url")
        api_id = entry.get("api_id")
        if isinstance(spec_url, str) and spec_url and isinstance(api_id, str) and api_id:
            counts.setdefault(spec_url, []).append(api_id)
    return {url: ids[0] for url, ids in counts.items() if len(set(ids)) == 1}


def _credential_catalog_id_by_doubled_slug(
    credentials: Sequence[CredentialSnapshot],
) -> dict[tuple[str, str], str]:
    """Map ``(vendor_slug, doubled_name_slug)`` to unique ``catalog_api_id`` from credentials."""
    candidates: dict[tuple[str, str], set[str]] = {}
    for cred in credentials:
        if not cred.catalog_api_id or "/" not in cred.catalog_api_id:
            continue
        derived_vendor = vendor_from_api_id(cred.catalog_api_id)
        if not derived_vendor:
            continue
        v_slug = slugify_api_field(derived_vendor)
        doubled = slugify_api_field(cred.catalog_api_id)
        candidates.setdefault((v_slug, doubled), set()).add(cred.catalog_api_id)
    return {key: next(iter(vals)) for key, vals in candidates.items() if len(vals) == 1}


def _detect_api_doubled_target(
    api: RegistryApiSnapshot,
    *,
    manifest_ids: Sequence[str],
    url_to_catalog_id: Mapping[str, str],
    revision_urls: Sequence[str],
    cred_cat_map: Mapping[tuple[str, str], str],
) -> tuple[str, str | None, str] | None:
    """Return ``(clean_name, after_catalog_api_id, shape)`` if ``api`` is vendor-doubled."""
    effective_cat_id: str | None = api.catalog_api_id
    if not effective_cat_id:
        matched_cat_ids = {url_to_catalog_id[u] for u in revision_urls if u in url_to_catalog_id}
        if len(matched_cat_ids) == 1:
            effective_cat_id = next(iter(matched_cat_ids))
        elif (api.vendor, api.name) in cred_cat_map:
            effective_cat_id = cred_cat_map[(api.vendor, api.name)]

    if effective_cat_id and "/" in effective_cat_id:
        host, sep, sub = effective_cat_id.partition("/")
        if sep and host and sub:
            seed = catalog_entry_api_name(effective_cat_id, manifest_ids)
            clean_name = slugify_api_field(seed)
            doubled_forms = {
                slugify_api_field(effective_cat_id),
                slugify_api_field(f"{api.vendor}-{clean_name}"),
                slugify_api_field(f"{slugify_api_field(host)}-{clean_name}"),
            }
            if clean_name and clean_name != api.name and api.name in doubled_forms:
                return clean_name, effective_cat_id, "vendor_doubled_catalog_api"

    # Repeated vendor prefix without catalog metadata (e.g. ``acme-com-acme-com-sub``).
    repeated_prefix = f"{api.vendor}-{api.vendor}-"
    if api.vendor and api.name.startswith(repeated_prefix):
        stripped = api.name[len(api.vendor) + 1 :]
        if stripped and stripped != api.name:
            return stripped, api.catalog_api_id, "vendor_doubled_prefix_api"

    return None


def _is_path_noise_token(token: str) -> bool:
    lowered = token.strip().lower()
    if not lowered:
        return True
    if lowered in _SPEC_DIR_TOKENS or lowered in _BRANCH_TOKENS or lowered in _SPEC_FILE_TOKENS:
        return True
    return lowered.endswith(_SPEC_FILE_EXTENSIONS)


def _is_path_derived_version(raw_version: str | None, *, vendor_slug: str) -> bool:
    if raw_version is None:
        return False
    stripped = raw_version.strip()
    if not stripped:
        return False
    if "/" in stripped:
        return True
    lowered = stripped.lower()
    if lowered in _SPEC_DIR_TOKENS or lowered in _BRANCH_TOKENS or lowered in _SPEC_FILE_TOKENS:
        return True
    if lowered.endswith(_SPEC_FILE_EXTENSIONS):
        return True
    if looks_like_domain_segment(stripped):
        return True
    slugged = slugify_api_field(stripped)
    return bool(vendor_slug and slugged == vendor_slug and "." in stripped)


def _is_path_derived_credential(
    cred: CredentialSnapshot,
    canon: CredentialScope,
) -> bool:
    if "/" in cred.api_vendor:
        return True
    if cred.api_name is not None and "/" in cred.api_name:
        return True
    if cred.api_version is not None and "/" in cred.api_version:
        return True
    if canon.name in _SPEC_DIR_TOKENS or canon.name in _BRANCH_TOKENS:
        return True
    if cred.api_name is not None and cred.api_name.strip().lower().endswith(_SPEC_FILE_EXTENSIONS):
        return True
    return _is_path_derived_version(cred.api_version, vendor_slug=canon.vendor)


def _extract_path_hints(
    cred: CredentialSnapshot,
    canon: CredentialScope,
    *,
    manifest_ids: Sequence[str],
) -> tuple[str, list[str], str | None]:
    """Extract ``(resolved_vendor, candidate_name_slugs, version_hint)`` from path segments."""
    raw_segments: list[str] = []
    for raw in (cred.api_vendor, cred.api_name, cred.api_version):
        if raw:
            raw_segments.extend(seg.strip() for seg in raw.split("/") if seg.strip())

    resolved_vendor = canon.vendor
    if resolved_vendor in _SPEC_DIR_TOKENS or "/" in cred.api_vendor:
        for seg in raw_segments:
            if not _is_path_noise_token(seg) and looks_like_domain_segment(seg):
                derived = vendor_from_api_id(seg)
                if derived:
                    resolved_vendor = slugify_api_field(derived)
                    break
        else:
            if cred.catalog_api_id:
                derived = vendor_from_api_id(cred.catalog_api_id)
                if derived:
                    resolved_vendor = slugify_api_field(derived)

    name_hints: list[str] = []
    version_hint: str | None = None

    if cred.catalog_api_id:
        cat_seed = catalog_entry_api_name(cred.catalog_api_id, manifest_ids)
        cat_slug = slugify_api_field(cat_seed)
        if cat_slug:
            name_hints.append(cat_slug)

    # Walk segments from name + version to pull sub-API name and concrete version hints.
    tail_segments: list[str] = []
    for raw in (cred.api_name, cred.api_version):
        if raw:
            tail_segments.extend(seg.strip() for seg in raw.split("/") if seg.strip())

    # If api_vendor itself was ``host/sub``, carry ``sub`` as a name hint.
    if "/" in cred.api_vendor:
        _, _, vendor_sub = cred.api_vendor.partition("/")
        if vendor_sub:
            for seg in vendor_sub.split("/"):
                seg = seg.strip()
                if seg and not _is_path_noise_token(seg) and not looks_like_domain_segment(seg):
                    pass
                if seg and not _is_path_noise_token(seg):
                    tail_segments.insert(0, seg)

    for seg in tail_segments:
        if _is_path_noise_token(seg):
            continue
        if looks_like_domain_segment(seg):
            seg_slug = slugify_api_field(seg)
            if seg_slug and seg_slug not in name_hints:
                name_hints.append(seg_slug)
            if resolved_vendor and seg_slug.startswith(f"{resolved_vendor}-"):
                stripped = seg_slug[len(resolved_vendor) + 1 :]
                if stripped and stripped not in name_hints:
                    name_hints.append(stripped)
            continue
        # Non-domain, non-noise segment: check whether it is a concrete version vs name slug.
        if _SEMVER_PREFIX_RE.match(seg):
            version_hint = seg
            continue
        seg_slug = slugify_api_field(seg)
        if seg_slug and seg_slug not in name_hints:
            name_hints.append(seg_slug)
        if resolved_vendor and seg_slug.startswith(f"{resolved_vendor}-"):
            stripped = seg_slug[len(resolved_vendor) + 1 :]
            if stripped and stripped not in name_hints:
                name_hints.append(stripped)

    return resolved_vendor, name_hints, version_hint


def build_repair_plan(
    *,
    apis: Sequence[RegistryApiSnapshot],
    credentials: Sequence[CredentialSnapshot],
    connect_sessions: Sequence[ConnectSessionSnapshot] = (),
    manifest_entries: Sequence[Mapping[str, Any]] = (),
    revision_urls_by_api_id: Mapping[str, Sequence[str]] | None = None,
    api_ref_counts: Mapping[str, ApiReferenceCounts] | None = None,
    credential_ref_counts: Mapping[str, CredentialReferenceCounts] | None = None,
    active_bound_agents_by_credential: Mapping[str, frozenset[str]] | None = None,
) -> RepairPlan:
    """Compute the coordinated cross-DB repair plan for registry and control rows.

    Enforces fail-closed semantics on every collision or ambiguity:
    - If a clean ``registry.apis`` row already exists for a doubled API's target
      ``(vendor, name, version)`` (or two doubled rows map to the same target, or
      in-flight pending held executions block the rename), the API and every
      credential coupled to ``(vendor, old_name)`` emit ``conflict`` findings
      and are left untouched.
    - If a malformed credential maps to multiple candidate registered APIs, or
      collides with an existing or planned credential on the same owner/name/agent
      binding scope, it emits a ``conflict`` finding and is left untouched
      (along with any coupled registry API rename in the same group).
    - If a malformed credential has zero matching registered APIs (and cannot be
      uniquely resolved without guessing a concrete version), it emits an
      ``unresolvable`` finding and is left untouched.
    """
    manifest_ids = _collect_manifest_ids(apis, credentials, manifest_entries)
    url_to_catalog_id = _unique_spec_url_map(manifest_entries)
    cred_cat_map = _credential_catalog_id_by_doubled_slug(credentials)
    rev_urls = revision_urls_by_api_id or {}
    api_refs = api_ref_counts or {}
    cred_refs = credential_ref_counts or {}
    bound_agents = active_bound_agents_by_credential or {}

    conflicts: list[RepairFinding] = []
    unresolvable: list[RepairFinding] = []

    # ── Phase 1: Registry API vendor-doubled candidates (#1079) ──────────────
    raw_api_candidates: dict[str, PlannedApiRepair] = {}
    untouched_api_keys: dict[tuple[str, str, str], RegistryApiSnapshot] = {}

    for api in apis:
        detected = _detect_api_doubled_target(
            api,
            manifest_ids=manifest_ids,
            url_to_catalog_id=url_to_catalog_id,
            revision_urls=rev_urls.get(api.id, ()),
            cred_cat_map=cred_cat_map,
        )
        if detected is None:
            untouched_api_keys[(api.vendor, api.name, api.version)] = api
            continue
        clean_name, after_cat_id, shape = detected
        raw_api_candidates[api.id] = PlannedApiRepair(
            api_id=api.id,
            before_vendor=api.vendor,
            before_name=api.name,
            before_version=api.version,
            before_catalog_api_id=api.catalog_api_id,
            after_vendor=api.vendor,
            after_name=clean_name,
            after_version=api.version,
            after_catalog_api_id=after_cat_id,
            shape=shape,
            references=api_refs.get(api.id, ApiReferenceCounts()),
        )

    by_api_target: dict[tuple[str, str, str], list[PlannedApiRepair]] = {}
    for candidate in raw_api_candidates.values():
        by_api_target.setdefault(
            (candidate.after_vendor, candidate.after_name, candidate.after_version), []
        ).append(candidate)

    blocked_api_pairs: dict[tuple[str, str], str] = {}
    safe_api_repairs: dict[str, PlannedApiRepair] = {}

    for target_key, group in by_api_target.items():
        existing_clean = untouched_api_keys.get(target_key)
        if existing_clean is not None:
            for item in group:
                blocked_api_pairs[(item.before_vendor, item.before_name)] = existing_clean.id
                conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="registry",
                        resource_type="api",
                        resource_id=item.api_id,
                        shape=item.shape,
                        reason="target_api_already_exists",
                        before=item.before_dict(),
                        after=item.after_dict(),
                        references=item.references.as_dict(),
                        detail={"colliding_api_id": existing_clean.id},
                        remediation=(
                            f"Clean API {existing_clean.id} "
                            f"('{target_key[0]}/{target_key[1]}/{target_key[2]}') already exists "
                            f"alongside doubled API {item.api_id}. Inspect revisions/overlays on "
                            "both rows, delete the stale duplicate API, and re-run repair."
                        ),
                    )
                )
            continue
        if len(group) > 1:
            ids_csv = ", ".join(sorted(i.api_id for i in group))
            for item in group:
                blocked_api_pairs[(item.before_vendor, item.before_name)] = ids_csv
                conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="registry",
                        resource_type="api",
                        resource_id=item.api_id,
                        shape=item.shape,
                        reason="duplicate_target_api",
                        before=item.before_dict(),
                        after=item.after_dict(),
                        references=item.references.as_dict(),
                        detail={"colliding_api_ids": sorted(i.api_id for i in group)},
                        remediation=(
                            f"Multiple malformed API rows ({ids_csv}) map to "
                            f"'{target_key[0]}/{target_key[1]}/{target_key[2]}'. Consolidate to "
                            "one API row and re-run repair."
                        ),
                    )
                )
            continue
        sole = group[0]
        if sole.references.pending_held_executions > 0:
            blocked_api_pairs[(sole.before_vendor, sole.before_name)] = sole.api_id
            conflicts.append(
                RepairFinding(
                    category="conflict",
                    surface="registry",
                    resource_type="api",
                    resource_id=sole.api_id,
                    shape=sole.shape,
                    reason="pending_held_execution_in_flight",
                    before=sole.before_dict(),
                    after=sole.after_dict(),
                    references=sole.references.as_dict(),
                    remediation=(
                        f"API {sole.api_id} has {sole.references.pending_held_executions} "
                        "pending held execution(s) in flight. Approve, deny, or let them "
                        "expire before renaming the API identity."
                    ),
                )
            )
            continue
        safe_api_repairs[sole.api_id] = sole

    # Build the post-Phase-1 view of clean registry APIs for credential resolution.
    renamed_pair_map: dict[tuple[str, str], str] = {
        (r.before_vendor, r.before_name): r.after_name for r in safe_api_repairs.values()
    }
    effective_apis: list[RegistryApiSnapshot] = []
    for api in apis:
        if api.id in safe_api_repairs:
            rep = safe_api_repairs[api.id]
            effective_apis.append(
                RegistryApiSnapshot(
                    id=api.id,
                    vendor=rep.after_vendor,
                    name=rep.after_name,
                    version=rep.after_version,
                    catalog_api_id=rep.after_catalog_api_id,
                )
            )
        elif (api.vendor, api.name) not in blocked_api_pairs:
            effective_apis.append(api)

    # Index catalog manifest entries by vendor slug for zero-import catalog hints.
    manifest_by_vendor: dict[str, list[tuple[str, str]]] = {}
    for api_id in manifest_ids:
        v = vendor_from_api_id(api_id)
        if not v:
            continue
        v_slug = slugify_api_field(v)
        n_slug = slugify_api_field(catalog_entry_api_name(api_id, manifest_ids))
        if v_slug and n_slug:
            manifest_by_vendor.setdefault(v_slug, []).append((api_id, n_slug))

    # ── Phase 2: Control credential candidates (#1079 + #1388) ───────────────
    tentative_cred_repairs: dict[str, PlannedCredentialRepair] = {}
    untouched_creds: dict[str, CredentialSnapshot] = {}
    # Track which API rename pair ``(vendor, old_name)`` a credential depends on so
    # a credential-side collision rolls back that API rename in the plan too.
    cred_coupled_api_pair: dict[str, tuple[str, str]] = {}

    for cred in credentials:
        c_refs = cred_refs.get(cred.id, CredentialReferenceCounts())
        canon = canonical_credential_scope(
            vendor=cred.api_vendor, name=cred.api_name, version=cred.api_version
        )
        before_payload = {
            "api_vendor": cred.api_vendor,
            "api_name": cred.api_name,
            "api_version": cred.api_version,
            "catalog_api_id": cred.catalog_api_id,
        }

        # Check if coupled to a blocked Registry API rename.
        if canon.name is not None and (canon.vendor, canon.name) in blocked_api_pairs:
            conflicts.append(
                RepairFinding(
                    category="conflict",
                    surface="control",
                    resource_type="credential",
                    resource_id=cred.id,
                    shape="vendor_doubled_credential",
                    reason="registry_api_collision",
                    before=before_payload,
                    after=None,
                    references=c_refs.as_dict(),
                    detail={"credential_name": cred.name},
                    remediation=(
                        f"Credential {cred.id} ({cred.name!r}) targets doubled API "
                        f"'{canon.vendor}/{canon.name}', whose registry rename failed closed "
                        "because the clean API row already exists. Resolve the duplicate API "
                        "and duplicate credentials, then re-run repair."
                    ),
                )
            )
            untouched_creds[cred.id] = cred
            continue

        has_slash = (
            "/" in cred.api_vendor
            or (cred.api_name is not None and "/" in cred.api_name)
            or (cred.api_version is not None and "/" in cred.api_version)
        )
        needs_canon = (
            cred.api_vendor != canon.vendor
            or cred.api_name != canon.name
            or cred.api_version != canon.version
        )
        covers_effective = any(
            credential_covers(canon, vendor=a.vendor, name=a.name, version=a.version)
            for a in effective_apis
        )

        # Already covers a clean effective API and contains no path separator.
        if not has_slash and covers_effective:
            # Make sure it was not only covering via wildcard while carrying a doubled vendor.
            if needs_canon:
                tentative_cred_repairs[cred.id] = PlannedCredentialRepair(
                    credential_id=cred.id,
                    credential_name=cred.name,
                    created_by=cred.created_by,
                    before_vendor=cred.api_vendor,
                    before_name=cred.api_name,
                    before_version=cred.api_version,
                    before_catalog_api_id=cred.catalog_api_id,
                    after_vendor=canon.vendor,
                    after_name=canon.name,
                    after_version=canon.version,
                    after_catalog_api_id=cred.catalog_api_id,
                    shape="non_canonical_scope",
                    references=c_refs,
                )
            else:
                untouched_creds[cred.id] = cred
            continue

        # Vendor-only wildcard with canonical vendor (and not a slugged domain/sub vendor).
        if not has_slash and canon.name is None and canon.version is None:
            vendor_repair = _resolve_doubled_vendor_only(
                cred, canon, effective_apis=effective_apis, manifest_ids=manifest_ids
            )
            if vendor_repair is None:
                if needs_canon:
                    tentative_cred_repairs[cred.id] = PlannedCredentialRepair(
                        credential_id=cred.id,
                        credential_name=cred.name,
                        created_by=cred.created_by,
                        before_vendor=cred.api_vendor,
                        before_name=cred.api_name,
                        before_version=cred.api_version,
                        before_catalog_api_id=cred.catalog_api_id,
                        after_vendor=canon.vendor,
                        after_name=None,
                        after_version=None,
                        after_catalog_api_id=cred.catalog_api_id,
                        shape="non_canonical_scope",
                        references=c_refs,
                    )
                else:
                    untouched_creds[cred.id] = cred
                continue
            target_scope, shape = vendor_repair
            tentative_cred_repairs[cred.id] = PlannedCredentialRepair(
                credential_id=cred.id,
                credential_name=cred.name,
                created_by=cred.created_by,
                before_vendor=cred.api_vendor,
                before_name=cred.api_name,
                before_version=cred.api_version,
                before_catalog_api_id=cred.catalog_api_id,
                after_vendor=target_scope.vendor,
                after_name=target_scope.name,
                after_version=target_scope.version,
                after_catalog_api_id=cred.catalog_api_id,
                shape=shape,
                references=c_refs,
            )
            continue

        # Check M1: coupled to a safe Registry API rename or vendor-doubled name (#1079).
        doubled_outcome = _resolve_vendor_doubled_credential(
            cred,
            canon,
            renamed_pair_map=renamed_pair_map,
            effective_apis=effective_apis,
            manifest_ids=manifest_ids,
        )
        if doubled_outcome is not None and not _is_path_derived_version(
            cred.api_version, vendor_slug=doubled_outcome[0].vendor
        ):
            target_scope, shape, coupled_pair = doubled_outcome
            if coupled_pair is not None:
                cred_coupled_api_pair[cred.id] = coupled_pair
            tentative_cred_repairs[cred.id] = PlannedCredentialRepair(
                credential_id=cred.id,
                credential_name=cred.name,
                created_by=cred.created_by,
                before_vendor=cred.api_vendor,
                before_name=cred.api_name,
                before_version=cred.api_version,
                before_catalog_api_id=cred.catalog_api_id,
                after_vendor=target_scope.vendor,
                after_name=target_scope.name,
                after_version=target_scope.version,
                after_catalog_api_id=cred.catalog_api_id,
                shape=shape,
                references=c_refs,
            )
            continue

        # Check M2: catalog_api_id points to a registered API under the same vendor.
        if cred.catalog_api_id and canon.name is not None and not has_slash:
            cat_matches = [
                a
                for a in effective_apis
                if a.catalog_api_id == cred.catalog_api_id and a.vendor == canon.vendor
            ]
            if cat_matches and not _is_path_derived_version(
                cred.api_version, vendor_slug=canon.vendor
            ):
                distinct_names = sorted({a.name for a in cat_matches})
                if len(distinct_names) > 1:
                    conflicts.append(
                        RepairFinding(
                            category="conflict",
                            surface="control",
                            resource_type="credential",
                            resource_id=cred.id,
                            shape="catalog_mismatched_name",
                            reason="multiple_candidate_apis",
                            before=before_payload,
                            after=None,
                            references=c_refs.as_dict(),
                            detail={
                                "credential_name": cred.name,
                                "candidates": [
                                    f"{a.vendor}/{a.name}/{a.version}" for a in cat_matches
                                ],
                            },
                            remediation=(
                                f"Credential {cred.id} ({cred.name!r}) with catalog_api_id "
                                f"{cred.catalog_api_id!r} matches multiple registered API names "
                                f"({', '.join(distinct_names)}). Re-create the credential "
                                "targeting the intended API identity."
                            ),
                        )
                    )
                    untouched_creds[cred.id] = cred
                    continue
                reg_name = distinct_names[0]
                if canon.version is None or any(a.version == canon.version for a in cat_matches):
                    tentative_cred_repairs[cred.id] = PlannedCredentialRepair(
                        credential_id=cred.id,
                        credential_name=cred.name,
                        created_by=cred.created_by,
                        before_vendor=cred.api_vendor,
                        before_name=cred.api_name,
                        before_version=cred.api_version,
                        before_catalog_api_id=cred.catalog_api_id,
                        after_vendor=canon.vendor,
                        after_name=reg_name,
                        after_version=canon.version,
                        after_catalog_api_id=cred.catalog_api_id,
                        shape="catalog_mismatched_name",
                        references=c_refs,
                    )
                    continue

        # Check M3: path-derived api_name / api_version (#1388).
        if _is_path_derived_credential(cred, canon):
            decision = _resolve_path_derived_credential(
                cred,
                canon,
                effective_apis=effective_apis,
                manifest_ids=manifest_ids,
                manifest_by_vendor=manifest_by_vendor,
                references=c_refs,
            )
            if isinstance(decision, PlannedCredentialRepair):
                tentative_cred_repairs[cred.id] = decision
            elif decision.category == "conflict":
                conflicts.append(decision)
                untouched_creds[cred.id] = cred
            else:
                unresolvable.append(decision)
                untouched_creds[cred.id] = cred
            continue

        # Clean pre-import credential (not vendor-doubled, not path-derived).
        if needs_canon:
            tentative_cred_repairs[cred.id] = PlannedCredentialRepair(
                credential_id=cred.id,
                credential_name=cred.name,
                created_by=cred.created_by,
                before_vendor=cred.api_vendor,
                before_name=cred.api_name,
                before_version=cred.api_version,
                before_catalog_api_id=cred.catalog_api_id,
                after_vendor=canon.vendor,
                after_name=canon.name,
                after_version=canon.version,
                after_catalog_api_id=cred.catalog_api_id,
                shape="non_canonical_scope",
                references=c_refs,
            )
        else:
            untouched_creds[cred.id] = cred

    # ── Phase 3: Credential & binding collision checks (fail closed) ─────────
    safe_cred_repairs = _filter_credential_collisions(
        tentative_cred_repairs=tentative_cred_repairs,
        untouched_creds=untouched_creds,
        bound_agents=bound_agents,
        cred_coupled_api_pair=cred_coupled_api_pair,
        safe_api_repairs=safe_api_repairs,
        conflicts=conflicts,
    )

    # ── Phase 4: Open api-target connect_sessions coupled to repairs ─────────
    safe_cred_repairs, standalone_sessions = _plan_connect_session_repairs(
        connect_sessions=connect_sessions,
        safe_api_repairs=safe_api_repairs,
        safe_cred_repairs=safe_cred_repairs,
        cred_coupled_api_pair=cred_coupled_api_pair,
        conflicts=conflicts,
    )

    ordered_apis = tuple(sorted(safe_api_repairs.values(), key=lambda r: r.api_id))
    ordered_creds = tuple(sorted(safe_cred_repairs.values(), key=lambda r: r.credential_id))
    ordered_sessions = tuple(sorted(standalone_sessions, key=lambda r: r.session_id))
    return RepairPlan(
        api_repairs=ordered_apis,
        credential_repairs=ordered_creds,
        open_session_repairs=ordered_sessions,
        unresolvable=tuple(unresolvable),
        conflicts=tuple(conflicts),
    )


def _resolve_doubled_vendor_only(
    cred: CredentialSnapshot,
    canon: CredentialScope,
    *,
    effective_apis: Sequence[RegistryApiSnapshot],
    manifest_ids: Sequence[str],
) -> tuple[CredentialScope, str] | None:
    """Detect a vendor-only credential whose ``api_vendor`` is itself doubled."""
    if any(a.vendor == canon.vendor for a in effective_apis):
        return None

    if cred.catalog_api_id and "/" in cred.catalog_api_id:
        derived = vendor_from_api_id(cred.catalog_api_id)
        if derived:
            v_slug = slugify_api_field(derived)
            n_slug = slugify_api_field(catalog_entry_api_name(cred.catalog_api_id, manifest_ids))
            if canon.vendor == slugify_api_field(cred.catalog_api_id) and v_slug != canon.vendor:
                return CredentialScope(vendor=v_slug, name=n_slug, version=None), (
                    "vendor_contains_catalog_path"
                )

    matches: set[tuple[str, str | None]] = set()
    for api in effective_apis:
        if canon.vendor == slugify_api_field(f"{api.vendor}-{api.name}") or (
            api.catalog_api_id and canon.vendor == slugify_api_field(api.catalog_api_id)
        ):
            matches.add((api.vendor, api.name))
        elif canon.vendor == f"{api.vendor}-{api.vendor}":
            matches.add((api.vendor, None))
    if len(matches) == 1:
        matched_vendor, matched_name = next(iter(matches))
        return CredentialScope(vendor=matched_vendor, name=matched_name, version=None), (
            "vendor_doubled_vendor_axis"
        )
    return None


def _resolve_vendor_doubled_credential(
    cred: CredentialSnapshot,
    canon: CredentialScope,
    *,
    renamed_pair_map: Mapping[tuple[str, str], str],
    effective_apis: Sequence[RegistryApiSnapshot],
    manifest_ids: Sequence[str],
) -> tuple[CredentialScope, str, tuple[str, str] | None] | None:
    """Return ``(target_scope, shape, coupled_api_pair)`` if ``cred`` is vendor-doubled."""
    if canon.name is None:
        return None

    # 1. Exact match with a Registry API renamed in Phase 1.
    if (canon.vendor, canon.name) in renamed_pair_map:
        clean_name = renamed_pair_map[(canon.vendor, canon.name)]
        return (
            CredentialScope(vendor=canon.vendor, name=clean_name, version=canon.version),
            "vendor_doubled_credential",
            (canon.vendor, canon.name),
        )

    # 2. Explicit catalog_api_id with domain/sub shape.
    if cred.catalog_api_id and "/" in cred.catalog_api_id:
        host, sep, sub = cred.catalog_api_id.partition("/")
        if sep and host and sub:
            derived_v = vendor_from_api_id(cred.catalog_api_id)
            cat_vendor = slugify_api_field(derived_v or host)
            clean_name = slugify_api_field(
                catalog_entry_api_name(cred.catalog_api_id, manifest_ids)
            )
            doubled_forms = {
                slugify_api_field(cred.catalog_api_id),
                slugify_api_field(f"{cat_vendor}-{clean_name}"),
                slugify_api_field(f"{canon.vendor}-{clean_name}"),
                slugify_api_field(f"{slugify_api_field(host)}-{clean_name}"),
            }
            target_vendor = (
                cat_vendor
                if canon.vendor
                in {
                    cat_vendor,
                    slugify_api_field(host),
                    slugify_api_field(cred.catalog_api_id),
                }
                else canon.vendor
            )
            if clean_name and clean_name != canon.name and canon.name in doubled_forms:
                return (
                    CredentialScope(vendor=target_vendor, name=clean_name, version=canon.version),
                    "vendor_doubled_credential",
                    None,
                )

    # 3. api_vendor itself carries ``domain/sub`` while api_name repeats sub or doubled slug.
    if "/" not in cred.api_vendor:
        for api in effective_apis:
            if canon.vendor == slugify_api_field(f"{api.vendor}-{api.name}") and canon.name in {
                api.name,
                canon.vendor,
            }:
                return (
                    CredentialScope(vendor=api.vendor, name=api.name, version=canon.version),
                    "vendor_doubled_vendor_axis",
                    None,
                )

    # 4. Doubled vendor prefix on api_name without catalog_api_id.
    prefix = f"{canon.vendor}-"
    if canon.vendor and canon.name.startswith(prefix):
        stripped = canon.name[len(prefix) :]
        if stripped and stripped != canon.name:
            # Provable via an existing clean API, catalog manifest entry, or domain-slug vendor.
            matches_clean_api = any(
                a.vendor == canon.vendor
                and a.name == stripped
                and (canon.version is None or a.version == canon.version)
                for a in effective_apis
            )
            matches_manifest = any(
                slugify_api_field(vendor_from_api_id(m) or "") == canon.vendor
                and slugify_api_field(m) == canon.name
                and slugify_api_field(catalog_entry_api_name(m, manifest_ids)) == stripped
                for m in manifest_ids
                if "/" in m
            )
            repeated_twice = canon.name.startswith(f"{canon.vendor}-{canon.vendor}-")
            domain_vendor_no_apis = "-" in canon.vendor and not any(
                a.vendor == canon.vendor for a in effective_apis
            )
            if matches_clean_api or matches_manifest or repeated_twice or domain_vendor_no_apis:
                return (
                    CredentialScope(vendor=canon.vendor, name=stripped, version=canon.version),
                    "vendor_doubled_credential",
                    None,
                )

    return None


def _resolve_path_derived_credential(
    cred: CredentialSnapshot,
    canon: CredentialScope,
    *,
    effective_apis: Sequence[RegistryApiSnapshot],
    manifest_ids: Sequence[str],
    manifest_by_vendor: Mapping[str, Sequence[tuple[str, str]]],
    references: CredentialReferenceCounts,
) -> PlannedCredentialRepair | RepairFinding:
    """Resolve a path-derived credential (#1388) or return an unresolvable/conflict finding."""
    resolved_vendor, name_hints, version_hint = _extract_path_hints(
        cred, canon, manifest_ids=manifest_ids
    )
    before_payload = {
        "api_vendor": cred.api_vendor,
        "api_name": cred.api_name,
        "api_version": cred.api_version,
        "catalog_api_id": cred.catalog_api_id,
    }
    raw_version_present = bool(cred.api_version and cred.api_version.strip())

    vendor_apis = [a for a in effective_apis if a.vendor == resolved_vendor]
    candidates = list(vendor_apis)

    if cred.catalog_api_id:
        by_cat = [a for a in candidates if a.catalog_api_id == cred.catalog_api_id]
        if by_cat:
            candidates = by_cat

    if name_hints:
        by_name = [a for a in candidates if a.name in name_hints]
        if by_name:
            candidates = by_name
        elif name_hints == [resolved_vendor]:
            # A bare vendor domain token (e.g. ``github.com``) does not narrow sub-APIs
            # when no bare-domain API exists; keep ``candidates`` so 1 API resolves and
            # >1 APIs fail closed as ambiguous.
            pass
        else:
            candidates = []

    if version_hint is not None:
        candidates = [a for a in candidates if a.version == version_hint]

    # Single concrete API match in registry_db.
    if len(candidates) == 1:
        winner = candidates[0]
        target_version = winner.version if raw_version_present else None
        return PlannedCredentialRepair(
            credential_id=cred.id,
            credential_name=cred.name,
            created_by=cred.created_by,
            before_vendor=cred.api_vendor,
            before_name=cred.api_name,
            before_version=cred.api_version,
            before_catalog_api_id=cred.catalog_api_id,
            after_vendor=winner.vendor,
            after_name=winner.name,
            after_version=target_version,
            after_catalog_api_id=cred.catalog_api_id,
            shape="path_derived_credential",
            references=references,
        )

    # Multiple candidates: safe only when version was originally wildcard and all
    # candidates share one (vendor, name).
    if len(candidates) > 1:
        distinct_pairs = sorted({(a.vendor, a.name) for a in candidates})
        if not raw_version_present and len(distinct_pairs) == 1:
            v_slug, n_slug = distinct_pairs[0]
            return PlannedCredentialRepair(
                credential_id=cred.id,
                credential_name=cred.name,
                created_by=cred.created_by,
                before_vendor=cred.api_vendor,
                before_name=cred.api_name,
                before_version=cred.api_version,
                before_catalog_api_id=cred.catalog_api_id,
                after_vendor=v_slug,
                after_name=n_slug,
                after_version=None,
                after_catalog_api_id=cred.catalog_api_id,
                shape="path_derived_credential",
                references=references,
            )
        cand_labels = sorted({f"{a.vendor}/{a.name}/{a.version}" for a in candidates})
        return RepairFinding(
            category="conflict",
            surface="control",
            resource_type="credential",
            resource_id=cred.id,
            shape="path_derived_credential",
            reason="multiple_candidate_apis",
            before=before_payload,
            after=None,
            references=references.as_dict(),
            detail={
                "credential_name": cred.name,
                "candidates": cand_labels,
            },
            remediation=(
                f"Credential {cred.id} ({cred.name!r}) with path-derived identity "
                f"'{_render_identity_dict(before_payload)}' matches multiple registered APIs: "
                f"{', '.join(cand_labels)}. Re-create the credential targeting the single "
                "intended API version and move its agent bindings."
            ),
        )

    # Zero registered APIs matched: check if catalog manifest uniquely resolves a
    # version-unscoped credential; otherwise report unresolvable without mutating.
    catalog_candidates = list(manifest_by_vendor.get(resolved_vendor, ()))
    if name_hints:
        catalog_candidates = [
            (api_id, n_slug) for api_id, n_slug in catalog_candidates if n_slug in name_hints
        ]
    distinct_cat_names = sorted({n_slug for _, n_slug in catalog_candidates})
    if not raw_version_present and len(distinct_cat_names) == 1:
        return PlannedCredentialRepair(
            credential_id=cred.id,
            credential_name=cred.name,
            created_by=cred.created_by,
            before_vendor=cred.api_vendor,
            before_name=cred.api_name,
            before_version=cred.api_version,
            before_catalog_api_id=cred.catalog_api_id,
            after_vendor=resolved_vendor,
            after_name=distinct_cat_names[0],
            after_version=None,
            after_catalog_api_id=cred.catalog_api_id,
            shape="path_derived_credential",
            references=references,
        )

    catalog_hint = ""
    if catalog_candidates:
        cat_ids = sorted({api_id for api_id, _ in catalog_candidates})
        catalog_hint = f" Import catalog entry {cat_ids[0]!r} first and re-run repair, or"
    sibling_hint = ""
    if vendor_apis:
        listed = ", ".join(sorted({f"{a.vendor}/{a.name}/{a.version}" for a in vendor_apis})[:5])
        sibling_hint = f" Registered APIs under '{resolved_vendor}': {listed}."

    return RepairFinding(
        category="unresolvable",
        surface="control",
        resource_type="credential",
        resource_id=cred.id,
        shape="path_derived_credential",
        reason="no_matching_registered_api",
        before=before_payload,
        after=None,
        references=references.as_dict(),
        detail={
            "credential_name": cred.name,
            "resolved_vendor": resolved_vendor,
            "name_hints": name_hints,
        },
        remediation=(
            f"Credential {cred.id} ({cred.name!r}) stores path-derived identity "
            f"'{_render_identity_dict(before_payload)}', and no unique registered API "
            f"mapping exists in registry_db.{catalog_hint} delete and re-create the "
            f"credential with the target API identity.{sibling_hint}"
        ),
    )


def _scopes_overlap_same_name(a: CredentialScope, b: CredentialScope) -> bool:
    if a.vendor != b.vendor:
        return False
    name_overlap = a.name is None or b.name is None or a.name == b.name
    ver_overlap = a.version is None or b.version is None or a.version == b.version
    return name_overlap and ver_overlap


def _filter_credential_collisions(
    *,
    tentative_cred_repairs: Mapping[str, PlannedCredentialRepair],
    untouched_creds: Mapping[str, CredentialSnapshot],
    bound_agents: Mapping[str, frozenset[str]],
    cred_coupled_api_pair: Mapping[str, tuple[str, str]],
    safe_api_repairs: dict[str, PlannedApiRepair],
    conflicts: list[RepairFinding],
) -> dict[str, PlannedCredentialRepair]:
    """Reject any credential repair that collides with an existing or sibling row."""
    blocked_cred_ids: set[str] = set()

    # Check pending held executions first.
    for cid, plan in tentative_cred_repairs.items():
        if plan.references.pending_held_executions > 0:
            blocked_cred_ids.add(cid)
            conflicts.append(
                RepairFinding(
                    category="conflict",
                    surface="control",
                    resource_type="credential",
                    resource_id=cid,
                    shape=plan.shape,
                    reason="pending_held_execution_in_flight",
                    before=plan.before_dict(),
                    after=plan.after_dict(),
                    references=plan.references.as_dict(),
                    detail={"credential_name": plan.credential_name},
                    remediation=(
                        f"Credential {cid} ({plan.credential_name!r}) has "
                        f"{plan.references.pending_held_executions} pending held execution(s) "
                        "in flight. Decide or expire them before rewriting credential scope."
                    ),
                )
            )

    # Precompute clean untouched scopes.
    clean_entries: list[tuple[CredentialSnapshot, CredentialScope]] = [
        (
            u,
            canonical_credential_scope(vendor=u.api_vendor, name=u.api_name, version=u.api_version),
        )
        for u in untouched_creds.values()
    ]

    # Check each tentative credential against untouched clean rows.
    for cid, plan in tentative_cred_repairs.items():
        if cid in blocked_cred_ids:
            continue
        target_scope = CredentialScope(
            vendor=plan.after_vendor, name=plan.after_name, version=plan.after_version
        )
        my_agents = bound_agents.get(cid, frozenset())

        for other, other_scope in clean_entries:
            same_exact_scope = other_scope == target_scope
            same_owner = plan.created_by == other.created_by
            shared_agents = sorted(my_agents & bound_agents.get(other.id, frozenset()))

            reason: str | None = None
            detail_extra: dict[str, Any] = {"colliding_credential_id": other.id}
            if same_owner and same_exact_scope:
                reason = "target_credential_already_exists"
            elif (
                same_owner
                and plan.credential_name == other.name
                and _scopes_overlap_same_name(target_scope, other_scope)
            ):
                reason = "credential_name_collision"
            elif shared_agents and same_exact_scope:
                reason = "agent_binding_scope_collision"
                detail_extra["shared_agent_ids"] = shared_agents

            if reason is not None:
                blocked_cred_ids.add(cid)
                conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="control",
                        resource_type="credential",
                        resource_id=cid,
                        shape=plan.shape,
                        reason=reason,
                        before=plan.before_dict(),
                        after=plan.after_dict(),
                        references=plan.references.as_dict(),
                        detail={"credential_name": plan.credential_name, **detail_extra},
                        remediation=(
                            f"Credential {cid} ({plan.credential_name!r}) cannot be rewritten to "
                            f"'{_render_identity_dict(plan.after_dict())}' because credential "
                            f"{other.id} ({other.name!r}) already occupies that target scope/name. "
                            "Consolidate agent bindings onto the clean credential, delete the "
                            "duplicate row, and re-run repair."
                        ),
                    )
                )
                break

    # Check pairwise collisions among remaining tentative repairs.
    remaining_ids = [cid for cid in sorted(tentative_cred_repairs) if cid not in blocked_cred_ids]
    for i, cid_a in enumerate(remaining_ids):
        plan_a = tentative_cred_repairs[cid_a]
        scope_a = CredentialScope(
            vendor=plan_a.after_vendor, name=plan_a.after_name, version=plan_a.after_version
        )
        agents_a = bound_agents.get(cid_a, frozenset())
        for cid_b in remaining_ids[i + 1 :]:
            plan_b = tentative_cred_repairs[cid_b]
            scope_b = CredentialScope(
                vendor=plan_b.after_vendor, name=plan_b.after_name, version=plan_b.after_version
            )
            same_exact = scope_a == scope_b
            same_owner = plan_a.created_by == plan_b.created_by
            shared_agents = sorted(agents_a & bound_agents.get(cid_b, frozenset()))
            if (
                (same_owner and same_exact)
                or (
                    same_owner
                    and plan_a.credential_name == plan_b.credential_name
                    and _scopes_overlap_same_name(scope_a, scope_b)
                )
                or (shared_agents and same_exact)
            ):
                for colliding_id, peer_id, p in (
                    (cid_a, cid_b, plan_a),
                    (cid_b, cid_a, plan_b),
                ):
                    if colliding_id in blocked_cred_ids:
                        continue
                    blocked_cred_ids.add(colliding_id)
                    conflicts.append(
                        RepairFinding(
                            category="conflict",
                            surface="control",
                            resource_type="credential",
                            resource_id=colliding_id,
                            shape=p.shape,
                            reason="duplicate_target_credential",
                            before=p.before_dict(),
                            after=p.after_dict(),
                            references=p.references.as_dict(),
                            detail={
                                "credential_name": p.credential_name,
                                "colliding_credential_id": peer_id,
                            },
                            remediation=(
                                f"Malformed credentials {colliding_id} and {peer_id} both map to "
                                f"'{_render_identity_dict(p.after_dict())}'. Delete the redundant "
                                "credential row and re-run repair."
                            ),
                        )
                    )

    # Propagate any blocked credential back to its coupled Registry API rename group
    # so a working doubled API+credential pair never half-migrates.
    safe_creds = {
        cid: plan for cid, plan in tentative_cred_repairs.items() if cid not in blocked_cred_ids
    }
    _propagate_coupled_blocks(
        blocked_cred_ids=blocked_cred_ids,
        cred_coupled_api_pair=cred_coupled_api_pair,
        safe_api_repairs=safe_api_repairs,
        safe_creds=safe_creds,
        conflicts=conflicts,
    )
    return safe_creds


def _propagate_coupled_blocks(
    *,
    blocked_cred_ids: set[str],
    cred_coupled_api_pair: Mapping[str, tuple[str, str]],
    safe_api_repairs: dict[str, PlannedApiRepair],
    safe_creds: dict[str, PlannedCredentialRepair],
    conflicts: list[RepairFinding],
) -> None:
    blocked_pairs = {
        cred_coupled_api_pair[cid] for cid in blocked_cred_ids if cid in cred_coupled_api_pair
    }
    if not blocked_pairs:
        return
    for api_id, api_plan in list(safe_api_repairs.items()):
        if (api_plan.before_vendor, api_plan.before_name) in blocked_pairs:
            del safe_api_repairs[api_id]
            conflicts.append(
                RepairFinding(
                    category="conflict",
                    surface="registry",
                    resource_type="api",
                    resource_id=api_id,
                    shape=api_plan.shape,
                    reason="dependent_credential_collision",
                    before=api_plan.before_dict(),
                    after=api_plan.after_dict(),
                    references=api_plan.references.as_dict(),
                    remediation=(
                        f"API {api_id} rename to '{api_plan.after_vendor}/{api_plan.after_name}/"
                        f"{api_plan.after_version}' held back because a dependent credential "
                        "collides on the target identity. Resolve the credential collision and "
                        "re-run repair."
                    ),
                )
            )
    for cid, cred_plan in list(safe_creds.items()):
        if cred_coupled_api_pair.get(cid) in blocked_pairs:
            del safe_creds[cid]
            conflicts.append(
                RepairFinding(
                    category="conflict",
                    surface="control",
                    resource_type="credential",
                    resource_id=cid,
                    shape=cred_plan.shape,
                    reason="dependent_credential_collision",
                    before=cred_plan.before_dict(),
                    after=cred_plan.after_dict(),
                    references=cred_plan.references.as_dict(),
                    detail={"credential_name": cred_plan.credential_name},
                    remediation=(
                        f"Credential {cid} ({cred_plan.credential_name!r}) held back because "
                        "a sibling credential on the same API collided during preflight."
                    ),
                )
            )


def _plan_connect_session_repairs(
    *,
    connect_sessions: Sequence[ConnectSessionSnapshot],
    safe_api_repairs: dict[str, PlannedApiRepair],
    safe_cred_repairs: dict[str, PlannedCredentialRepair],
    cred_coupled_api_pair: Mapping[str, tuple[str, str]],
    conflicts: list[RepairFinding],
) -> tuple[dict[str, PlannedCredentialRepair], list[PlannedConnectSessionRepair]]:
    """Attach open ``api``-target connect-session updates and fail closed on collisions."""
    api_rename_by_triple: dict[tuple[str, str | None, str | None], tuple[str, str, str]] = {
        (r.before_vendor, r.before_name, r.before_version): (
            r.after_vendor,
            r.after_name,
            r.after_version,
        )
        for r in safe_api_repairs.values()
    }
    open_dedupe_keys: set[tuple[str, str, str | None, str | None]] = set()
    candidates: list[tuple[ConnectSessionSnapshot, PlannedConnectSessionRepair]] = []

    for cs in connect_sessions:
        if cs.target_kind != "api" or cs.state not in OPEN_CONNECT_SESSION_STATES:
            continue
        new_target: tuple[str, str | None, str | None] | None = None
        if (cs.vendor, cs.api_name, cs.api_version) in api_rename_by_triple:
            new_target = api_rename_by_triple[(cs.vendor, cs.api_name, cs.api_version)]
        elif cs.credential_id in safe_cred_repairs:
            cp = safe_cred_repairs[cs.credential_id]
            if cp.after_name is not None and cp.after_version is not None:
                new_target = (cp.after_vendor, cp.after_name, cp.after_version)

        if new_target is None or new_target == (cs.vendor, cs.api_name, cs.api_version):
            if cs.agent_id is not None and cs.initiator_actor_id == cs.agent_id:
                open_dedupe_keys.add((cs.agent_id, cs.vendor, cs.api_name, cs.api_version))
            continue

        candidates.append(
            (
                cs,
                PlannedConnectSessionRepair(
                    session_id=cs.id,
                    credential_id=cs.credential_id,
                    before_vendor=cs.vendor,
                    before_name=cs.api_name,
                    before_version=cs.api_version,
                    after_vendor=new_target[0],
                    after_name=new_target[1],
                    after_version=new_target[2],
                ),
            )
        )

    by_cred: dict[str, list[PlannedConnectSessionRepair]] = {}
    standalone: list[PlannedConnectSessionRepair] = []
    blocked_cred_ids: set[str] = set()

    for cs, planned in candidates:
        dedupe_key = (
            (cs.agent_id, planned.after_vendor, planned.after_name, planned.after_version)
            if cs.agent_id is not None and cs.initiator_actor_id == cs.agent_id
            else None
        )
        if dedupe_key is not None and dedupe_key in open_dedupe_keys:
            blocked_cred_ids.add(cs.credential_id)
            conflicts.append(
                RepairFinding(
                    category="conflict",
                    surface="control",
                    resource_type="connect_session",
                    resource_id=cs.id,
                    shape="open_api_connect_session",
                    reason="open_connect_session_collision",
                    before=planned.before_dict(),
                    after=planned.after_dict(),
                    references={},
                    detail={"credential_id": cs.credential_id},
                    remediation=(
                        f"Open connect session {cs.id} for credential {cs.credential_id} "
                        "collides with an existing open agent connect session on "
                        f"'{_render_identity_dict(planned.after_dict())}'. Expire or complete "
                        "the open connect session and re-run repair."
                    ),
                )
            )
            continue
        if dedupe_key is not None:
            open_dedupe_keys.add(dedupe_key)
        if cs.credential_id in safe_cred_repairs:
            by_cred.setdefault(cs.credential_id, []).append(planned)
        else:
            standalone.append(planned)

    if blocked_cred_ids:
        for cid in blocked_cred_ids:
            safe_cred_repairs.pop(cid, None)
            by_cred.pop(cid, None)
        _propagate_coupled_blocks(
            blocked_cred_ids=blocked_cred_ids,
            cred_coupled_api_pair=cred_coupled_api_pair,
            safe_api_repairs=safe_api_repairs,
            safe_creds=safe_cred_repairs,
            conflicts=conflicts,
        )

    updated_creds: dict[str, PlannedCredentialRepair] = {}
    for cid, cp in safe_cred_repairs.items():
        attached = tuple(by_cred.get(cid, ()))
        if attached:
            updated_creds[cid] = PlannedCredentialRepair(
                credential_id=cp.credential_id,
                credential_name=cp.credential_name,
                created_by=cp.created_by,
                before_vendor=cp.before_vendor,
                before_name=cp.before_name,
                before_version=cp.before_version,
                before_catalog_api_id=cp.before_catalog_api_id,
                after_vendor=cp.after_vendor,
                after_name=cp.after_name,
                after_version=cp.after_version,
                after_catalog_api_id=cp.after_catalog_api_id,
                shape=cp.shape,
                references=cp.references,
                connect_sessions=attached,
            )
        else:
            updated_creds[cid] = cp

    return updated_creds, standalone
