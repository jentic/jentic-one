"""The ``skill://`` resource surface served on the ``/mcp`` mount.

Serves the shipped skill set as MCP resources — ``skill://<name>`` for every
skill in :func:`jentic_one.shared.web.agent_discovery.shipped_skill_names`,
plus ``skill://index`` (the set manifest) — mirroring the Go stdio server's
surface (``cli/internal/cli/api/mcp_resources.go``) **minus its
hosted-vs-bundled machinery**: the CLI prefers the connected backend's copy
and falls back to its embed because it is a *client* of a backend; the daemon
**is** the backend. There is exactly one source — the wheel-shipped package
data ``agent_discovery`` already globs — so this module mints no new copy and
adds no fourth corner to the drift triangle: the mount consumes the same
allowlist and manifest helper the HTTP routes consume
(``tests/arch/test_skill_drift.py`` covers it transitively).

Kept as a sibling of ``app.py`` (the ``access_compose.py`` precedent:
handlers-adjacent logic lives next to the handlers, keeping ``app.py``
gate-focused). ``app.py``'s ``on_read_resource`` is a bare delegation to
:func:`read_skill_resource` — pinned structurally by
``tests/unit/mcp/test_resources.py`` — so this resolver is the entire
pre-auth enforcement seam for ``resources/read``.

Provenance (decision D3 of the plan): every read stamps
``one.jentic/source: "hosted"`` — the Go vocabulary's (``skillgen.Source``)
"the connected backend's copy, the session's source of truth", which is
exactly what a client reading from the mount gets. ``hosted`` is the ONLY
value this mount ever stamps (nothing client-side is embedded, so ``bundled``
would be a lie, and a third value would distinguish nothing), which makes the
Go index description's index-vs-document source-mismatch caveat structurally
impossible here — its prose is dropped from the index description accordingly.
"""

from __future__ import annotations

import json

import mcp.types as mcp_types
from mcp.shared.exceptions import MCPError

from jentic_one.shared.web.agent_discovery import (
    MARKDOWN_MEDIA_TYPE,
    SKILL_NAME_RE,
    _parse_frontmatter,
    load_skill_markdown,
    shipped_skill_names,
    skills_index_rows,
)

#: Resource URI scheme — byte-equal to the Go constants
#: (``skillURIScheme``/``skillIndexURI``), so a model that learned the
#: vocabulary on one door reuses it on the other. ``skill://<name>`` serves
#: one skill document; ``skill://index`` serves the set manifest.
SKILL_URI_SCHEME = "skill://"
SKILL_INDEX_URI = SKILL_URI_SCHEME + "index"

#: ``_meta`` keys carrying the provenance on every read result — byte-equal to
#: the Go constants (``skillMetaSource``/``skillMetaVersion``), namespaced per
#: the MCP general-fields guidance.
META_SOURCE_KEY = "one.jentic/source"
META_VERSION_KEY = "one.jentic/version"

#: The one source value this mount ever stamps (see the module docstring).
SOURCE_HOSTED = "hosted"

#: MIME type for the index manifest — the same string the Go constant
#: (``skillIndexMIME``) mirrors and ``GET /skills/index.json`` serves.
#: Documents reuse ``MARKDOWN_MEDIA_TYPE`` (= Go's ``skillMarkdownMIME``).
SKILL_INDEX_MIME = "application/json"

#: The MCP spec's resource-not-found JSON-RPC error code. The installed SDK
#: (2.1.1) leaves unmapped codes at HTTP 200, so the refusal rides in-band as
#: a JSON-RPC error — like every other handler error on the mount.
RESOURCE_NOT_FOUND = -32002

#: Appended to every resource description so a client (and its model) knows
#: where the bytes come from and how to read the provenance stamp.
#: Deliberately NOT the Go ``skillProvenanceNote`` verbatim: that note
#: promises "falling back to the copy embedded in this binary offline", which
#: is false here — the daemon serves one source. Honest divergence in prose,
#: identical ``_meta`` keys on the wire.
SKILL_PROVENANCE_NOTE = (
    " Served from this deployment's shipped package data (the same bytes as"
    " GET /skills/<name>.md); the read result's _meta carries "
    + META_SOURCE_KEY
    + " and "
    + META_VERSION_KEY
    + "."
)

#: The index resource's listing description: the Go server's manifest sentence
#: with the source-mismatch caveat dropped (structurally impossible here — see
#: the module docstring) and the url-vs-uri tell replaced by the daemon truth:
#: rows are always the hosted shape, locating each document by absolute
#: ``url`` (the strongest parity claim — the HTTP manifest verbatim).
_INDEX_DESCRIPTION = (
    "Manifest of the served skill set: name, description, version, and the sha256 of each "
    "document's raw bytes, so a client can pick and verify skills without reading them all. "
    "Rows locate each document with an absolute `url` — the same manifest "
    "GET /skills/index.json serves." + SKILL_PROVENANCE_NOTE
)


def skill_resources() -> list[mcp_types.Resource]:
    """The ``resources/list`` payload: the shipped skill set plus the index.

    Derived from the same glob the HTTP routes serve
    (:func:`shipped_skill_names` — cached wheel package data), never a second
    hand-maintained list, so listing and reading cannot drift. Listing fields
    mirror the Go server's (``registerResources``): ``name``/``title`` per
    skill, the frontmatter description plus the daemon provenance note, and
    Go-identical MIME types.

    No pagination: the served set is the shipped skills plus the index
    (currently four resources) — the SDK's ``PaginatedRequestParams`` is
    accepted and ignored by the handler and no ``nextCursor`` is ever
    emitted, exactly what the Go server does. Revisit if the served set ever
    grows past dozens.
    """
    resources = [
        mcp_types.Resource(
            uri=SKILL_URI_SCHEME + name,
            name=name,
            title="Jentic skill: " + name,
            description=_skill_frontmatter(name).get("description", "") + SKILL_PROVENANCE_NOTE,
            mime_type=MARKDOWN_MEDIA_TYPE,
        )
        for name in shipped_skill_names()
    ]
    resources.append(
        mcp_types.Resource(
            uri=SKILL_INDEX_URI,
            name="index",
            title="Jentic skill index",
            description=_INDEX_DESCRIPTION,
            mime_type=SKILL_INDEX_MIME,
        )
    )
    return resources


def read_skill_resource(uri: str, base: str) -> mcp_types.ReadResourceResult:
    """Resolve one ``resources/read`` URI — the entire pre-auth read seam.

    Exactly two arms, both public by construction, and **no identity read
    anywhere** — the resolver never sees a credential, so its behavior is
    *structurally* identical pre-auth and post-auth:

    - ``skill://index`` → the SAME manifest rows ``GET /skills/index.json``
      serves, produced by the SAME function (:func:`skills_index_rows`), so
      sha256 parity with the HTTP manifest is structural, not tested into
      existence. ``base`` is only used by this arm (absolute ``url`` rows).
      ``_meta`` carries the source only — Go parity: the index carries
      per-row versions, so ``skillReadResult`` omits the top-level stamp.
    - ``skill://<name>`` where ``<name>`` passes :data:`SKILL_NAME_RE` AND the
      :func:`shipped_skill_names` allowlist → the RAW package-data bytes
      verbatim (no BaseURL interpolation — render-time is CLI-only), with
      ``_meta`` = source + the frontmatter version (default ``"1"``).

    **Everything else** — unknown scheme, ``skill://`` with a name that fails
    the grammar or the allowlist (including ``init-design``, which lives in
    ``skills/`` but is deliberately un-served), empty name, traversal junk —
    raises :data:`RESOURCE_NOT_FOUND`: the same layered, fail-closed
    validation as the HTTP route's 404.
    """
    if uri == SKILL_INDEX_URI:
        # Serialize with the JSONResponse separators so the read is
        # byte-identical to what ``GET /skills/index.json`` serves at the
        # same base — the manifest sha256s cover documents, and byte parity
        # here means one wire shape for clients to cache and compare.
        text = json.dumps(skills_index_rows(base), separators=(",", ":"), ensure_ascii=False)
        return _read_result(uri, SKILL_INDEX_MIME, text, {META_SOURCE_KEY: SOURCE_HOSTED})
    if uri.startswith(SKILL_URI_SCHEME):
        name = uri[len(SKILL_URI_SCHEME) :]
        if SKILL_NAME_RE.fullmatch(name) and name in shipped_skill_names():
            text = load_skill_markdown(name)
            meta = {
                META_SOURCE_KEY: SOURCE_HOSTED,
                META_VERSION_KEY: _parse_frontmatter(text).get("version") or "1",
            }
            return _read_result(uri, MARKDOWN_MEDIA_TYPE, text, meta)
    raise MCPError(RESOURCE_NOT_FOUND, "resource not found")


def _skill_frontmatter(name: str) -> dict[str, str]:
    """Parsed frontmatter scalars for one shipped skill."""
    return _parse_frontmatter(load_skill_markdown(name))


def _read_result(
    uri: str, mime_type: str, text: str, meta: dict[str, str]
) -> mcp_types.ReadResourceResult:
    """The one read-result shape both arms return: the document bytes VERBATIM
    with the provenance stamped into the contents' ``_meta`` (wire alias)."""
    return mcp_types.ReadResourceResult(
        contents=[
            mcp_types.TextResourceContents(uri=uri, mime_type=mime_type, text=text, _meta=meta)
        ]
    )


__all__ = [
    "META_SOURCE_KEY",
    "META_VERSION_KEY",
    "RESOURCE_NOT_FOUND",
    "SKILL_INDEX_MIME",
    "SKILL_INDEX_URI",
    "SKILL_PROVENANCE_NOTE",
    "SKILL_URI_SCHEME",
    "SOURCE_HOSTED",
    "read_skill_resource",
    "skill_resources",
]
