"""rebuild url index rows displaced under the global key

Revision ID: e8f9a0b1c2d3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-29

Data migration paired with ``e7f8a9b0c1d2`` (which scoped the
``operation_url_indexes`` key to the revision). Under the old global key a URL
kept only the row of whichever revision indexed it last, so a live revision
whose URLs were later indexed by a draft (or by another API) lost those rows.
Now that unpinned lookups only consult each API's live revision, those missing
rows would leave the live revision's URLs unroutable.

For every revision that can still serve and has a stored spec file, this
migration recomputes the rows ``BuildURLIndexStage`` writes (effective servers
operation > path > root, server-variable defaults, host/path canonicalization,
per-revision structural dedup) from ``spec_files.content`` and the revision's ``operations``, and
**inserts only the rows that are missing**. Existing rows are never updated or
deleted: every surviving row was written by its own revision's ingest, so it is
already correct for that revision. A row counts as present when the revision
already has one with the same ``(method, host, structural path)`` — the key
ingest itself dedups on — so a row written by a newer ingest whose template
text differs cosmetically is never duplicated. That makes the migration
idempotent and a no-op on a database that never had a displacement.

Which revisions are rebuilt
---------------------------
Only revisions that can serve a request again:

- each API's live revision (``apis.current_revision_id``) — unpinned lookups;
- drafts — ``Jentic-Revision`` pins and a later promote;
- archived revisions recorded as an overlay's ``superseded_revision_id`` — the
  only way an archived revision becomes live again is an overlay rollback.

Every other archived revision is skipped: it can never be promoted, and a pin
on it is refused, so rows for it would be dead weight. Skipping them also keeps
the rolling-deploy window narrow: the previous release's unpinned lookup reads
every revision's rows and reports an ambiguous match when two revisions index
one URL, so only URLs that one of the above revisions actually shares with
another revision are affected on not-yet-rolled pods, and only until they roll.

Revisions are read in keyset-paginated batches, one spec at a time, so memory
stays bounded however many revisions exist. A revision whose stored spec the
frozen helpers cannot process (malformed ``paths`` / ``servers``) is logged and
skipped rather than aborting the upgrade — ingest could not have indexed it
either.

The URL helpers below are **frozen copies** of the pure functions in
``jentic_one.registry.core.url_index`` (and the orchestration of
``BuildURLIndexStage``) at the time of this migration, for the same reason as
``e6f7a8b9c0d1``: a later change to the live module must not silently change
what this historical migration writes. A canary test in
``tests/unit/test_migrations.py`` asserts they still agree with the live
functions, and an integration test asserts the rebuilt rows equal what a fresh
ingest writes.

Downgrade is a no-op: the inserted rows are exactly what a fresh ingest of each
revision would write, and ``e7f8a9b0c1d2``'s downgrade collapses them to one
row per URL before restoring the global key.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any
from urllib.parse import unquote, urlparse

import sqlalchemy as sa
import structlog
from alembic import op

from jentic_one.shared.db.types import GUID, json_variant, text_array_variant

revision: str = "e8f9a0b1c2d3"  # pragma: allowlist secret
down_revision: str | None = "e7f8a9b0c1d2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_logger = structlog.get_logger(__name__)

#: Revisions fetched per keyset page.
_BATCH_SIZE = 500


# --- Frozen copies of jentic_one.registry.core.url_index helpers (see module
# --- docstring). Do not "fix" these to track the live module.

_SCHEME_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443, "ftp": 21}
_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
_PERCENT_ENCODED_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_UNRESERVED_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_RFC6570_OPERATORS = frozenset("+#./;?&")


def _normalize_percent_encoding(path: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        encoded = match.group(0)
        char = chr(int(encoded[1:], 16))
        if char in _UNRESERVED_CHARS:
            return char
        return encoded.upper()

    return _PERCENT_ENCODED_RE.sub(_replace, path)


def _resolve_dot_segments(path: str) -> str:
    output: list[str] = []
    for segment in path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if output:
                output.pop()
        else:
            output.append(segment)
    resolved = "/".join(output)
    if path.startswith("/") and not resolved.startswith("/"):
        resolved = "/" + resolved
    return resolved


def _normalize_path(path: str) -> str:
    decoded = unquote(path)
    resolved = _resolve_dot_segments(decoded)
    normalized = _normalize_percent_encoding(resolved)
    if normalized and not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized.rstrip("/") or "/"


def _normalize_path_template(template: str) -> str:
    parts = _PATH_PARAM_RE.split(template)
    tokens: list[str] = []
    shielded: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            shielded.append(part)
        else:
            tokens.append(part)
            shielded.append(f"\x00{len(tokens) - 1}\x00")
    normalized = _normalize_path("".join(shielded))
    for idx, token in enumerate(tokens):
        normalized = normalized.replace(f"\x00{idx}\x00", "{" + token + "}")
    return normalized


def _normalise_host(host: str, scheme: str = "https") -> str:
    host = host.lower()
    if ":" in host:
        hostname, port_str = host.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return host
        default_port = _SCHEME_DEFAULT_PORTS.get(scheme)
        if default_port and port == default_port:
            return hostname
        return host
    return host


def _build_host_regex_pattern(host: str) -> str:
    parts = _PATH_PARAM_RE.split(host)
    regex_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            regex_parts.append(re.escape(part))
        else:
            regex_parts.append(r"[^:/]+")
    return "^" + "".join(regex_parts) + "$"


def _expand_server_variables(url_template: str, defaults: list[tuple[str, str | None]]) -> str:
    result = url_template
    for name, default_value in defaults:
        if default_value is None:
            continue
        result = result.replace("{" + name + "}", default_value)
    return result


def _parse_server_url(url: str) -> tuple[str, str, str]:
    """Return ``(scheme, normalised host, normalised path)`` for a server URL."""
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or ""
    port: int | None = parsed.port
    path = parsed.path or "/"
    if port and _SCHEME_DEFAULT_PORTS.get(scheme) == port:
        port = None
    return (
        scheme,
        _normalise_host(f"{host}:{port}" if port else host, scheme),
        _normalize_path(path),
    )


def _merge_paths(base_path: str, operation_path: str) -> str:
    base = base_path.rstrip("/")
    op_path = operation_path if operation_path.startswith("/") else "/" + operation_path
    return base + op_path


def _safe_param_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _split_param_token(token: str) -> tuple[str, bool]:
    is_catch_all = token.startswith("+")
    if token and token[0] in _RFC6570_OPERATORS:
        return token[1:], is_catch_all
    return token, is_catch_all


def _build_path_regex_pattern(path_template: str) -> str:
    parts = _PATH_PARAM_RE.split(path_template)
    regex_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            regex_parts.append(re.escape(part))
        else:
            name, is_catch_all = _split_param_token(part)
            matcher = ".+" if is_catch_all else "[^/]+"
            regex_parts.append(f"(?P<{_safe_param_name(name)}>{matcher})")
    return "^" + "".join(regex_parts) + "$"


def _structural_regex(path_template: str) -> str:
    parts = _PATH_PARAM_RE.split(path_template)
    regex_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            regex_parts.append(re.escape(part))
        else:
            _name, is_catch_all = _split_param_token(part)
            regex_parts.append(".+" if is_catch_all else "[^/]+")
    return "^" + "".join(regex_parts) + "$"


def _extract_param_names(path_template: str) -> list[str]:
    return [_split_param_token(token)[0] for token in _PATH_PARAM_RE.findall(path_template)]


def _count_segments(path: str) -> int:
    if "**" in path or "{+" in path:
        return -1
    stripped = path.strip("/")
    if not stripped:
        return 0
    return len(stripped.split("/"))


def build_entry(host: str, path_template: str, scheme: str = "https") -> dict[str, Any]:
    """Frozen ``build_index_entry``, returned as the stored column values."""
    normalized_host = _normalise_host(host, scheme)
    template = _normalize_path_template(path_template)
    return {
        "host": normalized_host,
        "host_regex": _build_host_regex_pattern(normalized_host),
        "path_template": template,
        "path_regex": _build_path_regex_pattern(template),
        "param_names": _extract_param_names(template),
        "segment_count": _count_segments(template),
    }


# --- Frozen copy of BuildURLIndexStage's orchestration.


def _effective_servers(content: dict[str, Any], path: str, method: str) -> list[dict[str, Any]]:
    """Operation-level servers, else path-level, else the document's root servers."""
    paths = content.get("paths") or {}
    path_item = paths.get(path) or {}
    operation_data = path_item.get(method.lower()) or {}
    return operation_data.get("servers") or path_item.get("servers") or content.get("servers") or []


def _variable_defaults(server: dict[str, Any]) -> list[tuple[str, str | None]] | None:
    """``(name, default)`` pairs, or ``None`` when ingest could not have indexed the server.

    Ingest only understands the OpenAPI map form of ``variables``; any other
    non-empty shape made the stage fail for that revision, so nothing was ever
    indexed from it and nothing is rebuilt from it here.
    """
    variables = server.get("variables") or {}
    if not isinstance(variables, dict):
        return None
    return [
        (name, spec.get("default") if isinstance(spec, dict) else None)
        for name, spec in variables.items()
    ]


def expected_rows(
    content: dict[str, Any], operations: Sequence[tuple[str, str, str]]
) -> list[dict[str, Any]]:
    """Rows ingest writes for one revision, given ``(operation_id, path, method)`` triples."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for operation_id, path, method in operations:
        for server in _effective_servers(content, path, method):
            url = server.get("url") if isinstance(server, dict) else None
            if not isinstance(url, str):
                continue
            defaults = _variable_defaults(server)
            if defaults is None:
                continue
            scheme, host, base_path = _parse_server_url(_expand_server_variables(url, defaults))
            entry = build_entry(host, _merge_paths(base_path, path), scheme)
            dedup_key = (method.upper(), entry["host"], _structural_regex(entry["path_template"]))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            rows.append({"operation_id": operation_id, "method": method.upper(), **entry})
    return rows


# --- Rebuild body (a function taking a connection so the integration test can
# --- exercise it directly against a real database).

_URL_INDEX = sa.table(
    "operation_url_indexes",
    sa.column("id", GUID()),
    sa.column("operation_id", sa.String()),
    sa.column("revision_id", GUID()),
    sa.column("method", sa.String()),
    sa.column("host", sa.Text()),
    sa.column("host_regex", sa.Text()),
    sa.column("path_template", sa.Text()),
    sa.column("path_regex", sa.Text()),
    sa.column("param_names", text_array_variant()),
    sa.column("segment_count", sa.Integer()),
    sa.column("created_by", sa.String()),
)
_REVISIONS = sa.table(
    "api_revisions",
    sa.column("id", GUID()),
    sa.column("state", sa.String()),
    sa.column("created_by", sa.String()),
)
_APIS = sa.table("apis", sa.column("current_revision_id", GUID()))
_OVERLAYS = sa.table("overlays", sa.column("superseded_revision_id", GUID()))
_SPEC_FILES = sa.table(
    "spec_files",
    sa.column("revision_id", GUID()),
    sa.column("filename", sa.String()),
    sa.column("content", json_variant()),
)
_OPERATIONS = sa.table(
    "operations",
    sa.column("id", sa.String()),
    sa.column("revision_id", GUID()),
    sa.column("path", sa.Text()),
    sa.column("method", sa.String()),
)


def _row_key(row: Any) -> tuple[Any, ...]:
    """Ingest's per-revision dedup key: ``(method, host, structural path)``."""
    return (row["method"], row["host"], _structural_regex(row["path_template"]))


def _servable_revisions(bind: sa.engine.Connection, after: Any) -> list[Any]:
    """One keyset page of revisions that can still serve (see module docstring)."""
    live = sa.select(_APIS.c.current_revision_id).where(_APIS.c.current_revision_id.is_not(None))
    rollback_targets = sa.select(_OVERLAYS.c.superseded_revision_id).where(
        _OVERLAYS.c.superseded_revision_id.is_not(None)
    )
    stmt = sa.select(_REVISIONS.c.id, _REVISIONS.c.created_by).where(
        sa.or_(
            _REVISIONS.c.state == "draft",
            _REVISIONS.c.id.in_(live),
            _REVISIONS.c.id.in_(rollback_targets),
        )
    )
    if after is not None:
        stmt = stmt.where(_REVISIONS.c.id > after)
    return list(bind.execute(stmt.order_by(_REVISIONS.c.id).limit(_BATCH_SIZE)).all())


def rebuild_url_index(bind: sa.engine.Connection) -> int:
    """Insert every missing URL-index row for servable revisions with a stored spec.

    Returns the number of rows inserted. Idempotent; never touches existing rows.
    """
    inserted = 0
    after: Any = None
    while batch := _servable_revisions(bind, after):
        after = batch[-1].id
        for revision_row in batch:
            inserted += _rebuild_revision(bind, revision_row)
    return inserted


def _rebuild_revision(bind: sa.engine.Connection, revision_row: Any) -> int:
    """Insert the missing rows of one revision; returns how many were inserted."""
    # Ingest stores one primary spec file per revision; mirror
    # SpecFileRepository.get_for_revision's deterministic pick.
    content = bind.execute(
        sa.select(_SPEC_FILES.c.content)
        .where(_SPEC_FILES.c.revision_id == revision_row.id)
        .order_by(_SPEC_FILES.c.filename)
        .limit(1)
    ).scalar_one_or_none()
    if not isinstance(content, dict):
        return 0

    operations = [
        (row.id, row.path, row.method)
        for row in bind.execute(
            sa.select(_OPERATIONS.c.id, _OPERATIONS.c.path, _OPERATIONS.c.method)
            .where(_OPERATIONS.c.revision_id == revision_row.id)
            .order_by(_OPERATIONS.c.id)
        )
    ]
    if not operations:
        return 0

    try:
        wanted = expected_rows(content, operations)
    except Exception:  # one malformed stored spec must not abort the upgrade
        _logger.warning(
            "url_index_rebuild_skipped_revision",
            revision_id=str(revision_row.id),
            reason="stored spec could not be processed",
            exc_info=True,
        )
        return 0

    existing = {
        _row_key(row._mapping)
        for row in bind.execute(
            sa.select(
                _URL_INDEX.c.method,
                _URL_INDEX.c.host,
                _URL_INDEX.c.path_template,
            ).where(_URL_INDEX.c.revision_id == revision_row.id)
        )
    }
    missing = [row for row in wanted if _row_key(row) not in existing]
    if not missing:
        return 0

    bind.execute(
        sa.insert(_URL_INDEX),
        [
            {
                **row,
                "id": uuid.uuid4(),
                "revision_id": revision_row.id,
                "created_by": revision_row.created_by,
            }
            for row in missing
        ],
    )
    return len(missing)


def upgrade() -> None:
    rebuild_url_index(op.get_bind())


def downgrade() -> None:
    # Inserted rows are what a fresh ingest writes; e7f8a9b0c1d2's downgrade
    # collapses duplicates before restoring the global key.
    pass
