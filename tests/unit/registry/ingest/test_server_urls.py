"""Tests for relative server URL resolution at ingest (#1241)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from jentic_one.registry.core.server_hosts import is_hostless_server_url
from jentic_one.registry.ingest.exc import (
    SERVER_URL_ERROR_MAX_CHARS,
    IngestStageError,
    RelativeServerUrlError,
    ServerUrlProblem,
)
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.server_urls import (
    DocumentBase,
    ServerResolution,
    document_base,
    resolve_relative_servers,
    resolved_revision_digest,
    server_base_url,
)
from jentic_one.registry.ingest.stages.resolve_servers import ResolveServerUrlsStage
from jentic_one.registry.ingest.stages.validation import ValidateOpenAPISpec
from jentic_one.registry.services.import_service import (
    ALL_SOURCES_FAILED_PREFIX_TEMPLATE,
    SOURCE_FAILURE_PREFIX_TEMPLATE,
)
from jentic_one.shared.config import AppConfig, CatalogConfig, CatalogSourceConfig
from jentic_one.shared.models import ApiRevisionSourceType

PETSTORE_URL = "https://petstore3.swagger.io/api/v3/openapi.json"
PETSTORE = document_base(PETSTORE_URL)
NO_BASE = document_base(None)
RAW_URL = "https://raw.githubusercontent.com/acme/specs/main/openapi.json"
MIRROR_URL = "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/x/openapi.json"
#: The wrapper a single-source import failure rides into ``job.error``.
JOB_ERROR_PREFIX = ALL_SOURCES_FAILED_PREFIX_TEMPLATE.format(
    count=1
) + SOURCE_FAILURE_PREFIX_TEMPLATE.format(index=0)
JOB_ERROR_MAX = 128


def _spec(servers: list[dict[str, Any]] | None, **paths: Any) -> dict[str, Any]:
    content: dict[str, Any] = {
        "openapi": "3.0.4",
        "info": {"title": "Pets", "version": "1.0.0"},
        "paths": paths or {"/pet": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }
    if servers is not None:
        content["servers"] = servers
    return content


def _ctx(
    content: dict[str, Any], config: AppConfig | None = None, **fields: Any
) -> PipelineContext:
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="swagger", name="petstore", version="1.0.0"),
        content=content,
        sha="raw-digest",
        **fields,
    )
    return PipelineContext(session=None, specification=spec, created_by="usr_test", config=config)


def _refusal(servers: list[dict[str, Any]], base: DocumentBase) -> RelativeServerUrlError:
    with pytest.raises(RelativeServerUrlError) as excinfo:
        resolve_relative_servers(_spec(servers), base)
    return excinfo.value


# -- host-less detection -------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "variables", "expected"),
    [
        ("/api/v3", None, True),
        ("api/v3", None, True),
        ("", None, True),
        ("https://api.example.com/v1", None, False),
        ("//api.example.com/v1", None, False),
        ("https://{region}.example.com", {"region": {"default": "us"}}, False),
        ("/api/{version}", {"version": {"default": "v3"}}, True),
        ("{base}/v1", {"base": {"default": "https://api.example.com"}}, False),
        ("{base}/v1", {"base": {"default": "/api"}}, True),
        # No default: the leading variable may name a host — opaque, not host-less.
        ("{base}/v1", None, False),
    ],
)
def test_is_hostless_server_url(url: str, variables: Any, expected: bool) -> None:
    assert is_hostless_server_url(url, variables) is expected


# -- the resolution base -------------------------------------------------------


@pytest.mark.parametrize(
    ("source_url", "expected"),
    [
        (PETSTORE_URL, PETSTORE_URL),
        (
            # pragma: allowlist nextline secret
            "https://user:secret@specs.example.com:8443/v1/openapi.yaml?token=abc#frag",
            "https://specs.example.com:8443/v1/openapi.yaml",
        ),
        ("https://specs.example.com", "https://specs.example.com/"),
        ("https://[::1]:8443/spec.json", "https://[::1]:8443/spec.json"),
        (None, None),
        ("", None),
        ("ftp://specs.example.com/openapi.yaml", None),
        ("/relative/openapi.yaml", None),
    ],
)
def test_server_base_url_drops_credentials_query_and_fragment(
    source_url: str | None, expected: str | None
) -> None:
    assert server_base_url(source_url) == expected


def test_ipv6_base_resolves_with_brackets() -> None:
    content = _spec([{"url": "/v1"}])

    resolve_relative_servers(content, document_base("https://[::1]:8443/spec.json"))

    assert content["servers"] == [{"url": "https://[::1]:8443/v1"}]


@pytest.mark.parametrize(
    "source_url",
    [
        RAW_URL,
        "https://gist.githubusercontent.com/acme/abc/raw/openapi.yaml",
        "https://media.githubusercontent.com/media/acme/specs/main/openapi.json",
        "https://objects.githubusercontent.com/github-production-release-asset/openapi.json",
        "https://raw.github.com/acme/specs/main/openapi.json",
        "https://github.com/acme/specs/blob/main/openapi.json",
        "https://app.swaggerhub.com/apis/acme/pets/1.0.0",
        # Amazon S3: legacy global and regional virtual-host, path-style (both
        # separators), dual-stack, website, and the China partition.
        "https://acme-specs.s3.amazonaws.com/openapi.json",
        "https://acme-specs.s3.eu-west-1.amazonaws.com/openapi.json",
        "https://s3.amazonaws.com/acme-specs/openapi.json",
        "https://s3.eu-west-1.amazonaws.com/acme-specs/openapi.json",
        "https://s3-eu-west-1.amazonaws.com/acme-specs/openapi.json",
        "https://acme-specs.s3.dualstack.us-east-1.amazonaws.com/openapi.json",
        "https://acme-specs.s3-website-us-east-1.amazonaws.com/openapi.json",
        "https://acme-specs.s3.cn-north-1.amazonaws.com.cn/openapi.json",
        # Google Cloud Storage: path-style, virtual-host, and the console host.
        "https://storage.googleapis.com/acme-specs/openapi.json",
        "https://acme-specs.storage.googleapis.com/openapi.json",
        "https://storage.cloud.google.com/acme-specs/openapi.json",
        # Firebase Storage.
        "https://firebasestorage.googleapis.com/v0/b/acme.appspot.com/o/openapi.json",
        "https://acme.appspot.com/openapi.json",
        # Azure Storage.
        "https://acmespecs.blob.core.windows.net/specs/openapi.json",
        "https://acmespecs.z6.web.core.windows.net/openapi.json",
        "https://acmespecs.blob.core.chinacloudapi.cn/specs/openapi.json",
        # CDNs for repository files.
        "https://cdn.jsdelivr.net/gh/acme/specs/openapi.json",
        "https://fastly.jsdelivr.net/gh/acme/specs/openapi.json",
        "https://raw.githack.com/acme/specs/main/openapi.json",
        "https://rawcdn.githack.com/acme/specs/main/openapi.json",
        # File sharing.
        "https://www.dropbox.com/s/abc/openapi.json?dl=1",
        "https://dl.dropboxusercontent.com/s/abc/openapi.json",
        # Static site hosting.
        "https://acme.github.io/specs/openapi.json",
        # Self-hosted GitLab raw and blob paths, on any host.
        "https://git.acme.example/team/specs/-/raw/main/openapi.json",
        "https://git.acme.example/team/specs/-/blob/main/openapi.json",
        # Host matching ignores case, a trailing dot, and the default port.
        "https://ACME-SPECS.S3.EU-WEST-1.AMAZONAWS.COM./openapi.json",
        "https://acme-specs.storage.googleapis.com:443/openapi.json",
    ],
)
def test_content_hosts_are_never_a_base(source_url: str) -> None:
    base = document_base(source_url)

    assert base.url is None
    assert base.problem is ServerUrlProblem.CONTENT_HOST


@pytest.mark.parametrize(
    "source_url",
    [
        # Real API hosts next to the content hosts above.
        "https://s3control.us-east-1.amazonaws.com/openapi.json",
        "https://abc123.execute-api.eu-west-1.amazonaws.com/prod/openapi.json",
        "https://api.dropboxapi.com/openapi.json",
        "https://api.github.com/openapi.json",
        "https://www.googleapis.com/discovery/v1/apis",
        "https://acme.azurewebsites.net/openapi.json",
        # CDNs that commonly front the API itself.
        "https://d111111abcdef8.cloudfront.net/openapi.json",
        "https://acme.netlify.app/openapi.json",
        # ``/raw/`` alone is an ordinary path segment.
        "https://api.acme.example/raw/openapi.json",
    ],
)
def test_api_hosts_beside_content_hosts_are_a_base(source_url: str) -> None:
    assert document_base(source_url).url is not None


def test_extra_content_hosts_are_never_a_base() -> None:
    base = document_base("https://specs.internal/openapi.json", content_hosts=["specs.internal"])

    assert base.problem is ServerUrlProblem.CONTENT_HOST


def test_the_post_redirect_url_is_the_base() -> None:
    base = document_base(
        "https://petstore.example/openapi.json",
        retrieval_url="https://cdn.petstore.example/v3/openapi.json?sig=abc",
    )

    assert base.url == "https://cdn.petstore.example/v3/openapi.json"


def test_a_redirect_onto_a_content_host_is_never_a_base() -> None:
    base = document_base("https://petstore.example/openapi.json", retrieval_url=RAW_URL)

    assert base.problem is ServerUrlProblem.CONTENT_HOST


def test_a_redirect_upgrading_to_https_resolves_to_https() -> None:
    content = _spec([{"url": "/v3"}])
    base = document_base(
        "http://petstore.example/openapi.json",
        retrieval_url="https://petstore.example/openapi.json",
    )

    resolve_relative_servers(content, base)

    assert content["servers"] == [{"url": "https://petstore.example/v3"}]


def test_a_redirect_downgrading_to_http_is_never_a_base() -> None:
    base = document_base(
        "https://petstore.example/openapi.json",
        retrieval_url="http://petstore.example/openapi.json",
    )

    assert base.url is None
    assert base.problem is ServerUrlProblem.INSECURE_REDIRECT


# -- resolution ----------------------------------------------------------------


def test_relative_only_servers_resolve_against_the_document_url() -> None:
    content = _spec([{"url": "/api/v3", "description": "stock petstore"}])

    result = resolve_relative_servers(content, PETSTORE)

    assert content["servers"] == [
        {"url": "https://petstore3.swagger.io/api/v3", "description": "stock petstore"}
    ]
    assert result.resolved == [("/api/v3", "https://petstore3.swagger.io/api/v3")]
    assert result.dropped == []


@pytest.mark.parametrize(
    ("relative", "absolute"),
    [
        ("v2", "https://specs.example.com/api/v2"),
        ("v1.0/pets", "https://specs.example.com/api/v1.0/pets"),
        ("../v2", "https://specs.example.com/v2"),
        ("/", "https://specs.example.com/"),
        # An empty URL is the server root, never the document itself.
        ("", "https://specs.example.com/"),
        # A network-path reference inherits the document's scheme.
        ("//api.example.com/v1", "https://api.example.com/v1"),
    ],
)
def test_document_relative_paths_follow_rfc3986(relative: str, absolute: str) -> None:
    content = _spec([{"url": relative}])

    resolve_relative_servers(content, document_base("https://specs.example.com/api/openapi.json"))

    assert content["servers"][0]["url"] == absolute


def test_a_network_path_reference_without_a_base_is_kept() -> None:
    content = _spec([{"url": "//api.example.com/v1"}])

    result = resolve_relative_servers(content, NO_BASE)

    assert content["servers"] == [{"url": "//api.example.com/v1"}]
    assert not result.changed


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("api.stripe.com/v1", ServerUrlProblem.MISSING_SCHEME),
        ("localhost:8080/v1", ServerUrlProblem.MISSING_SCHEME),
        ("api.example.com:8443/v1", ServerUrlProblem.MISSING_SCHEME),
        ("10.0.0.5:8080", ServerUrlProblem.MISSING_SCHEME),
        ("[::1]:8080/v1", ServerUrlProblem.MISSING_SCHEME),
        ("[2001:db8::1]/v1", ServerUrlProblem.MISSING_SCHEME),
        ("index.php/api", ServerUrlProblem.FILE_NAME),
        ("api.json/v1", ServerUrlProblem.FILE_NAME),
        ("//", ServerUrlProblem.EMPTY_HOST),
        ("///x", ServerUrlProblem.EMPTY_HOST),
        ("file:///etc/passwd", ServerUrlProblem.UNSUPPORTED_SCHEME),
        ("mailto:ops@example.com", ServerUrlProblem.UNSUPPORTED_SCHEME),
        ("?version=3", ServerUrlProblem.NO_PATH),
        ("#servers", ServerUrlProblem.NO_PATH),
    ],
)
@pytest.mark.parametrize("base", [PETSTORE, NO_BASE], ids=["with-base", "no-base"])
def test_unresolvable_references_are_refused_with_or_without_a_base(
    url: str, problem: ServerUrlProblem, base: DocumentBase
) -> None:
    error = _refusal([{"url": url}], base)

    assert error.problem is problem
    assert error.urls == [url]


def test_a_bare_host_hint_says_to_add_a_scheme() -> None:
    error = _refusal([{"url": "api.stripe.com/v1"}], PETSTORE)

    assert str(error) == "server URL looks like a host; add https://: 'api.stripe.com/v1'"


def test_a_file_name_segment_is_refused_without_calling_it_a_host() -> None:
    """``index.php/api`` is a path, but one that can't be told from a mistyped
    host, so it fails closed with guidance that fits a path."""
    error = _refusal([{"url": "index.php/api"}], PETSTORE)

    assert (
        str(error) == "server URL starts with a file name; use /path or https://: 'index.php/api'"
    )
    assert "looks like a host" not in str(error)


def test_empty_authority_references_are_never_resolved() -> None:
    """``//`` and ``///x`` would become ``https://`` and ``https:///x``: no host.
    Non-strict, they stay as declared and are reported, never resolved."""
    content = _spec([{"url": "//"}, {"url": "///x"}])

    result = resolve_relative_servers(content, PETSTORE, strict=False)

    assert content["servers"] == [{"url": "//"}, {"url": "///x"}]
    assert result.resolved == []
    assert result.unresolved == ["//", "///x"]


def test_an_empty_authority_beside_an_absolute_server_is_dropped() -> None:
    content = _spec([{"url": "https://api.example.com/v1"}, {"url": "//"}])

    result = resolve_relative_servers(content, PETSTORE)

    assert content["servers"] == [{"url": "https://api.example.com/v1"}]
    assert result.dropped == ["//"]
    assert result.resolved == []


def test_relative_servers_never_resolve_against_a_content_host() -> None:
    error = _refusal([{"url": "/v1"}], document_base(RAW_URL))

    assert error.problem is ServerUrlProblem.CONTENT_HOST
    assert str(error).startswith("spec host serves files; use an absolute server URL")


def test_relative_only_servers_without_a_base_are_refused_naming_the_urls() -> None:
    error = _refusal([{"url": "/api/v3"}, {"url": "/api/v4"}], NO_BASE)

    assert error.urls == ["/api/v3", "/api/v4"]
    assert str(error) == "relative server URL has no base; use an absolute URL: '/api/v3' (+1 more)"


@pytest.mark.parametrize("problem", list(ServerUrlProblem))
@pytest.mark.parametrize(
    "urls",
    [
        ["/api/v3"],
        ["/services/data/v58.0/sobjects/Account/describe/compactLayouts/primary"],
        ["/" + "x" * 300, "/b", "/c"],
    ],
)
def test_the_job_error_keeps_the_guidance(urls: list[str], problem: ServerUrlProblem) -> None:
    message = str(RelativeServerUrlError(urls, problem))
    guidance = message.split(": '", 1)[0]

    assert len(message) <= SERVER_URL_ERROR_MAX_CHARS
    job_error = (JOB_ERROR_PREFIX + message)[:JOB_ERROR_MAX]
    assert guidance in job_error
    if len(urls) > 1:
        assert job_error.endswith(f"(+{len(urls) - 1} more)")


def test_a_long_url_is_shortened_not_the_guidance() -> None:
    url = "/services/data/v58.0/sobjects/Account/describe/compactLayouts/primary"

    message = str(RelativeServerUrlError([url], ServerUrlProblem.NO_BASE))

    assert message.startswith("relative server URL has no base; use an absolute URL: '/services")
    assert message.endswith("…'")


def test_mixed_servers_without_a_base_keep_the_absolute_ones() -> None:
    content = _spec(
        [
            {"url": "https://petstore.example.com/v3"},
            {"url": "/api/v3"},
            {"url": "api.stripe.com/v1"},
        ]
    )

    result = resolve_relative_servers(content, NO_BASE)

    assert content["servers"] == [{"url": "https://petstore.example.com/v3"}]
    assert result.dropped == ["/api/v3", "api.stripe.com/v1"]


def test_mixed_servers_with_a_base_resolve_the_relative_ones() -> None:
    content = _spec([{"url": "https://petstore.example.com/v3"}, {"url": "/api/v3"}])

    resolve_relative_servers(content, PETSTORE)

    assert [s["url"] for s in content["servers"]] == [
        "https://petstore.example.com/v3",
        "https://petstore3.swagger.io/api/v3",
    ]


def test_path_and_operation_level_servers_are_resolved_or_refused() -> None:
    paths = {
        "/pet": {
            "servers": [{"url": "/path-level"}],
            "get": {
                "servers": [{"url": "/op-level"}],
                "responses": {"200": {"description": "ok"}},
            },
        }
    }
    content = _spec([{"url": "https://api.example.com"}], **paths)
    refused = copy.deepcopy(content)

    resolve_relative_servers(content, PETSTORE)

    assert content["paths"]["/pet"]["servers"] == [
        {"url": "https://petstore3.swagger.io/path-level"}
    ]
    assert content["paths"]["/pet"]["get"]["servers"] == [
        {"url": "https://petstore3.swagger.io/op-level"}
    ]
    with pytest.raises(RelativeServerUrlError) as excinfo:
        resolve_relative_servers(refused, NO_BASE)
    assert excinfo.value.urls == ["/path-level", "/op-level"]


def test_server_variables_survive_resolution() -> None:
    variables = {"version": {"default": "v3", "enum": ["v3", "v4"]}}
    content = _spec(
        [
            {"url": "/api/{version}", "variables": variables},
            {"url": "https://{region}.example.com", "variables": {"region": {"default": "eu"}}},
        ]
    )

    resolve_relative_servers(content, PETSTORE)

    assert content["servers"] == [
        {"url": "https://petstore3.swagger.io/api/{version}", "variables": variables},
        {"url": "https://{region}.example.com", "variables": {"region": {"default": "eu"}}},
    ]


def test_leading_variable_with_a_relative_default_names_the_variable() -> None:
    servers = [{"url": "{basePath}/v1", "variables": {"basePath": {"default": "/api"}}}]

    error = _refusal(servers, PETSTORE)

    assert error.problem is ServerUrlProblem.LEADING_VARIABLE
    assert "has no base" not in str(error)
    assert str(error).startswith("server URL starts with a relative variable")


def test_leading_variable_defaulting_to_a_bare_host_needs_a_scheme() -> None:
    servers = [{"url": "{host}/v1", "variables": {"host": {"default": "api.stripe.com"}}}]

    assert _refusal(servers, PETSTORE).problem is ServerUrlProblem.MISSING_SCHEME


def test_spec_without_servers_is_left_alone() -> None:
    content = _spec(None)
    before = copy.deepcopy(content)

    result = resolve_relative_servers(content, PETSTORE)

    assert content == before
    assert not result.changed


def test_non_strict_keeps_unresolvable_lists_as_declared() -> None:
    paths = {"/pet": {"get": {"servers": [{"url": "/op"}], "responses": {}}}}
    content = _spec([{"url": "/api/v3"}], **paths)
    before = copy.deepcopy(content)

    result = resolve_relative_servers(content, document_base(MIRROR_URL), strict=False)

    assert content == before
    assert result.unresolved == ["/api/v3", "/op"]
    assert result.resolved == []


def test_summary_caps_samples_and_keeps_counts() -> None:
    result = ServerResolution(
        resolved=[(f"/v{i}", f"https://h/v{i}") for i in range(25)],
        unresolved=["/x"],
    )

    summary = result.summary()

    assert summary.resolved == [f"https://h/v{i}" for i in range(10)]
    assert summary.resolved_count == 25
    assert summary.unresolved == ["/x"]
    assert summary.dropped_count == 0


def test_resolved_revision_digest_is_stable_and_distinct_from_the_raw_digest() -> None:
    resolved = [("/api/v3", "https://petstore3.swagger.io/api/v3")]

    digest = resolved_revision_digest("raw", resolved)

    assert digest == resolved_revision_digest("raw", list(resolved))
    assert digest != "raw"
    assert digest != resolved_revision_digest("raw", [("/api/v3", "https://other.example/api/v3")])


# -- the stage, per import path ------------------------------------------------


@pytest.mark.anyio
async def test_stage_resolves_a_url_import_and_reports_it() -> None:
    ctx = _ctx(
        _spec([{"url": "/api/v3"}]),
        source_type=ApiRevisionSourceType.URL,
        source_url=PETSTORE_URL,
    )

    await ResolveServerUrlsStage().run(ctx)

    assert ctx.specification.content is not None
    assert ctx.specification.content["servers"] == [{"url": "https://petstore3.swagger.io/api/v3"}]
    resolution = ctx.get("server_resolution")
    assert isinstance(resolution, ServerResolution)
    assert resolution.summary().resolved == ["https://petstore3.swagger.io/api/v3"]
    # The revision digest names the stored (resolved) content, not the raw bytes.
    assert ctx.specification.sha == resolved_revision_digest("raw-digest", resolution.resolved)


@pytest.mark.anyio
async def test_stage_resolves_against_the_post_redirect_url() -> None:
    ctx = _ctx(
        _spec([{"url": "/v3"}]),
        source_type=ApiRevisionSourceType.URL,
        source_url="http://petstore.example/openapi.json",
        retrieval_url="https://cdn.petstore.example/specs/openapi.json",
    )

    await ResolveServerUrlsStage().run(ctx)

    assert ctx.specification.content is not None
    assert ctx.specification.content["servers"] == [{"url": "https://cdn.petstore.example/v3"}]


@pytest.mark.anyio
async def test_stage_leaves_absolute_servers_and_the_digest_alone() -> None:
    ctx = _ctx(
        _spec([{"url": "https://api.example.com/v1"}]),
        source_type=ApiRevisionSourceType.URL,
        source_url=PETSTORE_URL,
    )

    await ResolveServerUrlsStage().run(ctx)

    assert ctx.specification.sha == "raw-digest"
    assert ctx.get("server_resolution") is None


@pytest.mark.anyio
async def test_stage_refuses_an_inline_import() -> None:
    ctx = _ctx(_spec([{"url": "/api/v3"}]), source_type=ApiRevisionSourceType.INLINE)

    with pytest.raises(RelativeServerUrlError, match="'/api/v3'"):
        await ResolveServerUrlsStage().run(ctx)


@pytest.mark.anyio
@pytest.mark.parametrize("source_url", [RAW_URL, MIRROR_URL])
async def test_stage_refuses_a_manual_url_import_from_a_content_host(source_url: str) -> None:
    """A manual import of a GitHub-hosted spec (the catalog mirror included)
    must not route the API, and its credentials, to the file host."""
    ctx = _ctx(
        _spec([{"url": "/v1"}]), source_type=ApiRevisionSourceType.URL, source_url=source_url
    )

    with pytest.raises(RelativeServerUrlError) as excinfo:
        await ResolveServerUrlsStage().run(ctx)
    assert excinfo.value.problem is ServerUrlProblem.CONTENT_HOST


@pytest.mark.anyio
async def test_stage_treats_a_configured_catalog_mirror_as_a_content_host() -> None:
    config = AppConfig.model_construct(
        catalog=CatalogConfig(
            sources=[
                CatalogSourceConfig(
                    slug="mirror",
                    kind="git-manifest",
                    settings={"url": "https://specs.mirror.example/apis.json"},
                )
            ]
        )
    )
    ctx = _ctx(
        _spec([{"url": "/v1"}]),
        config,
        source_type=ApiRevisionSourceType.URL,
        source_url="https://specs.mirror.example/acme/openapi.json",
    )

    with pytest.raises(RelativeServerUrlError) as excinfo:
        await ResolveServerUrlsStage().run(ctx)
    assert excinfo.value.problem is ServerUrlProblem.CONTENT_HOST


@pytest.mark.anyio
async def test_stage_refuses_a_bare_host_typo_on_a_manual_url_import() -> None:
    ctx = _ctx(
        _spec([{"url": "api.stripe.com/v1"}]),
        source_type=ApiRevisionSourceType.URL,
        source_url=RAW_URL,
    )

    with pytest.raises(RelativeServerUrlError) as excinfo:
        await ResolveServerUrlsStage().run(ctx)
    assert excinfo.value.problem is ServerUrlProblem.MISSING_SCHEME


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fields",
    [
        # Catalog import and Flow-3 update re-import.
        {"origin": "catalog", "catalog_api_id": "petstore3.swagger.io", "source_url": MIRROR_URL},
        # An overlay re-ingest of a catalog spec carries the catalog identity.
        {"origin": "overlay", "catalog_api_id": "petstore3.swagger.io", "source_url": MIRROR_URL},
        # An overlay over an inline import has no base at all.
        {"origin": "overlay", "source_url": None},
        # An overlay over a manual import of a content-host URL.
        {"origin": "overlay", "source_url": RAW_URL},
    ],
    ids=["catalog", "catalog-overlay", "inline-overlay", "content-host-overlay"],
)
async def test_stage_keeps_reingests_inspect_only(fields: dict[str, Any]) -> None:
    """Catalog, update, and overlay re-ingests of a spec whose servers can't be
    made absolute import inspect-only, with the servers as declared."""
    content = _spec([{"url": "/api/v3"}, {"url": "api.stripe.com/v1"}])
    ctx = _ctx(content, source_type=ApiRevisionSourceType.URL, **fields)

    await ResolveServerUrlsStage().run(ctx)

    assert content["servers"] == [{"url": "/api/v3"}, {"url": "api.stripe.com/v1"}]
    assert ctx.specification.sha == "raw-digest"
    resolution = ctx.get("server_resolution")
    assert isinstance(resolution, ServerResolution)
    assert resolution.unresolved == ["/api/v3", "api.stripe.com/v1"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fields",
    [
        {"source_url": PETSTORE_URL},
        # Even where the base import's redirects were known to end.
        {"source_url": PETSTORE_URL, "retrieval_url": "https://cdn.petstore.example/o.json"},
    ],
    ids=["source-url", "retrieval-url"],
)
async def test_stage_never_resolves_an_overlay_over_a_url_import(fields: dict[str, Any]) -> None:
    """An overlay over a legacy host-less URL import must not give the API a
    host: the confirmer never saw it, and it would skip the ``credentials:write``
    review a promote of the same change needs. The servers stay as declared."""
    content = _spec([{"url": "/api/v3"}])
    ctx = _ctx(content, source_type=ApiRevisionSourceType.INLINE, origin="overlay", **fields)

    await ResolveServerUrlsStage().run(ctx)

    assert content["servers"] == [{"url": "/api/v3"}]
    assert ctx.specification.sha == "raw-digest"
    resolution = ctx.get("server_resolution")
    assert isinstance(resolution, ServerResolution)
    assert resolution.resolved == []
    assert resolution.unresolved == ["/api/v3"]


@pytest.mark.anyio
async def test_swagger_20_without_host_is_refused_before_server_resolution() -> None:
    """Swagger 2.0 (where a missing ``host`` means "the document's host") is not
    an accepted spec format, so it can never yield host-less operations."""
    ctx = _ctx({"swagger": "2.0", "info": {"title": "Pets", "version": "1"}, "basePath": "/v2"})

    with pytest.raises(IngestStageError, match="openapi"):
        await ValidateOpenAPISpec().run(ctx)
