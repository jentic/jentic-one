"""Fetch layer — loads an IngestSpecification from a URL or inline content."""

import hashlib
import json
import math
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel, Field

from jentic_one.registry.ingest.api_identifier import resolve_api_identifier
from jentic_one.registry.ingest.errors import IngestStageError, RetryableFetchError
from jentic_one.registry.ingest.models import IngestSpecification, SpecType
from jentic_one.shared.bounded_read import (
    ResponseTooLargeError,
    UndecodableBodyError,
    decode_body,
    follow_redirects,
    read_capped_body,
)
from jentic_one.shared.config import IngestConfig
from jentic_one.shared.egress import build_pinned_transport
from jentic_one.shared.models import ApiRevisionSourceType
from jentic_one.shared.url_validation import validate_upstream_url_async


class InlineSource(BaseModel):
    """Inline source carrying raw spec content."""

    type: Literal["inline"]
    content: str
    filename: str
    vendor: str | None = None
    api_name: str | None = None
    version: str | None = None
    submitted_by: str | None = None
    origin: str | None = None
    #: Optional provenance URL for inline content. Normally ``None`` (a genuine
    #: paste has no URL), but overlay materialization re-ingests the base spec's
    #: bytes inline and must carry the base ``source_url`` forward, or catalog
    #: "registered" detection and the Flow-3 update-notify sweep lose the API.
    source_url: str | None = None
    #: Catalog identity slug, carried forward on re-ingests of a
    #: catalog-originated spec (e.g. overlay materialization). ``None`` for
    #: genuine pastes.
    catalog_api_id: str | None = None
    #: Overlay-only: the base revision's ``spec_digest`` this overlay is materialized
    #: over. Propagated onto the resulting revision so the Flow-3 sweep diffs upstream
    #: against the overlay's base rather than the overlaid digest. ``None`` otherwise.
    overlay_base_digest: str | None = None
    #: Authorized-supersede flag (A4b): a catalog re-import allowed to replace a live
    #: confirmed overlay. Set only by the scope-checked enqueue path.
    supersede_active: bool = False
    #: Operator approval for a server-host change (``credentials:write``): lets a
    #: catalog re-import that changes the API's server hosts become current even
    #: when the API has bound credentials. Server-set only, by the scope-checked
    #: ``CatalogService.import_entry``; no client ingest schema exposes it.
    host_change_approved: bool = False
    #: Overlay-only: the id of the overlay being (re-)materialized. Propagated to the
    #: ingest spec so ``CreateRevisionStage`` can distinguish a re-materialize of the same
    #: overlay (keep the clean-base ``superseded_revision_id``) from a stacked confirm of a
    #: different overlay over a live overlay's output (capture the current revision). Set
    #: only by the confirm/re-materialize enqueue path. ``None`` for non-overlay sources.
    overlay_id: str | None = None


class UrlSource(BaseModel):
    """URL source pointing at a fetchable spec."""

    type: Literal["url"]
    url: str
    vendor: str | None = None
    api_name: str | None = None
    version: str | None = None
    submitted_by: str | None = None
    origin: str | None = None
    #: Catalog identity slug (`domain[/sub-api]`) for catalog-originated
    #: imports. Persisted verbatim on the Api row (the `api_name` copy of the
    #: same slug gets slugified and loses the separable structure).
    catalog_api_id: str | None = None
    #: Authorized-supersede flag (A4b): a catalog re-import allowed to replace a live
    #: confirmed overlay (the current revision is then overlay-origin, so the stage must
    #: archive every active revision). Set only by the scope-checked enqueue path.
    supersede_active: bool = False
    #: Operator approval for a server-host change (``credentials:write``): lets a
    #: catalog re-import that changes the API's server hosts become current even
    #: when the API has bound credentials. Server-set only, by the scope-checked
    #: ``CatalogService.import_entry``; no client ingest schema exposes it.
    host_change_approved: bool = False


IngestSource = Annotated[UrlSource | InlineSource, Field(discriminator="type")]


#: Upper bound on the fully alias-expanded node count of a YAML spec. Stripe's
#: OpenAPI spec (~6.6 MB YAML) composes to ~280k nodes (~24 bytes/node), so a
#: spec at the default 25 MB ``max_spec_bytes`` cap is on the order of 1.1M
#: nodes. 2.5M leaves ~2x headroom over that for legitimate anchor reuse while
#: keeping the expanded document — what every downstream stage materializes —
#: within the same order of magnitude as the largest alias-free spec accepted.
#: Only enforced when the document actually contains an alias.
MAX_YAML_EXPANDED_NODES = 2_500_000

#: Upper bound on the alias-expanded nesting depth (levels of the expanded tree,
#: root and leaf scalars included) of a YAML spec. Literal nesting is already
#: bounded by PyYAML's recursive composer: at the default recursion limit a
#: document nested ~326 collections deep raises ``RecursionError``, which
#: ``parse_spec_content`` reports as a parse failure. An alias chain (``&a1 [*a0]``,
#: ``&a2 [*a1]``, …) composes iteratively from shallow source text, so without
#: this cap it builds an arbitrarily deep tree that recursive walkers downstream
#: (``json.dumps``, validation, ``$ref`` resolution) then overflow on. Real
#: OpenAPI documents nest a few dozen levels at most; 256 sits below the literal
#: ceiling, so aliases cannot produce a shape literal YAML would reject.
MAX_YAML_EXPANDED_DEPTH = 256


class YamlAliasExpansionError(yaml.YAMLError):
    """Raised when a YAML document's alias expansion exceeds the node budget."""


def _node_children(node: yaml.Node) -> list[yaml.Node]:
    if isinstance(node, yaml.SequenceNode):
        return list(node.value)
    if isinstance(node, yaml.MappingNode):
        return [child for pair in node.value for child in pair]
    return []


def _check_alias_expansion(root: yaml.Node, *, max_nodes: int, max_depth: int) -> None:
    """Raise ``YamlAliasExpansionError`` if ``root`` expands past either budget.

    Iterative post-order walk over the composed node graph, memoizing each
    shared (anchored) node's expanded size and height so the cost is linear in
    the number of distinct nodes, not in the expanded size — a bomb is rejected
    as soon as any subtree crosses ``max_nodes`` or ``max_depth``.
    """
    sizes: dict[int, int] = {}
    heights: dict[int, int] = {}
    in_progress: set[int] = set()
    stack: list[tuple[yaml.Node, bool]] = [(root, False)]
    while stack:
        node, children_done = stack.pop()
        key = id(node)
        if children_done:
            in_progress.discard(key)
            children = _node_children(node)
            total = 1 + sum(sizes[id(child)] for child in children)
            if total > max_nodes:
                raise YamlAliasExpansionError(
                    f"YAML alias expansion exceeds the limit of {max_nodes} nodes"
                )
            height = 1 + max((heights[id(child)] for child in children), default=0)
            if height > max_depth:
                raise YamlAliasExpansionError(
                    f"YAML alias expansion exceeds the nesting limit of {max_depth} levels"
                )
            sizes[key] = total
            heights[key] = height
            continue
        if key in sizes:
            continue
        if key in in_progress:
            raise YamlAliasExpansionError("YAML document contains a recursive alias")
        in_progress.add(key)
        stack.append((node, True))
        stack.extend((child, False) for child in _node_children(node) if id(child) not in sizes)


class _JsonSafeLoader(yaml.SafeLoader):
    """SafeLoader whose output contains only JSON-serializable values.

    Real-world YAML specs are full of unquoted ISO dates (``version: 2022-01-16``,
    changelog entries, example values). The stock ``SafeLoader`` resolves those
    to ``datetime.date``/``datetime.datetime``, which the ingest pipeline's later
    JSON serialization (JSONB spec storage, operation extraction) rejects —
    dead-lettering the import (issue #979). Spec documents must stay
    JSON-serializable (the contract stated on ``IngestSpecification.content``),
    so the two tags that produce non-JSON scalars — ``!!timestamp`` and
    ``!!binary`` — construct the scalar's verbatim text (lossless, and identical
    to what the same spec yields when served as JSON), and ``!!set`` constructs
    a list of its keys. Non-finite ``!!float`` scalars (``.nan``/``.inf``) also
    fall back to verbatim text (issue #984): ``json.dumps`` emits them as the
    non-standard ``NaN``/``Infinity`` tokens, which JSON parsers and Postgres
    JSONB reject — while finite floats keep parsing as numbers. The remaining
    exotic tags (``!!omap``/``!!pairs``) yield lists of tuples, which
    JSON-serialize as arrays already.

    Anchors and aliases are allowed, but their expansion is bounded (#1436).
    PyYAML shares one node per anchor, so loading itself stays small; every
    downstream consumer (``json.dumps``, validation, operation extraction)
    walks the *expanded* tree, so a few hundred bytes of nested aliases (a
    "billion laughs" document) slips under the byte-size cap and still
    exhausts worker memory. When a document uses aliases, the composed node
    graph's fully expanded size and depth are counted before construction and
    the load fails with ``YamlAliasExpansionError`` past
    ``MAX_YAML_EXPANDED_NODES`` / ``MAX_YAML_EXPANDED_DEPTH``.
    Recursive aliases are rejected outright: they cannot be JSON-serialized.
    """

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._saw_alias = False

    def compose_node(self, parent: yaml.Node | None, index: Any) -> yaml.Node | None:
        if self.check_event(yaml.AliasEvent):
            self._saw_alias = True
        return super().compose_node(parent, index)

    def get_single_node(self) -> yaml.Node | None:
        node = super().get_single_node()
        if node is not None and self._saw_alias:
            _check_alias_expansion(
                node, max_nodes=MAX_YAML_EXPANDED_NODES, max_depth=MAX_YAML_EXPANDED_DEPTH
            )
        return node


def _construct_scalar_as_str(loader: _JsonSafeLoader, node: yaml.ScalarNode) -> str:
    return loader.construct_scalar(node)


def _construct_set_as_list(loader: _JsonSafeLoader, node: yaml.MappingNode) -> list[Any]:
    return list(loader.construct_mapping(node))


def _construct_json_safe_float(loader: _JsonSafeLoader, node: yaml.ScalarNode) -> float | str:
    value = yaml.SafeLoader.construct_yaml_float(loader, node)
    if math.isfinite(value):
        return value
    return loader.construct_scalar(node)


# !!timestamp and !!float are the tags here with implicit resolvers (bare
# scalars); !!binary and !!set require an explicit tag but dead-letter
# identically.
_JsonSafeLoader.add_constructor("tag:yaml.org,2002:timestamp", _construct_scalar_as_str)
_JsonSafeLoader.add_constructor("tag:yaml.org,2002:binary", _construct_scalar_as_str)
_JsonSafeLoader.add_constructor("tag:yaml.org,2002:set", _construct_set_as_list)
_JsonSafeLoader.add_constructor("tag:yaml.org,2002:float", _construct_json_safe_float)


def _load_yaml(raw: str) -> Any:
    """``yaml.safe_load`` constrained to JSON-serializable output (see _JsonSafeLoader).

    The loader is a ``SafeLoader`` subclass, so this is exactly as safe as
    ``yaml.safe_load`` — hence the B506 suppression.

    PyYAML's stock scalar constructors leak raw builtin exceptions on
    malformed explicitly-tagged scalars instead of raising ``YAMLError``
    (issue #988): ``!!float abc`` -> ``ValueError``, ``!!int ''`` ->
    ``IndexError``, ``!!bool abc`` -> ``KeyError`` — and the escape set is
    PyYAML-version-dependent. Normalize every constructor escape to
    ``yaml.YAMLError`` so ``parse_spec_content``'s handlers wrap it into a
    clean ``IngestStageError`` instead of dead-lettering with an internal
    traceback.
    """
    try:
        return yaml.load(raw, Loader=_JsonSafeLoader)  # nosec B506
    except yaml.YAMLError:
        raise
    except Exception as exc:
        raise yaml.YAMLError(f"YAML document construction failed: {exc}") from exc


def _load_json(raw: str) -> Any:
    """``json.loads`` with non-finite float tokens kept as their literal text.

    Python's ``json.loads`` accepts the non-standard ``NaN``/``Infinity``/
    ``-Infinity`` tokens by default and produces non-finite floats — the same
    JSONB-rejected values the YAML loader guards against (issue #984). Keep
    the token text verbatim, mirroring ``_JsonSafeLoader``.

    Escapes that aren't ``ValueError`` (e.g. ``RecursionError`` on a deeply
    nested document) are normalized to it, symmetric with ``_load_yaml``, so
    both ``parse_spec_content`` call sites produce a clean parse error
    (``json.JSONDecodeError`` is a ``ValueError`` subclass).
    """
    try:
        return json.loads(raw, parse_constant=str)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"JSON document construction failed: {exc}") from exc


def parse_spec_content(raw: str, *, filename: str | None = None) -> dict[str, Any]:
    """Parse raw spec content as JSON or YAML, returning a dict.

    This is the single boundary where raw spec text becomes a document, and it
    guarantees the result is JSON-serializable regardless of source format —
    downstream stages (JSONB writes, ``json.dumps``) rely on that invariant.
    """
    if not raw or not raw.strip():
        raise IngestStageError("spec content is empty")

    json_first = raw.lstrip().startswith("{")
    if filename:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext == "json":
            json_first = True
        elif ext in ("yaml", "yml"):
            json_first = False

    parsed: Any = None
    if json_first:
        try:
            parsed = _load_json(raw)
        except (json.JSONDecodeError, ValueError):
            try:
                parsed = _load_yaml(raw)
            except YamlAliasExpansionError as exc:
                raise IngestStageError(str(exc)) from exc
            except yaml.YAMLError as exc:
                raise IngestStageError("failed to parse spec content as JSON or YAML") from exc
    else:
        try:
            parsed = _load_yaml(raw)
        except YamlAliasExpansionError as exc:
            # Aliases are YAML-only syntax, so a JSON fallback cannot succeed.
            raise IngestStageError(str(exc)) from exc
        except yaml.YAMLError:
            try:
                parsed = _load_json(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise IngestStageError("failed to parse spec content as JSON or YAML") from exc

    if not isinstance(parsed, dict):
        raise IngestStageError("spec content must be a mapping (object), not a scalar or list")

    if "arazzo" in parsed:
        raise IngestStageError("arazzo specifications are not supported")

    return parsed


_DEFAULT_INGEST_CONFIG = IngestConfig()


#: Upstream statuses that signal a temporary condition: request timeout, too
#: early, and rate limiting. Every 5xx is treated the same way.
_TRANSIENT_CLIENT_STATUSES = frozenset({408, 425, 429})

#: 5xx codes that describe a permanent property of the server rather than a
#: passing condition, so re-fetching the same URL gets the same answer. Retrying
#: them only spends the whole backoff ladder before dead-lettering.
_PERMANENT_SERVER_STATUSES = frozenset({501, 505, 510})


def _is_transient_status(status_code: int) -> bool:
    """True when a later fetch of the same URL may get a different answer."""
    if status_code in _PERMANENT_SERVER_STATUSES:
        return False
    return status_code >= 500 or status_code in _TRANSIENT_CLIENT_STATUSES


async def load_specification(
    source: UrlSource | InlineSource,
    *,
    config: IngestConfig | None = None,
) -> IngestSpecification:
    """Load and parse a spec from a URL or inline content into an IngestSpecification.

    Raises :class:`RetryableFetchError` when reaching a URL fails for a reason a
    later attempt may not repeat, and :class:`IngestStageError` for every other
    failure (a refused URL, an unusable response, unparseable content, or an
    unresolvable API identity).
    """
    cfg = config or _DEFAULT_INGEST_CONFIG
    max_bytes = cfg.max_spec_bytes
    size_limit_label = f"{max_bytes / (1024 * 1024):.1f} MB"

    if isinstance(source, InlineSource):
        # Inline content bypasses the URL-fetch size checks below, so enforce the
        # same cap here — otherwise an oversized inline import (e.g. a materialized
        # overlay whose document blew up the spec) can exhaust memory and produce
        # oversized DB rows.
        if len(source.content.encode()) > max_bytes:
            raise IngestStageError(f"inline content exceeds {size_limit_label} size limit")
        content = parse_spec_content(source.content, filename=source.filename)
        sha = hashlib.sha256(source.content.encode()).hexdigest()
        source_type = ApiRevisionSourceType.INLINE
        source_url = source.source_url
        retrieval_url: str | None = None
        source_filename: str | None = source.filename
    else:
        try:
            validated_url = await validate_upstream_url_async(source.url, cfg.egress)
        except ValueError as exc:
            raise IngestStageError(f"unsafe URL rejected: {exc}") from exc

        try:
            async with httpx.AsyncClient(
                timeout=cfg.fetch_timeout_s,
                follow_redirects=False,
                transport=build_pinned_transport(cfg.egress),
            ) as client:
                # Relative servers resolve against where the document was
                # retrieved, so keep the URL the redirect chain ended on.
                resp, validated_url = await follow_redirects(
                    client,
                    validated_url,
                    headers=None,
                    egress=cfg.egress,
                    max_redirects=cfg.max_redirects,
                    on_unsafe=lambda exc: IngestStageError(f"unsafe URL rejected: {exc}"),
                    on_too_many=lambda: IngestStageError("too many redirects"),
                )
                try:
                    if resp.status_code < 200 or resp.status_code >= 300:
                        message = f"non-success status {resp.status_code} fetching {source.url}"
                        if _is_transient_status(resp.status_code):
                            raise RetryableFetchError(message)
                        raise IngestStageError(message)
                    body = await read_capped_body(resp, max_bytes)
                finally:
                    await resp.aclose()
        except IngestStageError:
            raise
        except ResponseTooLargeError as exc:
            raise IngestStageError(f"response exceeds {size_limit_label} size limit") from exc
        except UndecodableBodyError as exc:
            raise IngestStageError(f"failed to fetch URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RetryableFetchError(f"failed to fetch URL: {exc}") from exc

        url_path = urlparse(source.url).path
        url_filename = url_path.rsplit("/", 1)[-1] if "/" in url_path else None

        content = parse_spec_content(decode_body(resp, body), filename=url_filename)
        sha = hashlib.sha256(body).hexdigest()
        source_type = ApiRevisionSourceType.URL
        source_url = source.url
        retrieval_url = validated_url
        source_filename = None

    api_identifier = resolve_api_identifier(
        content,
        vendor=source.vendor,
        name=source.api_name,
        version=source.version,
    )

    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=api_identifier,
        sha=sha,
        metadata=content.get("info"),
        content=content,
        source_type=source_type,
        source_url=source_url,
        retrieval_url=retrieval_url,
        source_filename=source_filename,
        submitted_by=source.submitted_by,
        origin=source.origin,
        catalog_api_id=source.catalog_api_id,
        overlay_base_digest=getattr(source, "overlay_base_digest", None),
        supersede_active=source.supersede_active,
        host_change_approved=source.host_change_approved,
        overlay_id=getattr(source, "overlay_id", None),
    )
