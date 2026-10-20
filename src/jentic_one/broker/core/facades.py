"""Vendor CLI facades: addressing, header policy and vendor error bodies.

A facade is a broker endpoint that speaks one vendor's API shape, so the
vendor's own CLI (``gh``, ``cf``, ``aws``) can be pointed at it and
authenticated with a ``cit_`` invocation token. Each call is mapped to the
vendor's real upstream URL and run through the same execute pipeline as the
forward proxy (registry match, rules, credential injection, execution record).

Path-mode facades live under :data:`FACADE_PATH_PREFIX`. Its first segment
starts with an underscore, which no DNS hostname can contain, so the reserved
prefix never shadows a URL the forward-proxy catch-all could otherwise serve
(the catch-all reads the first path segment as the upstream host).

Host-mode facades (GitHub) answer on a hostname of their own, in the layout a
GitHub Enterprise Server uses (``/api/v3``, ``/api/graphql``,
``/api/uploads``), because ``gh`` only talks to a non-github.com host that way.

The proxy route (:data:`PROXY_FACADE_PREFIX`) is vendor-neutral: it takes a
full upstream URL, as the forward proxy does, for an egress proxy that relays
a vendor CLI's calls with the command's invocation token. The URL must resolve
to an API of the token's vendor (:func:`api_vendor_matches`).

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote, urlsplit

from jentic_one.broker.core.proxy_headers import reconstruct_upstream_url
from jentic_one.shared.cli_facades import FACADE_PATH_PREFIX, facade_path_prefix
from jentic_one.shared.models.api_identity import slugify_api_field


@dataclass(frozen=True, slots=True)
class PathFacade:
    """A facade path prefix that forwards to ``upstream_base``.

    ``<prefix>/<rest>?<query>`` (e.g. ``/_facade/cloudflare/zones``) maps to
    ``<upstream_base>/<rest>?<query>`` with ``rest`` and ``query`` kept exactly
    as the client encoded them.
    """

    vendor: str
    prefix: str
    upstream_base: str


#: Cloudflare (``cf``): ``CLOUDFLARE_API_BASE_URL=<broker>/_facade/cloudflare``.
CLOUDFLARE_FACADE = PathFacade(
    vendor="cloudflare",
    prefix=facade_path_prefix("cloudflare"),
    upstream_base="https://api.cloudflare.com/client/v4",
)


def facade_upstream_url(scope: Mapping[str, Any], facade: PathFacade) -> str | None:
    """The upstream URL for a facade request, built from the raw ASGI path.

    Like the forward proxy, this reads ``scope["raw_path"]`` and
    ``scope["query_string"]``, never the decoded path parameter, so an
    encoded ``%2F`` inside a segment reaches the upstream as ``%2F``.

    Returns ``None`` (the caller answers 404) when the raw path does not start
    with the literal prefix (e.g. the prefix itself was percent-encoded, so the
    route matched only after decoding) or when the remainder has a ``.`` or
    ``..`` segment, literal or encoded, which could step outside the vendor's
    API base path.
    """
    raw_path = _raw_path(scope)
    lead = f"{facade.prefix}/"
    if not raw_path.startswith(lead):
        return None
    remainder = raw_path[len(lead) :]
    if _has_dot_segment(remainder):
        return None
    return _with_query(f"{facade.upstream_base}/{remainder}", scope)


def _raw_path(scope: Mapping[str, Any]) -> str:
    raw_path_bytes = scope.get("raw_path") or scope.get("path", "").encode("latin-1")
    return str(raw_path_bytes.decode("latin-1"))


def _with_query(url: str, scope: Mapping[str, Any]) -> str:
    query_string = scope.get("query_string", b"").decode("latin-1")
    return f"{url}?{query_string}" if query_string else url


def _has_dot_segment(path: str) -> bool:
    """Whether ``path`` has a ``.`` or ``..`` segment, literal or percent-encoded."""
    return any(unquote(segment) in (".", "..") for segment in path.split("/"))


# ── Proxy route (full upstream URL) ──────────────────────────────────────────

#: The vendor-neutral proxy route: ``/_facade/proxy/<full upstream URL>``.
PROXY_FACADE_PREFIX = facade_path_prefix("proxy")

#: The vendor domains each invocation-token vendor covers at the proxy route,
#: compared with an API's vendor in the registry's slug form (``github.com``
#: is stored as ``github-com``). A token vendor missing here reaches no API
#: through it.
PROXY_API_VENDORS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "github": frozenset({"github.com"}),
        "cloudflare": frozenset({"cloudflare.com"}),
        "aws": frozenset({"amazonaws.com"}),
    }
)


def proxy_upstream_url(scope: Mapping[str, Any]) -> str | None:
    """The upstream URL of a proxy-route request, built from the raw ASGI path.

    ``/_facade/proxy/<url>?<query>`` maps to ``<url>?<query>`` exactly as the
    forward proxy maps ``/<url>?<query>`` (:func:`reconstruct_upstream_url`):
    the raw path and query are kept byte for byte, a collapsed ``https:/``
    gets its second slash back and a URL without a scheme is ``https``.

    Returns ``None`` (the caller answers 404) when the raw path does not start
    with the literal prefix, when nothing follows it, or when the URL has a
    ``.`` or ``..`` segment, literal or encoded.
    """
    raw_path = _raw_path(scope)
    lead = f"{PROXY_FACADE_PREFIX}/"
    if not raw_path.startswith(lead):
        return None
    remainder = raw_path[len(lead) :]
    if not remainder.strip("/") or _has_dot_segment(remainder):
        return None
    return reconstruct_upstream_url(
        {
            "raw_path": remainder.encode("latin-1"),
            "query_string": scope.get("query_string", b""),
        }
    )


def api_vendor_matches(token_vendor: str, api_vendor: str) -> bool:
    """Whether an API of registry vendor ``api_vendor`` belongs to ``token_vendor``.

    The registry names a vendor by the slug of its domain (``github-com``); an
    invocation token names it by its CLI facade id (``github``). Both sides
    are compared in slug form (:func:`slugify_api_field`), exactly: a
    subdomain, lookalike or the facade id itself is another vendor. An
    unknown token vendor never matches.
    """
    allowed = PROXY_API_VENDORS.get(token_vendor, frozenset())
    slug = slugify_api_field(api_vendor)
    return bool(slug) and slug in {slugify_api_field(domain) for domain in allowed}


# ── GitHub (host mode) ───────────────────────────────────────────────────────

#: The GitHub REST API: ``https://<facade host>/api/v3/<rest>``.
GITHUB_REST_FACADE = PathFacade(
    vendor="github", prefix="/api/v3", upstream_base="https://api.github.com"
)

#: Release asset uploads: ``https://<facade host>/api/uploads/<rest>``.
GITHUB_UPLOADS_FACADE = PathFacade(
    vendor="github", prefix="/api/uploads", upstream_base="https://uploads.github.com"
)

#: The GitHub GraphQL endpoint, served at exactly :data:`GITHUB_GRAPHQL_PATH`.
GITHUB_GRAPHQL_PATH = "/api/graphql"
GITHUB_GRAPHQL_UPSTREAM = "https://api.github.com/graphql"

#: ``GET /api/v3/meta``, which ``gh`` reads to detect the Enterprise Server
#: version. The facade answers it itself (see :data:`GITHUB_META_BODY`).
GITHUB_META_PATH = "/api/v3/meta"

#: The ``meta`` body the facade answers. ``gh`` reads only ``installed_version``
#: from it, to decide which Enterprise Server-only fallbacks to use; a version
#: above every Enterprise Server release makes it take the github.com code
#: paths, which match the API the facade forwards to.
GITHUB_META_BODY: Mapping[str, Any] = {
    "installed_version": "3.99.0",
    "verifiable_password_authentication": False,
}


def github_upstream_url(scope: Mapping[str, Any]) -> str | None:
    """The github.com URL for a request on the GitHub facade host, or ``None``.

    ``/api/v3/<rest>`` maps to ``https://api.github.com/<rest>``,
    ``/api/graphql`` to ``https://api.github.com/graphql`` and
    ``/api/uploads/<rest>`` to ``https://uploads.github.com/<rest>``, with the
    query kept. Built from the raw path with the same rules as
    :func:`facade_upstream_url`; any other path is ``None`` (the caller
    answers 404).
    """
    raw_path = _raw_path(scope)
    if raw_path == GITHUB_GRAPHQL_PATH:
        return _with_query(GITHUB_GRAPHQL_UPSTREAM, scope)
    for facade in (GITHUB_REST_FACADE, GITHUB_UPLOADS_FACADE):
        if raw_path.startswith(f"{facade.prefix}/"):
            return facade_upstream_url(scope, facade)
    return None


def request_host(host_header: str | None) -> str | None:
    """The hostname a request was sent to: lowercased, port and trailing dot removed.

    Read from the ``Host`` header only. The broker trusts no forwarded-host
    header (``X-Forwarded-Host``, ``Forwarded``), so a client cannot claim a
    facade host it did not connect to by name.
    """
    if not host_header:
        return None
    host = host_header.strip().lower()
    name, separator, port = host.rpartition(":")
    if separator and port.isdigit() and not name.endswith(":"):
        host = name
    host = host.removesuffix(".")
    return host or None


def is_facade_host(host_header: str | None, facade_host: str | None) -> bool:
    """Whether a request's ``Host`` header names ``facade_host`` (exact, case-insensitive)."""
    if not facade_host:
        return False
    return request_host(host_header) == facade_host.lower()


_GITHUB_ORIGINS: tuple[tuple[str, str], ...] = (
    ("api.github.com", GITHUB_REST_FACADE.prefix),
    ("uploads.github.com", GITHUB_UPLOADS_FACADE.prefix),
)


def rewrite_github_url(url: str, facade_base: str) -> str:
    """Point a github.com API URL at the facade; leave every other URL unchanged.

    Only ``https://api.github.com/<rest>`` (to ``<facade_base>/api/v3/<rest>``)
    and ``https://uploads.github.com/<rest>`` (to
    ``<facade_base>/api/uploads/<rest>``) are rewritten, with path, query and
    fragment kept. Anything else (another origin such as a presigned
    blob-storage redirect, a relative or malformed URL, user info or a
    non-default port) is returned as given.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return url
    if parts.scheme != "https" or parts.username is not None or port not in (None, 443):
        return url
    hostname = parts.hostname
    for origin, prefix in _GITHUB_ORIGINS:
        if hostname == origin:
            rewritten = f"{facade_base.rstrip('/')}{prefix}{parts.path}"
            if parts.query:
                rewritten = f"{rewritten}?{parts.query}"
            if parts.fragment:
                rewritten = f"{rewritten}#{parts.fragment}"
            return rewritten
    return url


# One RFC 8288 link-value target: ``<URI-Reference>``.
_LINK_TARGET_RE = re.compile(r"<([^<>]*)>")


_REPO_PATH_RE = re.compile(r"^/repos/([^/]+)/([^/]+)(/.*)?$")
_REPOSITORY_ID_PATH_RE = re.compile(r"^/repositories/[0-9]+(/.*)?$")


def _repo_named(url: str, upstream_url: str | None) -> str:
    """A ``/repositories/<id>/<rest>`` link target, in the call's ``/repos/<owner>/<repo>`` form.

    GitHub's pagination links for a repository list name the repository by id
    even when the call named it ``/repos/<owner>/<repo>``. The id form is a
    path no rule or registered operation is written against, so when the
    link's ``<rest>`` is the same endpoint the call was made to, the target
    takes the call's own ``/repos/<owner>/<repo>`` prefix (GitHub serves both
    the same). Anything else is returned unchanged.
    """
    if upstream_url is None:
        return url
    try:
        link = urlsplit(url)
        call = urlsplit(upstream_url)
    except ValueError:
        return url
    if link.hostname != "api.github.com" or call.hostname != "api.github.com":
        return url
    link_match = _REPOSITORY_ID_PATH_RE.match(link.path)
    call_match = _REPO_PATH_RE.match(call.path)
    if link_match is None or call_match is None or link_match.group(1) != call_match.group(3):
        return url
    owner, repo, rest = call_match.group(1), call_match.group(2), call_match.group(3) or ""
    return link._replace(path=f"/repos/{owner}/{repo}{rest}").geturl()


def rewrite_github_link_header(
    value: str, facade_base: str, upstream_url: str | None = None
) -> str:
    """Rewrite every github.com target in a ``Link`` header (see :func:`rewrite_github_url`).

    Only the ``<…>`` targets change; link parameters (``rel="next"``) and any
    target on another origin are kept byte for byte. A value with no
    ``<…>`` target is returned unchanged. With ``upstream_url`` (the call's
    github.com URL), a target naming the called repository by id is first put
    back in the call's ``/repos/<owner>/<repo>`` form (:func:`_repo_named`).
    """
    return _LINK_TARGET_RE.sub(
        lambda match: (
            f"<{rewrite_github_url(_repo_named(match.group(1), upstream_url), facade_base)}>"
        ),
        value,
    )


# A ``cit_`` invocation token as minted (``cit_`` + 43 URL-safe base64 chars),
# not preceded by a word character so ``explicit_…`` never matches.
_INVOCATION_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])cit_[A-Za-z0-9_-]{32,}")

# Inbound trace context: a facade call runs on the trace its token was minted
# with, so a client-supplied context must not reach the upstream either.
_INBOUND_TRACE_HEADERS: frozenset[str] = frozenset({"traceparent", "tracestate", "baggage"})


def facade_request_headers(inbound: Mapping[str, str]) -> dict[str, str]:
    """Inbound headers a facade may forward, before the proxy's own filtering.

    Drops every header whose value carries an invocation token (``Authorization``
    is already a broker-consumed header; this also covers a CLI or plugin that
    repeats the token elsewhere) and the inbound trace context headers. The
    result still goes through ``forward_headers``.
    """
    return {
        key: value
        for key, value in inbound.items()
        if key.lower() not in _INBOUND_TRACE_HEADERS and not _INVOCATION_TOKEN_RE.search(value)
    }


#: Upstream response headers a facade passes on to the CLI. The facade answers
#: on the broker's own origin, so anything that could set state there
#: (``Set-Cookie``), redirect the CLI straight to the vendor (``Location``) or
#: advertise vendor auth (``WWW-Authenticate``) is dropped.
FACADE_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "cache-control",
        "content-disposition",
        "content-encoding",
        "content-language",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
        "retry-after",
        "vary",
        "ratelimit",
        "ratelimit-policy",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "cf-ray",
        "idempotent-replayed",
        "preference-applied",
        "tracestate",
    }
)


def is_facade_response_header(name: str) -> bool:
    """Whether a facade passes response header ``name`` on (the allowlist or ``Jentic-*``)."""
    key = name.lower()
    return key in FACADE_RESPONSE_HEADERS or key.startswith("jentic-")


#: Response headers the GitHub facade passes on beyond :data:`FACADE_RESPONSE_HEADERS`.
#: ``Link`` and ``Location`` are rewritten first (:func:`rewrite_github_url`).
#: ``X-Accepted-OAuth-Scopes`` and ``X-Accepted-GitHub-Permissions`` describe
#: the endpoint. ``X-OAuth-Scopes`` is not passed on: it lists what the stored
#: credential could do beyond the agent's rules, and ``gh`` only uses it to
#: suggest ``gh auth refresh``, which does not apply to a Jentic-held credential.
GITHUB_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "link",
        "location",
        "deprecation",
        "sunset",
        "x-accepted-oauth-scopes",
        "x-accepted-github-permissions",
        "x-poll-interval",
        "x-ratelimit-used",
        "x-ratelimit-resource",
    }
)


def is_github_response_header(name: str) -> bool:
    """Whether the GitHub facade passes response header ``name`` on.

    The common facade allowlist, :data:`GITHUB_RESPONSE_HEADERS` and every
    ``X-GitHub-*`` header (request id, media type, API version, SSO).
    """
    key = name.lower()
    return (
        is_facade_response_header(key)
        or key in GITHUB_RESPONSE_HEADERS
        or key.startswith("x-github-")
    )


#: The instruction a facade's held-call error carries: the CLI has already
#: failed, and the broker runs the call itself once a reviewer approves it.
FACADE_HELD_INSTRUCTION = (
    "Show the user the review link: they cannot approve the call without it. Do not "
    "re-run the command. Once approved, the call runs exactly once on its own and its "
    "outcome is at the result link; steps the command would have taken after this call "
    "did not happen."
)


def facade_error_message(problem: Mapping[str, Any]) -> str:
    """One line for a CLI: the problem's ``detail`` plus its agent directive, if any."""
    detail = problem.get("detail")
    if not isinstance(detail, str) or not detail:
        title = problem.get("title")
        detail = title if isinstance(title, str) and title else "The request failed."
    message = f"Jentic: {detail}"
    directive = problem.get("agent_directive")
    instruction = (
        directive.get("human_readable_instruction") if isinstance(directive, Mapping) else None
    )
    if isinstance(instruction, str) and instruction:
        message = f"{message} {instruction}"
    return message


def cloudflare_error_body(status: int, problem: Mapping[str, Any]) -> dict[str, Any]:
    """A Cloudflare API v4 error envelope for a broker problem.

    ``code`` is the HTTP status: Cloudflare's own error codes are not reused,
    so a Jentic refusal is never mistaken for one from Cloudflare.
    """
    return {
        "success": False,
        "errors": [{"code": status, "message": facade_error_message(problem)}],
        "messages": [],
        "result": None,
    }


def github_error_body(status: int, problem: Mapping[str, Any]) -> dict[str, Any]:
    """A GitHub REST error body for a broker problem (``message``, ``documentation_url``).

    ``gh`` prints ``message``, so it carries the problem's detail and agent
    directive (:func:`facade_error_message`). ``documentation_url`` is the
    review link of a held call and is left out when the problem has none.
    ``status`` is the HTTP status as a string, as GitHub sends it.
    """
    body: dict[str, Any] = {"message": facade_error_message(problem)}
    review_url = problem.get("review_url")
    if isinstance(review_url, str) and review_url:
        body["documentation_url"] = review_url
    body["status"] = str(status)
    return body


__all__ = [
    "CLOUDFLARE_FACADE",
    "FACADE_HELD_INSTRUCTION",
    "FACADE_PATH_PREFIX",
    "FACADE_RESPONSE_HEADERS",
    "GITHUB_GRAPHQL_PATH",
    "GITHUB_GRAPHQL_UPSTREAM",
    "GITHUB_META_BODY",
    "GITHUB_META_PATH",
    "GITHUB_RESPONSE_HEADERS",
    "GITHUB_REST_FACADE",
    "GITHUB_UPLOADS_FACADE",
    "PROXY_API_VENDORS",
    "PROXY_FACADE_PREFIX",
    "PathFacade",
    "api_vendor_matches",
    "cloudflare_error_body",
    "facade_error_message",
    "facade_request_headers",
    "facade_upstream_url",
    "github_error_body",
    "github_upstream_url",
    "is_facade_host",
    "is_facade_response_header",
    "is_github_response_header",
    "proxy_upstream_url",
    "request_host",
    "rewrite_github_link_header",
    "rewrite_github_url",
]
