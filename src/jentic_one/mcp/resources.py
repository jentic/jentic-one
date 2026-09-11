"""The ``skill://`` resource surface served on the ``/mcp`` mount.

Serves the shipped skill set as MCP resources — ``skill://<name>`` for every
skill in :func:`jentic_one.shared.web.agent_discovery.shipped_skill_names`,
``skill://<name>/references/<file>`` for every shipped reference OUTSIDE the
CLI lane (decision D11 — see below), plus ``skill://index`` (the set
manifest) — mirroring the Go stdio server's surface
(``cli/internal/cli/api/mcp_resources.go``) **minus its hosted-vs-bundled
machinery**: the CLI prefers the connected backend's copy and falls back to
its embed because it is a *client* of a backend; the daemon **is** the
backend. There is exactly one source — the wheel-shipped package data
``agent_discovery`` already globs — so this module mints no new copy and
adds no fourth corner to the drift triangle: the mount consumes the same
allowlist and manifest helper the HTTP routes consume
(``tests/arch/test_skill_drift.py`` covers it transitively).

The lane filter (decision D11 of the plan): a reference named in
:data:`~jentic_one.shared.web.agent_discovery.CLI_ONLY_REFERENCES`
(``cli.md``) is never listed and never readable on this mount — an MCP
session has no ``jentic`` CLI, so serving it the CLI lane would only
mislead. The mount refuses to read what it does not list (listed set ==
readable set, the D11 invariant); the Go stdio server applies the SAME
filter (``skillgen.CLIOnlyReference``). This is a serving decision, not a
secret: the HTTP routes are the raw neutral channel and serve every
reference, ``cli.md`` included. Note the filter governs the ``skill://``
door only — a ``skill://index`` read, being the HTTP manifest verbatim,
still NAMES the CLI-lane references and their HTTP URLs in its
``references`` rows; it advertises where the neutral channel serves them,
it does not make them readable here.

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
    CLI_ONLY_REFERENCES,
    MARKDOWN_MEDIA_TYPE,
    REFERENCE_STEM_RE,
    SKILL_NAME_RE,
    _parse_frontmatter,
    load_skill_markdown,
    load_skill_reference,
    shipped_skill_names,
    shipped_skill_references,
    skills_index_rows,
)

#: Resource URI scheme — byte-equal to the Go constants
#: (``skillURIScheme``/``skillIndexURI``), so a model that learned the
#: vocabulary on one door reuses it on the other. ``skill://<name>`` serves
#: one skill document; ``skill://index`` serves the set manifest.
SKILL_URI_SCHEME = "skill://"
SKILL_INDEX_URI = SKILL_URI_SCHEME + "index"

#: The path infix separating a skill name from one of its reference files in
#: a resource URI: ``skill://<name>/references/<file>`` — byte-parallel to
#: the Go server's reference URIs (``registerResources`` builds
#: ``skillURIScheme + name + "/references/" + ref``) and to the HTTP route
#: ``GET /skills/<name>/references/<file>``.
REFERENCES_INFIX = "/references/"

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
    """The ``resources/list`` payload: skills + lane-filtered references + index.

    Derived from the same globs the HTTP routes serve
    (:func:`shipped_skill_names` / :func:`shipped_skill_references` — cached
    wheel package data), never a second hand-maintained list, so listing and
    reading cannot drift. Listing fields mirror the Go server's
    (``registerResources``): ``name``/``title`` per skill and per reference
    (``<name>/references/<file>`` / ``Jentic skill reference: <name>/<file>``
    — the same vocabulary on both doors), the frontmatter description plus
    the daemon provenance note, and Go-identical MIME types.

    The lane filter (D11): references named in :data:`CLI_ONLY_REFERENCES`
    are skipped — never listed here, never readable in
    :func:`read_skill_resource` — matching the Go stdio server's filter
    (``skillgen.CLIOnlyReference``). Skills without references contribute no
    reference rows.

    No pagination: the served set is the shipped skills, their MCP-visible
    references, and the index — the SDK's ``PaginatedRequestParams`` is
    accepted and ignored by the handler and no ``nextCursor`` is ever
    emitted, exactly what the Go server does. Revisit if the served set ever
    grows past dozens.
    """
    resources: list[mcp_types.Resource] = []
    for name in shipped_skill_names():
        resources.append(
            mcp_types.Resource(
                uri=SKILL_URI_SCHEME + name,
                name=name,
                title="Jentic skill: " + name,
                description=_skill_frontmatter(name).get("description", "") + SKILL_PROVENANCE_NOTE,
                mime_type=MARKDOWN_MEDIA_TYPE,
            )
        )
        for file in shipped_skill_references(name):
            if file in CLI_ONLY_REFERENCES:
                continue
            resources.append(
                mcp_types.Resource(
                    uri=SKILL_URI_SCHEME + name + REFERENCES_INFIX + file,
                    name=name + "/references/" + file,
                    title="Jentic skill reference: " + name + "/" + file,
                    description=_reference_description(name, file),
                    mime_type=MARKDOWN_MEDIA_TYPE,
                )
            )
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


def _reference_description(name: str, file: str) -> str:
    """One reference resource's listing description.

    The first sentence is the Go server's reference description verbatim
    (``registerResources`` — the two doors share the vocabulary); the
    provenance sentence is the daemon's own (same honest divergence as
    :data:`SKILL_PROVENANCE_NOTE`: nothing here is "embedded in this
    binary" — there is exactly one source, the shipped package data).
    """
    return (
        f"A level-3 reference document of the {name} skill (read the skill first; "
        "it points at this file when the material applies). Served from this "
        "deployment's shipped package data (the same bytes as "
        f"GET /skills/{name}/references/{file}); the read result's _meta carries "
        + META_SOURCE_KEY
        + " and "
        + META_VERSION_KEY
        + "."
    )


def read_skill_resource(uri: str, base: str) -> mcp_types.ReadResourceResult:
    """Resolve one ``resources/read`` URI — the entire pre-auth read seam.

    Exactly three arms, all public by construction, and **no identity read
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
    - ``skill://<name>/references/<file>`` (D11) where ``<name>`` passes the
      document arm's grammar+allowlist AND ``<file>`` passes the reference
      grammar (:data:`REFERENCE_STEM_RE` + ``.md``) + the
      :func:`shipped_skill_references` allowlist AND is NOT in
      :data:`CLI_ONLY_REFERENCES` → the reference's raw bytes verbatim,
      markdown MIME, ``_meta`` = source + the OWNING skill's frontmatter
      version (a reference has no frontmatter of its own). The lane filter
      is read-side too: the mount must refuse to read what it does not list
      (listed set == readable set), so ``cli.md`` is RESOURCE_NOT_FOUND here
      while the same bytes stay public over HTTP.

    **Everything else** — unknown scheme, ``skill://`` with a name that fails
    the grammar or the allowlist (including ``init-design``, which lives in
    ``skills/`` but is deliberately un-served), a reference that fails the
    grammar, the per-skill allowlist, or the lane filter, empty name or file,
    traversal junk — raises :data:`RESOURCE_NOT_FOUND`: the same layered,
    fail-closed validation as the HTTP route's 404.
    """
    if uri == SKILL_INDEX_URI:
        # Serialize with the JSONResponse separators so the read is
        # byte-identical to what ``GET /skills/index.json`` serves at the
        # same base — the manifest sha256s cover documents, and byte parity
        # here means one wire shape for clients to cache and compare.
        text = json.dumps(skills_index_rows(base), separators=(",", ":"), ensure_ascii=False)
        return _read_result(uri, SKILL_INDEX_MIME, text, {META_SOURCE_KEY: SOURCE_HOSTED})
    if uri.startswith(SKILL_URI_SCHEME):
        # ``partition`` splits on the FIRST infix, so a second ``/references/``
        # inside ``file`` survives into the filename and fails the grammar.
        name, infix, file = uri[len(SKILL_URI_SCHEME) :].partition(REFERENCES_INFIX)
        if SKILL_NAME_RE.fullmatch(name) and name in shipped_skill_names():
            if not infix:
                text = load_skill_markdown(name)
                return _read_result(uri, MARKDOWN_MEDIA_TYPE, text, _document_meta(text))
            if (
                file.endswith(".md")
                and REFERENCE_STEM_RE.fullmatch(file[: -len(".md")])
                and file in shipped_skill_references(name)
                and file not in CLI_ONLY_REFERENCES
            ):
                meta = _document_meta(load_skill_markdown(name))  # the OWNING skill's version
                text = load_skill_reference(name, file)
                return _read_result(uri, MARKDOWN_MEDIA_TYPE, text, meta)
    raise MCPError(RESOURCE_NOT_FOUND, "resource not found")


def _document_meta(skill_text: str) -> dict[str, str]:
    """The provenance ``_meta`` stamped from one skill document's frontmatter."""
    return {
        META_SOURCE_KEY: SOURCE_HOSTED,
        META_VERSION_KEY: _parse_frontmatter(skill_text).get("version") or "1",
    }


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
    "REFERENCES_INFIX",
    "RESOURCE_NOT_FOUND",
    "SKILL_INDEX_MIME",
    "SKILL_INDEX_URI",
    "SKILL_PROVENANCE_NOTE",
    "SKILL_URI_SCHEME",
    "SOURCE_HOSTED",
    "read_skill_resource",
    "skill_resources",
]
