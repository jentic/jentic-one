"""normalize url index path templates and regexes

Revision ID: e6f7a8b9c0d1
Revises: d6e7f8a9b0c1
Create Date: 2026-08-19

Repairs ``operation_url_indexes`` rows written before #1085 was fixed:
``build_index_entry`` used to compile ``path_regex`` from the **raw** path
template, while ``URLLookupService.resolve`` normalizes the incoming request
path (which strips trailing slashes). A template ending in ``/`` therefore
produced a regex that could never match any normalized request path, making
every trailing-slash operation unreachable through the broker
(``operation_not_found``).

The fixed ingest code canonicalizes the template before deriving the regex,
but URL-index rows are only rebuilt when their revision is re-ingested — so
already-published revisions keep the broken rows forever. This migration
recomputes ``path_template``, ``path_regex``, and ``segment_count`` for every
existing row using the same canonicalization the fixed code applies.

The normalization helpers below are **frozen copies** of the pure functions in
``jentic_one.registry.core.url_index`` at the time of this migration. They are
deliberately not imported: a later refactor of the live module must not
silently change what this historical migration does. A canary test
(``tests/unit/test_migrations.py``) asserts the copies still agree with the
live functions — if it fails, normalization semantics changed and a **new**
data migration is required.

Collision handling: the table has a global unique constraint on
``(method, host, host_regex, path_template)``. If normalizing a row's template
lands on a key an already-canonical row occupies, the stale row is deleted —
the surviving row is exactly what a fresh ingest would have written, and the
next re-ingest of the stale row's revision rebuilds its index anyway.

The repair is idempotent: rows already canonical are left untouched.

Downgrade is a no-op by design: the original trailing-slash templates cannot
be reconstructed (normalization is lossy), and the canonical rows remain
correct under pre-fix code — the old lookup path normalizes the request the
same way, so canonical rows match strictly more URLs than the broken ones did.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import unquote

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"  # pragma: allowlist secret
down_revision: str | None = "d6e7f8a9b0c1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Frozen copies of jentic_one.registry.core.url_index helpers (see module
# --- docstring). Do not "fix" these to track the live module.

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
    segments = path.split("/")
    output: list[str] = []
    for segment in segments:
        if segment == ".":
            continue
        elif segment == "..":
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
            safe_name = _safe_param_name(name)
            matcher = ".+" if is_catch_all else "[^/]+"
            regex_parts.append(f"(?P<{safe_name}>{matcher})")
    return "^" + "".join(regex_parts) + "$"


def _count_segments(path: str) -> int:
    if "**" in path or "{+" in path:
        return -1
    stripped = path.strip("/")
    if not stripped:
        return 0
    return len(stripped.split("/"))


# --- Repair body (kept as a function taking a connection so the integration
# --- test can exercise it directly against a real database).

_URL_INDEX = sa.table(
    "operation_url_indexes",
    sa.column("id"),
    sa.column("method"),
    sa.column("host"),
    sa.column("host_regex"),
    sa.column("path_template"),
    sa.column("path_regex"),
    sa.column("segment_count"),
)


def repair_url_index(bind: sa.engine.Connection) -> None:
    """Recompute canonical template/regex/segment-count for every stale row.

    Idempotent; deletes a stale row instead of updating it when its canonical
    key is already occupied (see module docstring for why deletion is safe).
    """
    rows = bind.execute(sa.select(_URL_INDEX)).all()

    for row in rows:
        template = _normalize_path_template(row.path_template)
        regex = _build_path_regex_pattern(template)
        segment_count = _count_segments(template)
        if (template, regex, segment_count) == (
            row.path_template,
            row.path_regex,
            row.segment_count,
        ):
            continue

        collision = bind.execute(
            sa.select(_URL_INDEX.c.id).where(
                _URL_INDEX.c.method == row.method,
                _URL_INDEX.c.host.is_not_distinct_from(row.host),
                _URL_INDEX.c.host_regex.is_not_distinct_from(row.host_regex),
                _URL_INDEX.c.path_template == template,
                _URL_INDEX.c.id != row.id,
            )
        ).first()

        if collision is not None:
            bind.execute(sa.delete(_URL_INDEX).where(_URL_INDEX.c.id == row.id))
        else:
            bind.execute(
                sa.update(_URL_INDEX)
                .where(_URL_INDEX.c.id == row.id)
                .values(
                    path_template=template,
                    path_regex=regex,
                    segment_count=segment_count,
                )
            )


def upgrade() -> None:
    repair_url_index(op.get_bind())


def downgrade() -> None:
    # Lossy data repair — the pre-fix trailing-slash forms cannot be restored,
    # and canonical rows remain valid (and resolvable) under pre-fix code.
    pass
