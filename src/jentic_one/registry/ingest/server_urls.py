"""Resolve relative OpenAPI server URLs against the spec's document URL.

An OpenAPI 3.x server URL may be relative (``/api/v3``): it is then relative to
the location the document is served from. The registry routes requests, scopes
credentials, and builds execute targets by server host, so a relative server
would leave every operation it covers without an upstream host.

Ingest rewrites each relative server to an absolute URL (RFC 3986 reference
resolution against the document's retrieval URL, after redirects) when that URL
is where the API plausibly lives. It never resolves against a file or static
content host (:data:`STATIC_CONTENT_HOSTS`, the catalog mirror): that host is
not the API's, and resolving would send the credentials bound to the API there.
A reference whose first segment is a host name (``api.stripe.com/v1``) is a
missing scheme, not a path, and is never joined onto the document path.

What happens to a server that cannot be made absolute depends on the caller
(:func:`resolve_relative_servers`, ``strict``). For a user import a list that is
left with no absolute server is refused (:class:`RelativeServerUrlError`); a
list that still has an absolute server drops the rest. For a catalog, overlay,
or update re-ingest the unresolvable servers stay as declared, so the API
imports inspect-only, exactly as a spec without ``servers`` does. Every level is
handled the same way: document, path item, and operation ``servers``.

Only declared servers are considered. A spec that declares no ``servers`` keeps
them absent: the OpenAPI default (``/``) would point the API at whatever host
serves the document.

Server variables are honoured: a URL is relative when it still has no host
after its variables take their ``default``, and the template is resolved with
its ``{name}`` placeholders intact. A template that starts with a variable
whose default is relative (``{basePath}/v1``) has no resolvable template form.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from jentic_one.registry.core.server_hosts import (
    expand_variable_defaults,
    is_hostless_server_url,
)
from jentic_one.registry.ingest.exc import RelativeServerUrlError, ServerUrlProblem
from jentic_one.registry.ingest.parsers.openapi import HTTP_METHODS
from jentic_one.registry.ingest.schemas import ServerUrlChanges

#: Hosts that serve files rather than APIs: a spec fetched from one of them is
#: never the API's own host, so relative servers never resolve against it.
STATIC_CONTENT_HOSTS: frozenset[str] = frozenset(
    {
        "raw.githubusercontent.com",
        "gist.githubusercontent.com",
        "objects.githubusercontent.com",
        "github.com",
        "gist.github.com",
        "gitlab.com",
        "bitbucket.org",
        "raw.githack.com",
        "cdn.jsdelivr.net",
        "unpkg.com",
        "app.swaggerhub.com",
        "api.swaggerhub.com",
        "storage.googleapis.com",
    }
)

#: Host suffixes of the same kind (object storage, static site hosting).
STATIC_CONTENT_HOST_SUFFIXES: tuple[str, ...] = (
    ".s3.amazonaws.com",
    ".blob.core.windows.net",
    ".github.io",
    ".gitlab.io",
)

#: A first path segment shaped like ``host[:port]``: a DNS name ending in an
#: alphabetic TLD, ``localhost``, an IPv4 address, or any name with a port.
_BARE_HOST = re.compile(
    r"^(?:"
    r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?::\d+)?"
    r"|localhost(?::\d+)?"
    r"|\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?"
    r"|[a-z0-9.-]+:\d+"
    r")$",
    re.IGNORECASE,
)

#: Entries kept in a log line or job result per category.
SAMPLE_SIZE = 10


@dataclass(frozen=True)
class DocumentBase:
    """Where relative servers resolve, or why they cannot.

    ``url`` is the sanitised document URL, set only when relative servers may
    resolve against it; ``problem`` says why not otherwise. ``scheme`` is the
    scheme a scheme-relative ``//host`` server inherits (RFC 3986 §5.2.2); it is
    set exactly when ``url`` is, so nothing resolves against a content host or
    a plaintext downgrade, and such a server is otherwise kept as declared.
    """

    url: str | None
    problem: ServerUrlProblem | None = None
    scheme: str | None = None


@dataclass
class ServerResolution:
    """What :func:`resolve_relative_servers` changed in the spec."""

    #: ``(declared, absolute)`` pairs, one per rewritten server entry.
    resolved: list[tuple[str, str]] = field(default_factory=list)
    #: Unresolvable URLs removed from a list that still has an absolute server.
    dropped: list[str] = field(default_factory=list)
    #: Unresolvable URLs kept as declared (non-strict): their operations are
    #: inspect-only.
    unresolved: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Whether anything was resolved, dropped, or left unresolved."""
        return bool(self.resolved or self.dropped or self.unresolved)

    def summary(self) -> ServerUrlChanges:
        """The job-result form: at most :data:`SAMPLE_SIZE` URLs per category, plus counts."""
        return ServerUrlChanges(
            resolved=[absolute for _, absolute in self.resolved[:SAMPLE_SIZE]],
            resolved_count=len(self.resolved),
            dropped=self.dropped[:SAMPLE_SIZE],
            dropped_count=len(self.dropped),
            unresolved=self.unresolved[:SAMPLE_SIZE],
            unresolved_count=len(self.unresolved),
        )


def url_host(url: str | None) -> str | None:
    """The host name of ``url``, or ``None`` when it has none or does not parse."""
    if not url:
        return None
    try:
        return urlsplit(url.strip()).hostname
    except ValueError:
        return None


def is_static_content_host(host: str | None, extra: Iterable[str] = ()) -> bool:
    """Whether ``host`` serves files (see :data:`STATIC_CONTENT_HOSTS`) or is in ``extra``."""
    if not host:
        return False
    host = host.lower().rstrip(".")
    if host in STATIC_CONTENT_HOSTS or host in {h.lower() for h in extra}:
        return True
    return host.endswith(STATIC_CONTENT_HOST_SUFFIXES)


def server_base_url(source_url: str | None) -> str | None:
    """The base relative server URLs resolve against, or ``None`` when unusable.

    The source URL minus userinfo, query, and fragment: a resolved server URL is
    stored and shown, so it must never carry credentials or a signed query from
    the URL the spec was fetched with.
    """
    if not source_url:
        return None
    try:
        parts = urlsplit(source_url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path or "/", "", ""))


def document_base(
    source_url: str | None,
    *,
    retrieval_url: str | None = None,
    content_hosts: Iterable[str] = (),
) -> DocumentBase:
    """The resolution base for a spec requested at ``source_url``.

    ``retrieval_url`` is where the redirect chain ended; relative references
    resolve against it (RFC 3986 §5.1.3). Neither URL may be a content host
    (built-in list plus ``content_hosts``), and an https request that ended on
    plaintext http is refused rather than resolved to http.
    """
    final = retrieval_url or source_url
    base = server_base_url(final)
    if base is None:
        return DocumentBase(None, ServerUrlProblem.NO_BASE)
    scheme = urlsplit(base).scheme
    hosts = list(content_hosts)
    if any(is_static_content_host(url_host(u), hosts) for u in (source_url, final)):
        return DocumentBase(None, ServerUrlProblem.CONTENT_HOST)
    requested = server_base_url(source_url)
    if requested is not None and urlsplit(requested).scheme == "https" and scheme == "http":
        return DocumentBase(None, ServerUrlProblem.INSECURE_REDIRECT)
    return DocumentBase(base, None, scheme)


def _classify(url: str, variables: Any, base: DocumentBase) -> str | ServerUrlProblem | None:
    """The absolute form of ``url``, why it has none, or ``None`` to keep it as is."""
    template = url.strip()
    if template.startswith("//"):
        return f"{base.scheme}:{template}" if base.scheme else None
    if not is_hostless_server_url(template, variables):
        return None
    expanded = expand_variable_defaults(template, variables).strip()
    if expanded.startswith(("?", "#")):
        return ServerUrlProblem.NO_PATH
    if _BARE_HOST.match(expanded.split("/", 1)[0]):
        return ServerUrlProblem.MISSING_SCHEME
    if template.startswith("{"):
        return ServerUrlProblem.LEADING_VARIABLE
    try:
        if urlsplit(expanded).scheme:
            return ServerUrlProblem.UNSUPPORTED_SCHEME
    except ValueError:
        return ServerUrlProblem.UNSUPPORTED_SCHEME
    if base.url is None:
        return base.problem or ServerUrlProblem.NO_BASE
    absolute = urljoin(base.url, template or "/")
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ServerUrlProblem.UNSUPPORTED_SCHEME
    return absolute


def _resolve_list(
    holder: dict[str, Any],
    base: DocumentBase,
    strict: bool,
    result: ServerResolution,
    refused: list[tuple[str, ServerUrlProblem]],
) -> None:
    servers = holder.get("servers")
    if not isinstance(servers, list) or not servers:
        return
    kept: list[Any] = []
    problems: list[tuple[str, ServerUrlProblem]] = []
    resolved: list[tuple[str, str]] = []
    absolute_left = False
    for server in servers:
        url = server.get("url") if isinstance(server, Mapping) else None
        outcome = _classify(url, server.get("variables"), base) if isinstance(url, str) else None
        if outcome is None:
            kept.append(server)
            absolute_left = absolute_left or isinstance(url, str)
        elif isinstance(outcome, ServerUrlProblem):
            assert isinstance(url, str)
            problems.append((url, outcome))
        else:
            assert isinstance(url, str)
            kept.append({**server, "url": outcome})
            resolved.append((url, outcome))
            absolute_left = True
    if problems and not absolute_left:
        if strict:
            refused.extend(problems)
            return
        # Keep the list as declared: rewriting part of it would leave its
        # operations half-routable.
        result.unresolved.extend(url for url, _ in problems)
        return
    if not problems and not resolved:
        return
    result.resolved.extend(resolved)
    result.dropped.extend(url for url, _ in problems)
    holder["servers"] = kept


def resolve_relative_servers(
    content: dict[str, Any], base: DocumentBase, *, strict: bool = True
) -> ServerResolution:
    """Rewrite every relative server in ``content`` to an absolute URL, in place.

    ``base`` comes from :func:`document_base`. With ``strict`` (a user import),
    raises :class:`RelativeServerUrlError` naming the offending URLs when any
    server list is left with no absolute server; ``content`` is then not to be
    ingested. Without it such lists stay as declared and are reported in
    :attr:`ServerResolution.unresolved`.
    """
    result = ServerResolution()
    refused: list[tuple[str, ServerUrlProblem]] = []
    _resolve_list(content, base, strict, result, refused)
    paths = content.get("paths")
    if isinstance(paths, Mapping):
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            _resolve_list(path_item, base, strict, result, refused)
            for method, operation in path_item.items():
                if method in HTTP_METHODS and isinstance(operation, dict):
                    _resolve_list(operation, base, strict, result, refused)
    if refused:
        urls = list(dict.fromkeys(url for url, _ in refused))
        raise RelativeServerUrlError(urls, refused[0][1])
    return result


def resolved_revision_digest(raw_digest: str, resolved: list[tuple[str, str]]) -> str:
    """The revision digest of a spec whose relative servers ingest resolved.

    A revision's digest identifies the content it stores. Resolved servers make
    that content differ from the fetched bytes, so the digest folds them in:
    re-importing the same document from the same URL yields the same digest
    (still a duplicate), while a revision stored from the same bytes with its
    servers host-less does not collide with it. Only resolution folds in: it
    happens solely against a document URL that is not a content host, and the
    catalog's update check compares raw upstream digests only for specs served
    from the catalog mirror (a content host), so those digests stay raw.
    """
    rewrite = json.dumps(resolved, separators=(",", ":"))
    return hashlib.sha256(f"{raw_digest}\nresolved-servers:{rewrite}".encode()).hexdigest()
