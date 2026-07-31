"""Pure manifest + preview projection logic for the catalog (no I/O, no DB).

This is the "copy the idea, not the code" re-expression of jentic-mini's
``routers/catalog.py`` helpers. Everything here is a pure function over dicts so it
is trivially unit-testable and free of network/database coupling. The service layer
(``service.py``) owns the actual GitHub fetches and DB writes and calls into these
helpers.

Scope: **APIs only.** mini's workflow manifest / Arazzo preview is deliberately out of
scope (workflows stay deferred per D-001).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# api_id is parsed out of an apis.json `include[].url` of the shape
# .../apis/openapi/{domain}/{sub}/{version}/apis.json
_INCLUDE_URL_RE = re.compile(r"/apis/openapi/([^/]+)/([^/]+)/([^/]+)/apis\.json")
# A `sub` segment that looks like a version/branch marker means the API has no
# umbrella sub-name; the api_id is just the domain.
_VERSION_SUBDIR_RE = re.compile(r"^(main|master|latest|heads|v\d|[0-9])", re.IGNORECASE)

_CATALOG_PATH = "apis/openapi"
_GITHUB_REPO = "jentic/jentic-public-apis"

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

#: Hard ceiling on operations returned by a single preview response.
PREVIEW_MAX_OPERATIONS = 200


@dataclass(frozen=True)
class ManifestEntry:
    """One parsed catalog entry — a slim pointer stored in the snapshot blob."""

    api_id: str
    path: str
    spec_url: str | None
    github_url: str
    vendor: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Serialise to the plain dict shape persisted in the snapshot blob."""
        return {
            "api_id": self.api_id,
            "path": self.path,
            "spec_url": self.spec_url,
            "github_url": self.github_url,
            "vendor": self.vendor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManifestEntry:
        """Rehydrate from a snapshot-blob dict (tolerant of missing optionals)."""
        return cls(
            api_id=data["api_id"],
            path=data.get("path") or "",
            spec_url=data.get("spec_url"),
            github_url=data.get("github_url") or "",
            vendor=data.get("vendor"),
        )


def github_tree_url(path: str) -> str:
    """Build the human-facing GitHub tree URL for a catalog entry path."""
    return f"https://github.com/{_GITHUB_REPO}/tree/main/{path}"


def parse_apis_json(data: dict[str, Any]) -> list[ManifestEntry]:
    """Build catalog entries from the curated ``apis.json`` index document.

    Mirrors mini's ``_build_manifest_from_apis_json``: one entry per unique
    ``api_id`` with umbrella-vendor expansion (a non-version ``sub`` segment is
    folded into the id as ``domain/sub``). Pure: takes the already-fetched dict.
    """
    includes = data.get("include") or []
    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    for entry in includes:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url", "")
        match = _INCLUDE_URL_RE.search(url)
        if not match:
            continue
        domain, sub, _version = match.groups()
        api_id = domain if _VERSION_SUBDIR_RE.match(sub) else f"{domain}/{sub}"
        if api_id in seen:
            continue
        seen.add(api_id)
        path = f"{_CATALOG_PATH}/{domain}/{sub}" if sub != domain else f"{_CATALOG_PATH}/{domain}"
        spec_url = (
            url.replace("/apis.json", "/openapi.json") if url.endswith("/apis.json") else None
        )
        entries.append(
            ManifestEntry(
                api_id=api_id,
                path=path,
                spec_url=spec_url,
                github_url=github_tree_url(path),
                vendor=extract_vendor(api_id),
            )
        )
    entries.sort(key=lambda e: e.api_id)
    return entries


def extract_vendor(api_id: str) -> str | None:
    """Reduce an api_id to its registrable-domain vendor for dedup/coverage.

    ``api.stripe.com`` → ``stripe.com``; ``slack.com/api`` → ``slack.com``;
    a bare ``stripe`` stays ``stripe``. Mirrors mini's ``extract_vendor`` intent
    (eTLD+1-ish) without a public-suffix dependency: take the host portion before
    the first slash and keep the last two dotted labels.
    """
    if not api_id:
        return None
    host = api_id.split("/", 1)[0].strip().lower()
    if not host:
        return None
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels) if labels else None
    return ".".join(labels[-2:])


# ── search / scoring (pure) ──────────────────────────────────────────────────


def _tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


#: Encoding of the two ranking signals into one sortable integer (the cursor
#: carries a single number). ``coverage`` — how many query tokens matched at
#: all — is the dominant signal and preserves the pre-existing recall-fraction
#: ordering exactly: matching more of the query always outranks matching less
#: of it, no matter how "strong" the individual hits are (so a multi-word query
#: like "google api" still ranks ``googleapis.com`` — both words covered —
#: above the hundreds of ids that merely contain the whole word "api").
#: ``whole`` — how many of those matches are whole ``api_id`` tokens rather
#: than substrings buried in longer tokens — only breaks ties *within* a
#: coverage class (the #604 fix: query "stripe" ranks ``stripe.com``, a whole
#: token, above ``soundstripe.com``, a substring).
#:
#: ``score = coverage * (len(q_tokens) + 1) + whole`` is order-isomorphic to
#: the tuple ``(coverage, whole)`` because ``whole <= coverage <= len(q_tokens)
#: < len(q_tokens) + 1`` — the quality digit can never carry into the coverage
#: digit. Scores are only ever compared within one query (the cursor is
#: re-paired with the same ``q`` on each page), so the query-length-dependent
#: base is safe.


def score_entry(api_id: str, q_tokens: list[str]) -> int:
    """Relevance score for an ``api_id`` against (deduplicated) query tokens.

    Two signals, folded into one integer (see the encoding note above):

    - **coverage** (dominant): the number of query tokens that match at all —
      as a whole ``api_id`` token or as a substring of one. Identical to the
      membership/recall predicate used since the original scorer.
    - **whole-token quality** (tie-break): of the covered tokens, how many are
      whole ``api_id`` tokens (query "stripe" IS the id token "stripe") rather
      than incidental substrings ("box" buried inside "mapbox").

    A whole-token hit and a substring hit are mutually exclusive per query
    token — a whole-token match already satisfies the substring test, so it is
    counted once, at the higher tier. Integer scores add and compare exactly,
    so the sort key and the cursor round-trip without float error.
    """
    name_tokens = _tokenise(api_id)
    if not name_tokens or not q_tokens:
        return 0
    name_token_set = set(name_tokens)
    coverage = 0
    whole = 0
    for t in q_tokens:
        if t in name_token_set:
            coverage += 1
            whole += 1
        elif any(t in nt for nt in name_tokens):
            coverage += 1
    return coverage * (len(q_tokens) + 1) + whole


def score_entries(
    entries: list[ManifestEntry], q: str | None
) -> list[tuple[ManifestEntry, int | None]]:
    """Order entries for paging, returning each with its sort score.

    Two stable orderings, each with a unique tail key (``api_id``) so keyset
    paging never skips or repeats:

    - **Browse** (empty ``q``): the natural ``api_id`` order, score ``None``.
    - **Search**: matches only, sorted by ``(-score, api_id)``; the integer score
      is carried in the cursor so the next page resumes at the exact tie-break.

    Membership (which entries appear at all) is unchanged from the original
    token-overlap behaviour: an entry qualifies iff at least one query token
    substring-matches one of its ``api_id`` tokens. Only the *scoring* — and thus
    the *order* — is graded by match quality (see ``score_entry``); recall is
    identical to before.
    """
    if not q or not q.strip():
        return [(e, None) for e in sorted(entries, key=lambda e: e.api_id)]
    # Dedupe tokens (order-preserving): duplicates would scale every entry's
    # score uniformly — no ranking effect — while inflating scan cost, and the
    # coverage encoding assumes each query token counts once.
    q_tokens = list(dict.fromkeys(_tokenise(q)))
    if not q_tokens:
        return [(e, None) for e in sorted(entries, key=lambda e: e.api_id)]
    scored = [(e, score_entry(e.api_id, q_tokens)) for e in entries]
    # Keep only positive scores. This also guarantees a search cursor's carried
    # score is always > 0, so it is never confused with the browse cursor (which
    # carries score None) — `_is_after_cursor` distinguishes the two by None-ness.
    scored = [(e, s) for e, s in scored if s > 0]
    scored.sort(key=lambda x: (-x[1], x[0].api_id))
    return [(e, s) for e, s in scored]


@dataclass(frozen=True)
class EntryPage:
    """A keyset page over the in-memory catalog list."""

    items: list[ManifestEntry]
    has_more: bool
    next_api_id: str | None
    next_score: int | None


def paginate_entries(
    scored: list[tuple[ManifestEntry, int | None]],
    *,
    after_api_id: str | None,
    after_score: float | None,
    limit: int,
) -> EntryPage:
    """Slice an ordered ``(entry, score)`` list into a keyset page.

    ``scored`` must already be in the final display order (see ``score_entries``).
    The cursor is the ``(score, api_id)`` of the last item returned; the next
    request resumes at the first item strictly after it in that same order. This
    is an in-memory slice — the whole catalog is a ~1 MB cached blob — so there
    is no DB cost to paging.

    ``after_score`` is decoded from a client cursor and typed ``float`` so any
    historical/foreign cursor value round-trips; our own scores are integers, and
    ``int``/``float`` compare exactly for these small values in ``_is_after_cursor``.
    """
    if after_api_id is not None:
        start = 0
        for i, (entry, score) in enumerate(scored):
            if _is_after_cursor(score, entry.api_id, after_score, after_api_id):
                start = i
                break
        else:
            start = len(scored)
    else:
        start = 0

    if limit <= 0:
        # An empty window is terminal: there is no usable cursor for "give me
        # nothing", so never advertise more (would strand the caller with
        # has_more=True / next_cursor=None). The router forbids limit<1 anyway;
        # this guards non-router callers.
        return EntryPage(items=[], has_more=False, next_api_id=None, next_score=None)
    window = scored[start : start + limit]
    has_more = start + len(window) < len(scored)
    if window and has_more:
        last_entry, last_score = window[-1]
        next_api_id: str | None = last_entry.api_id
        next_score = last_score
    else:
        next_api_id = None
        next_score = None
    return EntryPage(
        items=[e for e, _ in window],
        has_more=has_more,
        next_api_id=next_api_id,
        next_score=next_score,
    )


def _is_after_cursor(
    score: int | None, api_id: str, cursor_score: float | None, cursor_api_id: str
) -> bool:
    """Whether ``(score, api_id)`` sorts strictly after the cursor position.

    Browse order is ascending ``api_id``. Search order is descending ``score``
    then ascending ``api_id`` — so "after" means lower score, or equal score and
    a later ``api_id``.
    """
    if score is None or cursor_score is None:
        return api_id > cursor_api_id
    if score != cursor_score:
        return score < cursor_score
    return api_id > cursor_api_id


def is_registered(entry: ManifestEntry, registered_spec_urls: set[str]) -> bool:
    """Whether a catalog entry is already imported into the local registry.

    Coverage is keyed on the entry's ``spec_url`` matching the ``source_url`` of a
    local API revision — an exact, reliable join. Vendor/domain matching is
    deliberately *not* used: jentic-one's local identity is the slugified spec
    triple ``(vendor, name, version)``, which does not map back to a catalog
    domain id, so any domain-based guess produces both false positives (one
    ``googleapis.com/*`` import hiding every sibling) and false negatives.
    """
    return entry.spec_url is not None and entry.spec_url in registered_spec_urls


def filter_unregistered(
    entries: list[ManifestEntry], registered_spec_urls: set[str]
) -> list[ManifestEntry]:
    """Drop catalog entries already imported locally (by spec_url match)."""
    return [e for e in entries if not is_registered(e, registered_spec_urls)]


# ── preview projectors (pure) ────────────────────────────────────────────────


@dataclass(frozen=True)
class PreviewParameter:
    """A slimmed OpenAPI parameter for the preview operation list."""

    name: str
    location: str  # OpenAPI `in`
    required: bool
    description: str


@dataclass(frozen=True)
class PreviewOperation:
    """A slimmed OpenAPI operation for the preview operation list."""

    method: str
    path: str
    summary: str
    description: str
    operation_id: str | None
    parameters: list[PreviewParameter]
    security: list[str]
    tags: list[str]


@dataclass(frozen=True)
class PreviewInfo:
    """The `info` block fields the preview surfaces."""

    title: str | None
    version: str | None
    description: str | None


@dataclass
class PreviewProjection:
    """Full pure projection of a spec doc for preview (pre-pagination)."""

    operations: list[PreviewOperation]
    info: PreviewInfo
    security_schemes: dict[str, dict[str, Any]] = field(default_factory=dict)


def resolve_local_ref(doc: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Resolve a local JSON Pointer ref (``#/components/...``); None otherwise."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: object = doc
    for seg in ref[2:].split("/"):
        seg = seg.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node if isinstance(node, dict) else None


def project_parameter(doc: dict[str, Any], param: dict[str, Any]) -> PreviewParameter | None:
    """Slim a parameter to the fields the UI renders; None when malformed."""
    if "$ref" in param:
        resolved = resolve_local_ref(doc, param["$ref"])
        if resolved is None:
            return None
        param = resolved
    name = param.get("name")
    location = param.get("in")
    if not name or not location:
        return None
    return PreviewParameter(
        name=name,
        location=location,
        required=bool(param.get("required", False)),
        description=param.get("description") or "",
    )


def flatten_security(security_raw: object) -> list[str]:
    """Flatten OpenAPI per-op `security` (disjunction of conjunctions) to names."""
    if not isinstance(security_raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for entry in security_raw:
        if not isinstance(entry, dict):
            continue
        for name in entry:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def slim_security_schemes(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Project `components.securitySchemes` to the fields the UI renders."""
    raw = (doc.get("components") or {}).get("securitySchemes") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, scheme in raw.items():
        if not isinstance(scheme, dict):
            continue
        slim: dict[str, Any] = {
            "type": scheme.get("type"),
            "description": scheme.get("description") or "",
        }
        scheme_type = scheme.get("type")
        if scheme_type == "apiKey":
            slim["in"] = scheme.get("in")
            slim["name"] = scheme.get("name")
        elif scheme_type == "http":
            slim["scheme"] = scheme.get("scheme")
            slim["bearerFormat"] = scheme.get("bearerFormat")
        elif scheme_type == "oauth2":
            flows = scheme.get("flows") or {}
            slim["flows"] = list(flows.keys()) if isinstance(flows, dict) else []
        elif scheme_type == "openIdConnect":
            slim["openIdConnectUrl"] = scheme.get("openIdConnectUrl")
        out[name] = slim
    return out


def parse_preview_operations(
    doc: dict[str, Any], *, tag: str | None = None, q: str | None = None
) -> list[PreviewOperation]:
    """Extract a UI-friendly operation list from an OpenAPI doc.

    Path-level + op-level parameters are merged (op overrides path on the same
    ``(name, in)`` key). Op security overrides doc security entirely; absence
    inherits doc security. Optional ``tag`` is a case-insensitive substring filter
    on ``op.tags[]``; optional ``q`` is a case-insensitive substring filter over
    method + path + summary + operationId. Both are applied before counting, so a
    caller paginating the result sees a total that reflects the filtered set.
    """
    ops: list[PreviewOperation] = []
    doc_security = flatten_security(doc.get("security"))
    tag_lower = tag.lower() if tag else None
    q_lower = q.strip().lower() if q and q.strip() else None
    for path, methods in (doc.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        path_params: list[PreviewParameter] = []
        for raw_param in methods.get("parameters") or []:
            if isinstance(raw_param, dict):
                proj = project_parameter(doc, raw_param)
                if proj is not None:
                    path_params.append(proj)
        for method, op in methods.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            op_params: list[PreviewParameter] = []
            for raw_param in op.get("parameters") or []:
                if isinstance(raw_param, dict):
                    proj = project_parameter(doc, raw_param)
                    if proj is not None:
                        op_params.append(proj)
            op_keys = {(p.name, p.location) for p in op_params}
            merged = op_params + [p for p in path_params if (p.name, p.location) not in op_keys]
            op_security_raw = op.get("security")
            # Op security overrides doc security entirely; absence inherits it.
            # The same op-else-doc resolution is applied at ingest in
            # OpenAPIOperationParser (issue #772) — keep the two aligned.
            security = (
                flatten_security(op_security_raw) if op_security_raw is not None else doc_security
            )
            raw_tags = op.get("tags") or []
            op_tags = [t for t in raw_tags if isinstance(t, str)]
            if tag_lower is not None and not any(tag_lower in t.lower() for t in op_tags):
                continue
            # Coerce to str up front: a malformed upstream spec can carry a
            # non-string summary/operationId (e.g. a number or object), which
            # would crash the q-filter join below and surface as an uncaught 500.
            raw_summary = op.get("summary")
            summary = raw_summary if isinstance(raw_summary, str) else ""
            raw_operation_id = op.get("operationId")
            operation_id = raw_operation_id if isinstance(raw_operation_id, str) else None
            if q_lower is not None:
                haystack = " ".join([method.upper(), path, summary, operation_id or ""]).lower()
                if q_lower not in haystack:
                    continue
            ops.append(
                PreviewOperation(
                    method=method.upper(),
                    path=path,
                    summary=summary,
                    description=op.get("description") or "",
                    operation_id=operation_id,
                    parameters=merged,
                    security=security,
                    tags=op_tags,
                )
            )
    return ops


def project_preview(
    doc: dict[str, Any], *, tag: str | None = None, q: str | None = None
) -> PreviewProjection:
    """Full preview projection: operations + info + slimmed security schemes."""
    info_raw = doc.get("info") or {}
    return PreviewProjection(
        operations=parse_preview_operations(doc, tag=tag, q=q),
        info=PreviewInfo(
            title=info_raw.get("title"),
            version=info_raw.get("version"),
            description=info_raw.get("description"),
        ),
        security_schemes=slim_security_schemes(doc),
    )
