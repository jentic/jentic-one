"""Fetch layer — loads an IngestSpecification from a URL or inline content."""

import hashlib
import json
from typing import Annotated, Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from pydantic import BaseModel, Field

from jentic_one.registry.ingest.api_identifier import resolve_api_identifier
from jentic_one.registry.ingest.exc import IngestStageError
from jentic_one.registry.ingest.models import IngestSpecification, SpecType
from jentic_one.shared.config import IngestConfig
from jentic_one.shared.egress import build_pinned_transport
from jentic_one.shared.models import ApiRevisionSourceType
from jentic_one.shared.url_validation import validate_upstream_url


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


IngestSource = Annotated[UrlSource | InlineSource, Field(discriminator="type")]


def parse_spec_content(raw: str, *, filename: str | None = None) -> dict[str, Any]:
    """Parse raw spec content as JSON or YAML, returning a dict."""
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
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            try:
                parsed = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                raise IngestStageError("failed to parse spec content as JSON or YAML") from exc
    else:
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError:
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise IngestStageError("failed to parse spec content as JSON or YAML") from exc

    if not isinstance(parsed, dict):
        raise IngestStageError("spec content must be a mapping (object), not a scalar or list")

    if "arazzo" in parsed:
        raise IngestStageError("arazzo specifications are not supported")

    return parsed


_DEFAULT_INGEST_CONFIG = IngestConfig()


async def load_specification(
    source: UrlSource | InlineSource,
    *,
    config: IngestConfig | None = None,
) -> IngestSpecification:
    """Load and parse a spec from a URL or inline content into an IngestSpecification."""
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
        source_filename: str | None = source.filename
    else:
        try:
            validated_url = validate_upstream_url(source.url, cfg.egress)
        except ValueError as exc:
            raise IngestStageError(f"unsafe URL rejected: {exc}") from exc

        try:
            async with httpx.AsyncClient(
                timeout=cfg.fetch_timeout_s,
                follow_redirects=False,
                transport=build_pinned_transport(cfg.egress),
            ) as client:
                resp = await client.get(validated_url)
                for _ in range(cfg.max_redirects):
                    if resp.status_code < 300 or resp.status_code >= 400:
                        break
                    location = resp.headers.get("location")
                    if not location:
                        break
                    # Resolve relative Location headers against the current URL.
                    next_url = urljoin(validated_url, location)
                    try:
                        validated_url = validate_upstream_url(next_url, cfg.egress)
                    except ValueError as exc:
                        raise IngestStageError(f"unsafe URL rejected: {exc}") from exc
                    resp = await client.get(validated_url)
                else:
                    if 300 <= resp.status_code < 400:
                        raise IngestStageError("too many redirects")
        except IngestStageError:
            raise
        except httpx.HTTPError as exc:
            raise IngestStageError(f"failed to fetch URL: {exc}") from exc

        if resp.status_code < 200 or resp.status_code >= 300:
            raise IngestStageError(f"non-success status {resp.status_code} fetching {source.url}")

        content_length = resp.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_bytes:
            raise IngestStageError(f"response exceeds {size_limit_label} size limit")

        if len(resp.content) > max_bytes:
            raise IngestStageError(f"response exceeds {size_limit_label} size limit")

        url_path = urlparse(source.url).path
        url_filename = url_path.rsplit("/", 1)[-1] if "/" in url_path else None

        content = parse_spec_content(resp.text, filename=url_filename)
        sha = hashlib.sha256(resp.content).hexdigest()
        source_type = ApiRevisionSourceType.URL
        source_url = source.url
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
        source_filename=source_filename,
        submitted_by=source.submitted_by,
        origin=source.origin,
        catalog_api_id=source.catalog_api_id,
        overlay_base_digest=getattr(source, "overlay_base_digest", None),
        supersede_active=source.supersede_active,
    )
