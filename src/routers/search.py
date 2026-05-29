"""BM25 search over registered operations AND workflows, with automatic catalog blending."""

from fastapi import APIRouter, Query

import src.bm25 as bm25
from src.models import SearchResult
from src.openapi_helpers import agent_hints
from src.routers.catalog import (
    GITHUB_REPO,
    get_registered_api_ids,
    load_manifest,
    load_workflow_manifest,
    search_manifest,
)
from src.utils import abbreviate


router = APIRouter()

_OP_INTERNAL_KEYS = {"_id", "_operation_id", "_api_id", "_vendor"}

# Sentinel character used to delimit the matched span inside a snippet.
# Picked deliberately — `\u0001` (U+0001 START OF HEADING) doesn't appear
# in human prose, so the frontend can split on it without escaping.
_MATCH_SENTINEL = "\u0001"
_SNIPPET_RADIUS = 40  # ~80-char window around the match


# ── source/type normalisation ─────────────────────────────────────────────────
# `source` and `type` are accepted both in their canonical form and in legacy
# synonyms so older clients (which only know `source=local|catalog`) keep
# working without translation.

_SOURCE_SYNONYMS = {
    "local": "workspace",
    "catalog": "directory",
}
_VALID_SOURCES = {"workspace", "directory", "all"}
_VALID_TYPES = {"all", "endpoint", "workflow", "api"}


def _normalise_source(value: str | None) -> str:
    if not value:
        return "all"
    v = value.lower().strip()
    v = _SOURCE_SYNONYMS.get(v, v)
    return v if v in _VALID_SOURCES else "all"


def _normalise_type(value: str | None) -> str:
    if not value:
        return "all"
    v = value.lower().strip()
    return v if v in _VALID_TYPES else "all"


# ── relevance helpers ────────────────────────────────────────────────────────


def _build_snippet(text: str, query: str) -> str | None:
    """Return ~80 char snippet around the first case-insensitive occurrence
    of `query` inside `text`, with the match wrapped in sentinel markers.

    Returns None when `text` is empty or the substring is absent. The caller
    is responsible for picking the highest-priority field before invoking
    this — the function does no field selection itself.
    """
    if not text or not query:
        return None
    haystack = text
    pos = haystack.lower().find(query.lower())
    if pos < 0:
        return None
    end = pos + len(query)
    start = max(0, pos - _SNIPPET_RADIUS)
    stop = min(len(haystack), end + _SNIPPET_RADIUS)
    prefix = haystack[start:pos]
    matched = haystack[pos:end]
    suffix = haystack[end:stop]
    snippet = f"{prefix}{_MATCH_SENTINEL}{matched}{_MATCH_SENTINEL}{suffix}"
    if start > 0:
        snippet = "…" + snippet
    if stop < len(haystack):
        snippet = snippet + "…"
    return snippet


def _compute_matches(
    query: str,
    *,
    name: str | None = None,
    operation_summary: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> tuple[list[str], str | None]:
    """Substring-match the lowered query against the four relevance fields.

    Returns `(matched_on, snippet)` where:
    - `matched_on` is a list of field labels the query was found in (a
      subset of `name | operation_summary | description | tag`).
    - `snippet` is the ~80 char window around the match in the
      highest-priority matched field (`name > operation_summary >
      description > tag`), or None if no field matched.

    A pure substring check, deliberately cheap — BM25's matched-fields
    aren't exposed and trying to derive them from the index would be
    fragile. This is "good enough" feedback for the UI.
    """
    if not query:
        return [], None
    q_lower = query.lower()
    matched: list[str] = []
    snippet: str | None = None

    fields = [
        ("name", name),
        ("operation_summary", operation_summary),
        ("description", description),
    ]
    for label, value in fields:
        if value and q_lower in value.lower():
            matched.append(label)
            if snippet is None:
                snippet = _build_snippet(value, query)

    if tags:
        tag_hit = next((t for t in tags if t and q_lower in t.lower()), None)
        if tag_hit:
            matched.append("tag")
            if snippet is None:
                snippet = _build_snippet(tag_hit, query)

    # Fallback: BM25 can rank a row via token-level matches that don't show
    # up as substring hits (e.g. case-folding edge cases, or query "charges"
    # vs token "charge" if the tokeniser normalises). Surface a sensible
    # default so callers can still group results, instead of leaking an
    # empty `matched_on` list to the wire.
    if not matched:
        matched = ["description"]

    return matched, snippet


def _op_links(op_id: str, api_id: str) -> dict:
    """Build _links for an operation result."""
    links = {"inspect": f"/inspect/{op_id}"}
    if api_id:
        links["api"] = f"/apis/{api_id}"
        links["operations"] = f"/apis/{api_id}/operations"
    return links


def _workflow_links(slug: str, wf_id: str) -> dict:
    """Build _links for a workflow result."""
    return {
        "inspect": f"/inspect/{wf_id}",
        "definition": f"/workflows/{slug}",
    }


@router.get(
    "/search",
    summary="Search the catalog — find operations and workflows by natural language intent",
    response_model=list[SearchResult],
    openapi_extra=agent_hints(
        when_to_use="Use when you need to discover APIs or workflows based on a natural language description of what you want to do. Primary entry point for finding capabilities — search first before exploring individual APIs.",
        prerequisites=["Requires authentication (toolkit key or human session)"],
        avoid_when="Do not use if you already know the exact operation ID or workflow slug — use GET /inspect/{id} directly instead.",
        related_operations=[
            "GET /inspect/{id} — get full operation details after finding it via search",
            "GET /apis — browse APIs by provider when you know the vendor",
            "GET /workflows — list all workflows when browsing by category",
        ],
    ),
)
async def search(
    q: str = Query(..., description='Search query, e.g. "send an email" or "create payment"'),
    n: int = Query(10, ge=1, le=100, description="Number of results to return"),
    source: str | None = Query(
        None,
        description=(
            "Restrict results by source: `workspace` (locally registered APIs and "
            "workflows) or `directory` (Jentic public catalog). Default `all` mixes "
            "both. Legacy synonyms `local`→`workspace` and `catalog`→`directory` are "
            "accepted for backwards compatibility."
        ),
    ),
    type: str | None = Query(
        None,
        description=(
            "Restrict by result type: `endpoint` (workspace operations only), "
            "`workflow` (workspace workflows + directory APIs that ship "
            "workflows), or `api` (directory APIs). Default `all` returns "
            "the full mix. Directory APIs always carry a `has_workflows` "
            "boolean indicating whether the public catalog also ships "
            "Arazzo workflows for that vendor."
        ),
    ),
):
    """BM25 search over all registered API operations, Arazzo workflows, and the Jentic public API catalog.

    Returns id, summary, description (≤3 sentences), type, score, and _links.
    - `source: "local"` — operation or workflow in your local registry
    - `source: "catalog"` — API available from the Jentic public catalog; add credentials to use

    Each row also carries `matched_on` (which fields the query hit) and an
    optional `match_snippet` with the matched span wrapped in `\u0001` markers.

    _links.inspect → GET /inspect/{id} for full schema and auth detail.
    _links.execute → broker URL to call directly once ready.
    Typical flow: search → inspect → execute.
    """
    source_n = _normalise_source(source)
    type_n = _normalise_type(type)

    # type='endpoint' wants only operations; type='workflow' wants workflows
    # (workspace) + catalog APIs that ship workflows; type='api' wants catalog
    # APIs without the workflow-availability filter. The local BM25 hit set
    # already covers operations + workflows, so we filter *after* ranking
    # rather than narrowing the index — keeps BM25 scoring consistent
    # regardless of which slice the caller asked for.
    #
    # Historical note: this endpoint used to emit a separate
    # `catalog_workflow_source` row type for vendor folders that ship Arazzo
    # workflows in the public repo. Those rows were 1:1 with `catalog_api`
    # rows for the same `api_id` (workflow_manifest entries are keyed by
    # vendor directory), carried no per-workflow detail, and visually
    # duplicated the API tile. We've collapsed them into a `has_workflows`
    # boolean on the corresponding `catalog_api` row so the UI renders one
    # tile per vendor with a "+ workflows" chip when relevant. The
    # `type=workflow` slice on the directory side now means "only catalog
    # APIs that have workflows" — no separate row type.
    include_local = source_n in ("workspace", "all")
    include_catalog_api = source_n in ("directory", "all") and type_n in (
        "all",
        "api",
        "workflow",
    )
    only_workflow_bearing = type_n == "workflow"
    include_op_rows = include_local and type_n in ("all", "endpoint")
    include_wf_rows = include_local and type_n in ("all", "workflow")

    results = bm25.search(q, n) if include_local else []
    out = []
    for doc, score in results:
        doc_type = doc.get("type", "operation")
        if doc_type == "workflow":
            if not include_wf_rows:
                continue
            wf_id = doc.get("id", "")
            slug = doc.get("slug", "")
            name = doc.get("name") or doc.get("summary")
            description = doc.get("description", "") or ""
            matched_on, snippet = _compute_matches(
                q,
                name=name,
                description=description,
            )
            out.append(
                {
                    "type": "workflow",
                    "source": "local",
                    "id": wf_id,
                    "slug": slug,
                    "summary": doc.get("summary") or doc.get("name"),
                    "description": abbreviate(description),
                    "involved_apis": doc.get("involved_apis", []),
                    "score": round(score, 4),
                    "matched_on": matched_on,
                    "match_snippet": snippet,
                    "_links": _workflow_links(slug, wf_id),
                }
            )
        else:
            if not include_op_rows:
                continue
            op_id = doc.get("id", "")
            api_id = doc.get("_api_id") or ""
            if not api_id and "/" in op_id:
                parts = op_id.split("/", 2)
                api_id = parts[1] if len(parts) >= 2 else ""
            clean = {k: v for k, v in doc.items() if k not in _OP_INTERNAL_KEYS}
            description_raw = clean.get("description") or ""
            if "description" in clean:
                clean["description"] = abbreviate(clean["description"] or "")
            matched_on, snippet = _compute_matches(
                q,
                operation_summary=clean.get("summary"),
                description=description_raw,
            )
            out.append(
                {
                    "type": "operation",
                    "source": "local",
                    **clean,
                    "score": round(score, 4),
                    "matched_on": matched_on,
                    "match_snippet": snippet,
                    "_links": _op_links(op_id, api_id),
                }
            )

    # Skip the catalog blender entirely when the caller pinned source to
    # `workspace` — the blender's own dedup logic depends on
    # `get_registered_api_ids` so cutting it short here also skips the DB
    # round-trip.
    if not include_catalog_api:
        return out

    # ── Catalog blending (always-on) ──────────────────────────────────────────
    manifest = load_manifest()
    registered_ids: set[str] = set()
    covered_sub_apis: set[str] = set()
    covered_leaf_vendors: set[str] = set()

    if manifest:
        registered_ids = await get_registered_api_ids()

        # Precise dedup: sub-apis by subdomain coverage, leaves by vendor
        _GENERIC_SUBS = {"api", "www", "app", "web", "portal", "v1", "v2", "v3"}
        for local_id in registered_ids:
            hostname = local_id.split("/")[0]
            parts = hostname.split(".")
            if len(parts) < 2:
                continue
            vendor = ".".join(parts[-2:])
            sub = ".".join(parts[:-2]) if len(parts) > 2 else ""
            if sub and sub not in _GENERIC_SUBS:
                covered_sub_apis.add(f"{vendor}/{sub}")
            covered_leaf_vendors.add(vendor)

        # `has_workflows` annotation: every catalog_api row carries a
        # boolean telling the UI whether the public catalog also ships
        # Arazzo workflows for this vendor. The set is precomputed once
        # per request so the per-row check is O(1). Replaces the old
        # `catalog_workflow_source` blender, which emitted a parallel row
        # per workflow-bearing vendor that was 1:1 with the API row.
        wf_manifest = load_workflow_manifest()
        workflow_api_ids = {e["api_id"] for e in wf_manifest} if wf_manifest else set()

        catalog_matches = search_manifest(manifest, q, n)
        for entry in catalog_matches:
            api_id = entry["api_id"]
            if api_id in registered_ids:
                continue
            if "/" in api_id:
                if api_id in covered_sub_apis:
                    continue
            else:
                vendor = (
                    (api_id.split(".")[-2] + "." + api_id.split(".")[-1])
                    if "." in api_id
                    else api_id
                )
                if vendor in covered_leaf_vendors:
                    continue
            has_workflows = api_id in workflow_api_ids
            # `type=workflow` on the directory side narrows to vendors
            # that actually ship workflows — replaces the old "show me
            # catalog_workflow_source rows" filter without introducing a
            # second row type.
            if only_workflow_bearing and not has_workflows:
                continue
            summary = f"{api_id} — available in Jentic public catalog"
            # Catalog rows have no rich text to search against — the
            # blender already filtered by api_id substring upstream, so
            # we can safely claim a `name` match and synthesise a snippet
            # from the summary.
            snippet = _build_snippet(api_id, q) or _build_snippet(summary, q)
            row = {
                "type": "catalog_api",
                "source": "catalog",
                "id": api_id,
                "api_id": api_id,
                "summary": summary,
                "description": None,
                "score": 0.0,
                "matched_on": ["name"],
                "match_snippet": snippet,
                "has_workflows": has_workflows,
                # Surface `spec_url` so the UI can `POST /import` without a
                # follow-up `GET /catalog/{api_id}` — same rationale as the
                # `/apis` browse row. Falls back to None for older manifests
                # that pre-date the cached `spec_url` field; the UI degrades
                # gracefully by re-fetching `getCatalogEntry` in that case.
                "spec_url": entry.get("spec_url"),
                "_links": {
                    "catalog": f"/catalog/{api_id}",
                    "credentials": "/credentials",
                    "github": f"https://github.com/{GITHUB_REPO}/tree/main/{entry['path']}",
                },
            }
            # When the vendor ships workflows, surface a deep link to
            # the workflows GitHub directory too — saves the UI from
            # synthesising the URL itself and stays in sync with however
            # `WORKFLOWS_CATALOG_PATH` evolves.
            if has_workflows:
                row["_links"]["github_workflows"] = (
                    f"https://github.com/{GITHUB_REPO}/tree/main/workflows/{api_id.replace('/', '~')}"
                )
            out.append(row)

    return out
