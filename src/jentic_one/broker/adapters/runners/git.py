"""The governed git pipeline's upstream transport: streaming smart-HTTP to github.com.

git's request and response bodies (a whole pack) stream through here in both
directions: nothing is buffered, and the execute pipeline's body caps,
retries and circuit breaker do not apply (a push is not safe to resend).
The upstream is fixed, ``https://github.com`` for git and
``https://api.github.com`` for the compare API the force-push check calls;
only tests construct a :class:`GitUpstream` with other bases. Redirects are
never followed, so the injected credential is only ever sent to the fixed
upstream.

Transport failures map to the broker taxonomy: a timeout to
:class:`~jentic_one.broker.core.exceptions.UpstreamTimeoutError`, any other
transport error to
:class:`~jentic_one.broker.core.exceptions.UpstreamTransportError`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Literal, cast
from urllib.parse import quote

import httpx

from jentic_one.broker.core.exceptions import UpstreamTimeoutError, UpstreamTransportError

#: Where git smart-HTTP is sent.
GITHUB_GIT_BASE = "https://github.com"
#: Where the compare API the force-push check calls is.
GITHUB_API_BASE = "https://api.github.com"

CompareStatus = Literal["ahead", "behind", "identical", "diverged"]
_COMPARE_STATUSES: frozenset[str] = frozenset({"ahead", "behind", "identical", "diverged"})


@dataclass(frozen=True, slots=True)
class GitTimeouts:
    """Per-request timeouts: connecting, and the longest wait for the next byte."""

    connect_s: float
    idle_s: float

    def httpx(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_s, read=self.idle_s, write=self.idle_s, pool=self.connect_s
        )


@dataclass(frozen=True, slots=True)
class CompareResult:
    """What GitHub's compare API said about ``base...head``.

    ``status`` is GitHub's comparison status, or ``None`` when the call did
    not answer one (``http_status`` then tells why: 404 for a commit GitHub
    does not have, anything else for an error).
    """

    http_status: int
    status: CompareStatus | None = None


class GitUpstreamResponse:
    """An upstream response whose body is still streaming; close it when done."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        """The response headers, names lowercased."""
        return {key.lower(): value for key, value in self._response.headers.items()}

    async def iter_raw(self) -> AsyncIterator[bytes]:
        """The body exactly as the upstream sent it (any content encoding kept)."""
        try:
            async for chunk in self._response.aiter_raw():
                yield chunk
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(
                "github.com stopped sending the git response.", type="upstream_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamTransportError(
                "The connection to github.com broke during the git response.",
                type="upstream_transport_error",
            ) from exc

    async def iter_decoded(self) -> AsyncIterator[bytes]:
        """The body with any content encoding removed."""
        try:
            async for chunk in self._response.aiter_bytes():
                yield chunk
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(
                "github.com stopped sending the git response.", type="upstream_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamTransportError(
                "The connection to github.com broke during the git response.",
                type="upstream_transport_error",
            ) from exc

    async def aclose(self) -> None:
        await self._response.aclose()

    async def __aenter__(self) -> GitUpstreamResponse:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


class GitUpstream:
    """Sends git smart-HTTP and compare calls to github.com over the shared client."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        git_base: str = GITHUB_GIT_BASE,
        api_base: str = GITHUB_API_BASE,
    ) -> None:
        self._client = client
        self._git_base = git_base.rstrip("/")
        self._api_base = api_base.rstrip("/")

    async def send(
        self,
        method: str,
        path: str,
        *,
        query: str,
        headers: Mapping[str, str],
        body: AsyncIterable[bytes] | None,
        timeouts: GitTimeouts,
    ) -> GitUpstreamResponse:
        """Send one git request; the response body is left to stream.

        ``path`` is the upstream path (``/<owner>/<repo>.git/<endpoint>``), built
        by the caller from validated names only.
        """
        url = f"{self._git_base}{path}"
        if query:
            url = f"{url}?{query}"
        request = self._client.build_request(
            method, url, headers=dict(headers), content=body, timeout=timeouts.httpx()
        )
        try:
            response = await self._client.send(request, stream=True, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(
                "github.com did not answer the git request in time.", type="upstream_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamTransportError(
                "Jentic could not reach github.com for the git request.",
                type="upstream_transport_error",
            ) from exc
        return GitUpstreamResponse(response)

    async def compare(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
        *,
        authorization: str,
        timeout_s: float,
        max_bytes: int,
    ) -> CompareResult:
        """``GET /repos/{owner}/{repo}/compare/{base}...{head}`` and its ``status``.

        A response body larger than ``max_bytes`` answers no status, as does
        any failure to reach the API: the caller treats an unknown answer as
        "not shown to be a fast-forward".
        """
        url = (
            f"{self._api_base}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
            f"/compare/{base}...{head}?per_page=1"
        )
        headers = {
            "Authorization": authorization,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        request = self._client.build_request(
            "GET", url, headers=headers, timeout=httpx.Timeout(timeout_s)
        )
        try:
            response = await self._client.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError:
            return CompareResult(http_status=0)
        try:
            if response.status_code != 200:
                return CompareResult(http_status=response.status_code)
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    return CompareResult(http_status=response.status_code)
        except httpx.HTTPError:
            return CompareResult(http_status=0)
        finally:
            await response.aclose()
        try:
            status = json.loads(bytes(body)).get("status")
        except (ValueError, AttributeError):
            return CompareResult(http_status=200)
        if status not in _COMPARE_STATUSES:
            return CompareResult(http_status=200)
        return CompareResult(http_status=200, status=cast(CompareStatus, status))


__all__ = [
    "GITHUB_API_BASE",
    "GITHUB_GIT_BASE",
    "CompareResult",
    "CompareStatus",
    "GitTimeouts",
    "GitUpstream",
    "GitUpstreamResponse",
]
