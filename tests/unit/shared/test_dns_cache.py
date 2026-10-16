"""Unit tests for off-loop DNS resolution and the shared raw-answer cache (#1700).

Covers the event-loop safety of ``validate_upstream_url_async`` and the pinning
transport, the cache's TTL / size / failure semantics, and the security
contract that a cached answer is re-validated against the consumer's own
egress policy on every use (the cache stores addresses, never verdicts).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
import time
from typing import Any

import httpx
import pytest

from jentic_one.shared import dns_cache
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.dns_cache import ResolutionCache, resolve_host
from jentic_one.shared.egress import DnsPinningTransport, resolve_and_validate_async
from jentic_one.shared.url_validation import validate_upstream_url_async


def _answers(*ips: str) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for ip in ips:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        out.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0)))
    return out


class _Resolver:
    """A ``getaddrinfo`` stand-in that records each call's host and thread."""

    def __init__(self, *ips: str, delay_s: float = 0.0) -> None:
        self.ips = ips
        self.delay_s = delay_s
        self.hosts: list[str] = []
        self.threads: list[int] = []

    def __call__(self, host: str, *_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        self.hosts.append(host)
        self.threads.append(threading.get_ident())
        if self.delay_s:
            time.sleep(self.delay_s)
        return _answers(*self.ips)


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(204)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Swap the process-wide cache for one driven by a manual clock."""
    fake = _Clock()
    monkeypatch.setattr(dns_cache, "_CACHE", ResolutionCache(ttl_s=5.0, clock=fake))
    return fake


# --- event-loop safety ---------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_async_resolves_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _Resolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    url = await validate_upstream_url_async("https://api.example.com/v1")

    assert url == "https://api.example.com/v1"
    assert resolver.hosts == ["api.example.com"]
    assert resolver.threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_slow_resolver_does_not_stall_concurrent_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 300 ms resolver must not freeze the loop: other tasks keep running and
    independent validations overlap instead of serialising."""
    resolver = _Resolver("93.184.216.34", delay_s=0.3)
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    ticks = 0
    stop = asyncio.Event()

    async def _heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    started = time.monotonic()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(_heartbeat())
        validations = [
            tg.create_task(validate_upstream_url_async(f"https://h{i}.example.com/"))
            for i in range(4)
        ]
        await asyncio.gather(*validations)
        stop.set()
    elapsed = time.monotonic() - started

    assert all(task.result() for task in validations)
    assert len(resolver.hosts) == 4
    # Serialised on the loop this would take >= 1.2 s with zero heartbeats.
    assert elapsed < 0.9
    assert ticks >= 10


@pytest.mark.asyncio
async def test_validate_async_ip_literal_never_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_dns(*_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        raise AssertionError("an IP literal must not be resolved")

    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)
    assert await validate_upstream_url_async("http://93.184.216.34/x") == "http://93.184.216.34/x"
    with pytest.raises(ValueError, match="blocked address range"):
        await validate_upstream_url_async("http://10.0.0.1/x")


# --- async pre-flight has the sync policy ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ips", "egress"),
    [
        (("10.0.0.5",), None),
        (("169.254.169.254",), EgressConfig(allowed_private_subnets=["169.254.0.0/16"])),
        (("93.184.216.34", "10.0.0.5"), None),
        (("::ffff:127.0.0.1",), None),
        # Subnet allowlisted but the name lacks an allowed internal-domain suffix.
        (("10.1.2.3",), EgressConfig(allowed_private_subnets=["10.0.0.0/8"])),
    ],
)
async def test_validate_async_blocks_like_sync(
    monkeypatch: pytest.MonkeyPatch, ips: tuple[str, ...], egress: EgressConfig | None
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver(*ips))
    with pytest.raises(ValueError, match="blocked address range"):
        await validate_upstream_url_async("https://evil.example.com/", egress)


@pytest.mark.asyncio
async def test_validate_async_allows_allowlisted_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver("10.1.2.3"))
    egress = EgressConfig(
        allowed_private_subnets=["10.0.0.0/8"], allowed_internal_domains=[".svc.cluster.local"]
    )
    url = await validate_upstream_url_async("http://api.svc.cluster.local/x", egress)
    assert url == "http://api.svc.cluster.local/x"


@pytest.mark.asyncio
async def test_validate_async_rejects_blocked_hostname_without_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _Resolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    with pytest.raises(ValueError, match="blocked hostname"):
        await validate_upstream_url_async("http://metadata.google.internal/computeMetadata/v1/")
    with pytest.raises(ValueError, match="no hostname"):
        await validate_upstream_url_async("http:///path")
    assert resolver.hosts == []


@pytest.mark.asyncio
async def test_validate_async_lets_unresolvable_name_through_uncached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matches the sync pre-flight: NXDOMAIN passes here (the pinning transport
    fails closed), and the failure is not cached."""
    calls = 0

    def _nxdomain(*_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        nonlocal calls
        calls += 1
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", _nxdomain)
    url = "https://ghost.example.com/"
    assert await validate_upstream_url_async(url) == url
    assert await validate_upstream_url_async(url) == url
    assert calls == 2
    with pytest.raises(ValueError, match="did not resolve"):
        await resolve_and_validate_async("ghost.example.com", None)


# --- shared cache: one lookup per call, re-validated on every use --------------


@pytest.mark.asyncio
async def test_preflight_and_transport_share_one_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = _Resolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    inner = _RecordingTransport()

    url = await validate_upstream_url_async("https://api.example.com/x")
    async with httpx.AsyncClient(transport=DnsPinningTransport(inner, None)) as client:
        await client.get(url)
        await client.get(url)

    assert resolver.hosts == ["api.example.com"]
    assert [r.url.host for r in inner.seen] == ["93.184.216.34", "93.184.216.34"]
    assert inner.seen[0].extensions["sni_hostname"] == "api.example.com"


@pytest.mark.asyncio
async def test_cached_answer_is_revalidated_against_the_consumers_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answer cached under a permissive policy must not open a stricter one."""
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver("10.1.2.3"))
    permissive = EgressConfig(
        allowed_private_subnets=["10.0.0.0/8"], allowed_internal_domains=[".svc.cluster.local"]
    )
    await validate_upstream_url_async("http://api.svc.cluster.local/x", permissive)

    def _no_dns(*_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        raise AssertionError("expected a cache hit")

    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)
    with pytest.raises(ValueError, match="blocked address range"):
        await validate_upstream_url_async("http://api.svc.cluster.local/x", None)
    inner = _RecordingTransport()
    async with httpx.AsyncClient(transport=DnsPinningTransport(inner, None)) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get("http://api.svc.cluster.local/x")
    assert inner.seen == []


@pytest.mark.asyncio
async def test_rebind_after_ttl_is_caught_at_connect_time(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    """Once the cached public answer expires, a rebind to a private IP is
    resolved fresh and blocked before the pin."""
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver("93.184.216.34"))
    await validate_upstream_url_async("https://evil.example.com/")

    clock.now += 5.0
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver("169.254.169.254"))
    inner = _RecordingTransport()
    async with httpx.AsyncClient(transport=DnsPinningTransport(inner, None)) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get("https://evil.example.com/")
    assert inner.seen == []


@pytest.mark.asyncio
async def test_transport_pins_only_to_the_cached_validated_ip(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    """Within the TTL a resolver flip can't reach the connection: the pin is the
    answer that was validated, not a second (hostile) lookup."""
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver("93.184.216.34"))
    await validate_upstream_url_async("https://evil.example.com/")

    clock.now += 4.9
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver("10.0.0.5"))
    inner = _RecordingTransport()
    async with httpx.AsyncClient(transport=DnsPinningTransport(inner, None)) as client:
        await client.get("https://evil.example.com/")
    assert inner.seen[0].url.host == "93.184.216.34"


# --- ResolutionCache unit behaviour ---------------------------------------------


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(text)


def test_cache_expires_entries_after_ttl() -> None:
    clock = _Clock()
    cache = ResolutionCache(ttl_s=5.0, clock=clock)
    cache.put("API.example.com", (_ip("93.184.216.34"),))
    assert cache.get("api.example.com") == (_ip("93.184.216.34"),)
    clock.now += 5.0
    assert cache.get("api.example.com") is None
    assert len(cache) == 0


def test_cache_is_size_bounded_oldest_first() -> None:
    cache = ResolutionCache(max_entries=2, clock=_Clock())
    for host in ("a.example", "b.example", "c.example"):
        cache.put(host, (_ip("93.184.216.34"),))
    assert len(cache) == 2
    assert cache.get("a.example") is None
    assert cache.get("c.example") is not None


def test_cache_ignores_empty_answers_and_keeps_trailing_dot_distinct() -> None:
    cache = ResolutionCache(clock=_Clock())
    cache.put("empty.example", ())
    assert cache.get("empty.example") is None
    cache.put("host.example.", (_ip("93.184.216.34"),))
    assert cache.get("host.example") is None


def test_cache_disabled_with_zero_ttl() -> None:
    cache = ResolutionCache(ttl_s=0, clock=_Clock())
    cache.put("api.example.com", (_ip("93.184.216.34"),))
    assert cache.get("api.example.com") is None


@pytest.mark.asyncio
async def test_resolve_host_caches_hit_without_a_thread_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _Resolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    loop = asyncio.get_running_loop()
    executor_calls = 0
    real_run_in_executor = loop.run_in_executor

    def _counting_run_in_executor(executor: Any, func: Any, *args: Any) -> Any:
        nonlocal executor_calls
        executor_calls += 1
        return real_run_in_executor(executor, func, *args)

    monkeypatch.setattr(loop, "run_in_executor", _counting_run_in_executor)
    first = await resolve_host("api.example.com")
    second = await resolve_host("api.example.com")
    assert first == second == (_ip("93.184.216.34"),)
    assert executor_calls == 1
    assert len(resolver.hosts) == 1


# --- single-flight on a miss -----------------------------------------------------


class _GatedResolver:
    """A ``getaddrinfo`` stand-in that blocks until released, then answers or fails."""

    def __init__(self, *ips: str, error: Exception | None = None) -> None:
        self.ips = ips
        self.error = error
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, host: str, *_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=5), "resolver was never released"
        if self.error is not None:
            raise self.error
        return _answers(*self.ips)


async def _wait_until_entered(resolver: _GatedResolver) -> None:
    assert await asyncio.to_thread(resolver.entered.wait, 5)


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_blocking_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N callers missing on the same host (any case) cost one resolver run."""
    resolver = _GatedResolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    async with asyncio.TaskGroup() as tg:
        tasks = [
            tg.create_task(resolve_host("API.example.com" if i % 2 else "api.example.com"))
            for i in range(20)
        ]
        await _wait_until_entered(resolver)
        await asyncio.sleep(0)
        resolver.release.set()

    assert resolver.calls == 1
    assert {task.result() for task in tasks} == {(_ip("93.184.216.34"),)}
    # The shared answer is cached: the next call is a hit.
    assert await resolve_host("api.example.com") == (_ip("93.184.216.34"),)
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_shared_lookup_failure_reaches_every_waiter_and_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _GatedResolver(error=socket.gaierror("nope"))
    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    tasks = [asyncio.create_task(resolve_host("ghost.example.com")) for _ in range(5)]
    await _wait_until_entered(resolver)
    resolver.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert resolver.calls == 1
    assert all(isinstance(r, socket.gaierror) for r in results)
    assert dns_cache._IN_FLIGHT.get(asyncio.get_running_loop()) == {}

    # Nothing poisoned: the next caller runs a fresh lookup.
    retry = _Resolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", retry)
    assert await resolve_host("ghost.example.com") == (_ip("93.184.216.34"),)
    assert retry.hosts == ["ghost.example.com"]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_the_shared_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _GatedResolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    first = asyncio.create_task(resolve_host("api.example.com"))
    await _wait_until_entered(resolver)
    others = [asyncio.create_task(resolve_host("api.example.com")) for _ in range(3)]
    await asyncio.sleep(0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    others[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await others[0]

    resolver.release.set()
    assert await asyncio.gather(*others[1:]) == [(_ip("93.184.216.34"),)] * 2
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_lookup_outliving_every_waiter_still_caches_its_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _GatedResolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    only = asyncio.create_task(resolve_host("api.example.com"))
    await _wait_until_entered(resolver)
    only.cancel()
    with pytest.raises(asyncio.CancelledError):
        await only

    pending = dns_cache._IN_FLIGHT[asyncio.get_running_loop()]["api.example.com"]
    resolver.release.set()
    await asyncio.wait([pending])
    await asyncio.sleep(0)
    assert await resolve_host("api.example.com") == (_ip("93.184.216.34"),)
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_clear_detaches_in_flight_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lookup started before a clear serves its own waiters but neither the
    callers after the clear nor the cache."""
    stale = _GatedResolver("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", stale)
    before = asyncio.create_task(resolve_host("api.example.com"))
    await _wait_until_entered(stale)

    dns_cache.clear_resolution_cache()
    fresh = _Resolver("203.0.113.7")
    monkeypatch.setattr(socket, "getaddrinfo", fresh)
    assert await resolve_host("api.example.com") == (_ip("203.0.113.7"),)

    stale.release.set()
    assert await before == (_ip("93.184.216.34"),)
    await asyncio.sleep(0)
    assert await resolve_host("api.example.com") == (_ip("203.0.113.7"),)
    assert fresh.hosts == ["api.example.com"]
