"""The ``http-index`` source kind: specs fetched by URL, listed by an index or by config.

``settings`` holds exactly one of:

- ``index_url``: an `APIs.json <https://apisjson.org>`_-style index. Each item of
  its ``apis`` array is one entry: ``aid`` is its ``external_id`` (an item with
  no usable ``aid`` is skipped), the first ``properties`` item whose ``type`` names
  an OpenAPI or Swagger document gives the spec URL (resolved against the index
  URL), and ``name``, ``version``, ``humanURL`` and ``tags`` fill the card. An
  optional ``x-catalog-api-id`` is the ``catalog_api_id`` hint;
- ``specs``: a static list of ``{external_id, spec_url, catalog_api_id?, title?, tags?}``.

Listings are always full (no delta listing); spec fetches are conditional on the
last ``ETag``. The source's credentials go only to origins its configuration
names (the index URL's, or each configured spec URL's, plus ``auth.origins``): a
spec URL an index lists on any other host is fetched without them. A spec URL
carrying userinfo (``user:pass@``) is never listed or fetched, and a redirect
to one is refused: the HTTP client would turn it into its own ``Authorization``
header, letting the index or the redirecting host choose it. An ``index_url``
listing more than ``max_entries`` entries (default ``DEFAULT_HTTP_INDEX_ENTRIES``, at most
``MAX_HTTP_INDEX_ENTRIES``) fails the sync run with ``listing_too_large``, so the
stored entries stay as they were; ``max_entries`` does not apply to a static
``specs`` list, which holds at most ``MAX_HTTP_INDEX_ENTRIES`` items.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar
from urllib.parse import urljoin

import structlog
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from jentic_one.registry.services.catalog.fetch import has_userinfo
from jentic_one.registry.services.catalog.sources.deps import SourceDeps
from jentic_one.registry.services.catalog.sources.origins import url_origin
from jentic_one.registry.services.catalog.sources.protocol import (
    EntryChange,
    JsonScalar,
    ListingTooLargeError,
    ListStream,
    SourceCapabilities,
    SourceEntry,
    SourceHealth,
    SourceUnavailableError,
    SpecFetch,
    SpecGoneError,
    Upserted,
)
from jentic_one.registry.services.catalog.sources.registry import register_catalog_source_kind
from jentic_one.registry.services.catalog.sources.streams import FullListStream
from jentic_one.shared.config import CatalogSourceConfig

logger = structlog.get_logger(__name__)

#: ``properties[].type`` values (lower-cased) that name a spec document.
_SPEC_PROPERTY_TYPES = frozenset({"openapi", "swagger", "x-openapi", "x-openapi-spec", "x-swagger"})

#: Most entries one source lists unless its ``max_entries`` says otherwise.
DEFAULT_HTTP_INDEX_ENTRIES = 10_000
#: The highest ``max_entries`` a source may set.
MAX_HTTP_INDEX_ENTRIES = 100_000


def _no_userinfo(url: HttpUrl | None, field: str) -> HttpUrl | None:
    if url is not None and has_userinfo(str(url)):
        # Never echo the URL: it carries a secret.
        raise ValueError(f"{field} must not carry credentials (user:pass@); use auth instead")
    return url


class HttpIndexSpec(BaseModel):
    """One statically configured spec of an ``http-index`` source."""

    # A URL with userinfo would carry a secret: keep inputs out of errors.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    external_id: str = Field(min_length=1, max_length=287)
    spec_url: HttpUrl
    catalog_api_id: str | None = Field(
        default=None, description="catalog_api_id hint; resolved like any other."
    )
    title: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("spec_url")
    @classmethod
    def _spec_url_no_userinfo(cls, value: HttpUrl) -> HttpUrl:
        _no_userinfo(value, "spec_url")
        return value


class HttpIndexSettings(BaseModel):
    """``settings`` of an ``http-index`` source: ``index_url`` or ``specs``, not both."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    index_url: HttpUrl | None = None
    specs: list[HttpIndexSpec] = Field(default_factory=list, max_length=MAX_HTTP_INDEX_ENTRIES)
    max_entries: int = Field(
        default=DEFAULT_HTTP_INDEX_ENTRIES,
        ge=1,
        le=MAX_HTTP_INDEX_ENTRIES,
        description=(
            "Most entries index_url may list; a longer index fails the sync run "
            "(listing_too_large) and the stored entries stay. A static specs list "
            "is not capped by it (at most MAX_HTTP_INDEX_ENTRIES items)."
        ),
    )

    @field_validator("index_url")
    @classmethod
    def _index_url_no_userinfo(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _no_userinfo(value, "index_url")

    @model_validator(mode="after")
    def _exactly_one(self) -> HttpIndexSettings:
        if (self.index_url is None) == (not self.specs):
            raise ValueError("set exactly one of index_url or specs")
        ids = [s.external_id for s in self.specs]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"specs: duplicate external_id {', '.join(duplicates)}")
        return self


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _spec_url(item: Mapping[str, Any], base: str) -> str | None:
    for prop in item.get("properties") or []:
        if not isinstance(prop, Mapping):
            continue
        kind = prop.get("type")
        url = prop.get("url")
        if isinstance(kind, str) and kind.lower() in _SPEC_PROPERTY_TYPES and isinstance(url, str):
            absolute = urljoin(base, url.strip())
            if url_origin(absolute) is not None and not has_userinfo(absolute):
                return absolute
    return None


def index_changes(
    doc: Mapping[str, Any], *, index_url: str, max_entries: int = DEFAULT_HTTP_INDEX_ENTRIES
) -> list[EntryChange]:
    """The entries of an APIs.json-style index document, as listing changes.

    A spec URL that is not http(s) or carries userinfo leaves its entry card-only.
    """
    changes: list[EntryChange] = []
    seen: set[str] = set()
    apis = doc.get("apis")
    for item in apis if isinstance(apis, list) else []:
        if not isinstance(item, Mapping):
            continue
        external_id = _str_or_none(item.get("aid"))
        if external_id is None or external_id in seen:
            continue
        seen.add(external_id)
        if len(seen) > max_entries:
            # A full listing that silently dropped the tail would remove those
            # entries on every sync; fail the run instead so the rows stay.
            logger.warning("catalog_http_index_too_large", index_url=index_url, limit=max_entries)
            raise ListingTooLargeError(f"index lists more than {max_entries} entries")
        spec_url = _spec_url(item, index_url)
        tags = item.get("tags")
        changes.append(
            Upserted(
                SourceEntry(
                    external_id=external_id,
                    catalog_api_id_hint=_str_or_none(item.get("x-catalog-api-id")),
                    title=_str_or_none(item.get("name")),
                    version=_str_or_none(item.get("version")),
                    spec_ref={"url": spec_url} if spec_url else None,
                    human_url=_str_or_none(item.get("humanURL")),
                    tags=tuple(t for t in tags if isinstance(t, str))
                    if isinstance(tags, list)
                    else (),
                )
            )
        )
    return changes


def static_changes(specs: Sequence[HttpIndexSpec]) -> list[EntryChange]:
    """The configured static specs, as listing changes."""
    return [
        Upserted(
            SourceEntry(
                external_id=spec.external_id,
                catalog_api_id_hint=spec.catalog_api_id,
                title=spec.title,
                version=None,
                spec_ref={"url": str(spec.spec_url)},
                human_url=None,
                tags=tuple(spec.tags),
            )
        )
        for spec in specs
    ]


def _watermark(changes: Sequence[EntryChange]) -> str:
    digest = hashlib.sha256()
    for change in changes:
        if isinstance(change, Upserted):
            digest.update(change.entry.external_id.encode())
            digest.update(b"\0")
            digest.update(str((change.entry.spec_ref or {}).get("url", "")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


@register_catalog_source_kind
class HttpIndexSource:
    """Lists an APIs.json-style index or a static spec list; fetches specs by URL."""

    kind: ClassVar[str] = "http-index"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities.CONDITIONAL_FETCH
    config_model: ClassVar[type[BaseModel]] = HttpIndexSettings

    def __init__(self, settings: HttpIndexSettings, *, deps: SourceDeps) -> None:
        self._settings = settings
        self._deps = deps
        self._index_url = str(settings.index_url) if settings.index_url is not None else None

    @classmethod
    def credential_urls(cls, settings: BaseModel) -> list[str]:
        """The configured index URL, or every static spec URL (never index-listed ones)."""
        assert isinstance(settings, HttpIndexSettings)
        if settings.index_url is not None:
            return [str(settings.index_url)]
        return [str(s.spec_url) for s in settings.specs]

    @classmethod
    def build(cls, cfg: CatalogSourceConfig, *, deps: SourceDeps) -> HttpIndexSource:
        return cls(HttpIndexSettings.model_validate(cfg.settings), deps=deps)

    def list_entries(self, *, since: str | None) -> ListStream:
        del since  # full listings only
        index_url = self._index_url

        async def _load() -> list[EntryChange]:
            if index_url is None:
                return static_changes(self._settings.specs)
            doc = await self._deps.http.get_json(index_url)
            return index_changes(doc, index_url=index_url, max_entries=self._settings.max_entries)

        return FullListStream(_load, watermark=_watermark)

    async def fetch_spec(
        self, ref: Mapping[str, JsonScalar], *, validator: str | None = None
    ) -> SpecFetch:
        url = ref.get("url")
        if not isinstance(url, str) or url_origin(url) is None:
            raise SpecGoneError("http-index spec_ref carries no http(s) url")
        if has_userinfo(url):
            # Stored before userinfo URLs were skipped at listing time.
            raise SpecGoneError("http-index spec url carries credentials (user:pass@)")
        # The source HTTP client sends the credential only to the source's
        # credential origins (``credential_urls`` plus ``auth.origins``), so the
        # connector asks for it on every fetch and lets that one check decide.
        return await self._deps.http.get_bytes(url, validator=validator)

    async def health(self) -> SourceHealth:
        if self._index_url is None:
            return SourceHealth(ok=True, reason=None, checked_at=self._deps.clock())
        try:
            await self._deps.http.get_json(self._index_url)
        except SourceUnavailableError:
            return SourceHealth(ok=False, reason="unreachable", checked_at=self._deps.clock())
        except SpecGoneError:
            return SourceHealth(ok=False, reason="misconfigured", checked_at=self._deps.clock())
        return SourceHealth(ok=True, reason=None, checked_at=self._deps.clock())
